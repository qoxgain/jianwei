"""P0.2+ executable contracts only; no decoding, training or prediction engine."""
import hashlib
import rfc8785
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .models import Strict
from .scenes import Identifier

Sha = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
ContextValue = str | int | float | bool | None

class Scope(Strict):
    tenant_id: Identifier
    scene_id: Identifier

class Episode(Scope):
    contract_version: Literal['3.1']
    episode_id: Identifier
    source_id: Identifier
    source_video_id: Identifier
    source_hash: Sha
    entity_id: Identifier | None
    pts_start_ms: int = Field(ge=0)
    pts_end_ms: int = Field(gt=0)
    context: dict[Identifier, ContextValue]

    @model_validator(mode='after')
    def interval(self):
        if self.pts_end_ms <= self.pts_start_ms: raise ValueError('空或倒序区间')
        return self

class FeatureSpace(Strict):
    contract_version: Literal['3.1']
    encoder_sha: Sha
    preprocessing_sha: Sha
    sampling_sha: Sha
    roi_policy_hash: Sha
    pooling: str = Field(min_length=1)
    dimension: int = Field(ge=1)

    def identifier(self) -> str:
        return hashlib.sha256(rfc8785.dumps(self.model_dump())).hexdigest()

class FeatureBatch(Episode):
    feature_space: FeatureSpace
    feature_space_id: Sha
    quality: float = Field(ge=0, le=1)
    vector_uri: str = Field(min_length=1)
    data_origin: Literal['real_video', 'synthetic_features']
    split: Literal['unassigned', 'train', 'calibration', 'test']

    @model_validator(mode='after')
    def space(self):
        if self.feature_space_id != self.feature_space.identifier(): raise ValueError('特征空间hash不匹配')
        return self

class LabelProvenance(Strict):
    tier: Literal['unlabeled', 'teacher_candidate', 'weak_verified', 'human_verified']
    producer_version: str = Field(min_length=1)
    evidence_ids: list[Identifier]

class ActivityObservation(Episode):
    observation_id: Identifier
    kind: Literal['visual_observation']
    action_id: str | None
    observed_status: Literal['UNKNOWN', 'REVIEW_REQUIRED', 'VERIFIED']
    available_at_pts_ms: int = Field(ge=0)
    provenance: LabelProvenance
    knowledge_version: str

    @model_validator(mode='after')
    def evidence(self):
        if self.available_at_pts_ms < self.pts_end_ms: raise ValueError('观测尚不可用')
        if self.observed_status == 'UNKNOWN' and self.action_id is not None: raise ValueError('UNKNOWN不能绑定确定动作')
        if self.observed_status == 'VERIFIED':
            if not self.action_id or self.provenance.tier not in {'weak_verified', 'human_verified'} or not self.provenance.evidence_ids:
                raise ValueError('已核验观测需要动作及核验证据')
        return self

class StateSnapshot(Episode):
    snapshot_id: Identifier
    kind: Literal['observed_state']
    state_id: Identifier | None
    anonymous_cluster_id: Identifier | None
    cluster_version: str | None
    provenance: LabelProvenance

    @model_validator(mode='after')
    def cluster(self):
        if (self.anonymous_cluster_id is None) != (self.cluster_version is None):
            raise ValueError('匿名簇必须绑定聚类版本')
        return self

class EventOccurrence(Episode):
    occurrence_id: Identifier
    kind: Literal['event_occurrence']
    event_id: Identifier | None
    status: Literal['UNKNOWN', 'REVIEW_REQUIRED', 'VERIFIED']
    provenance: LabelProvenance

    @model_validator(mode='after')
    def evidence(self):
        if self.status == 'VERIFIED' and (not self.event_id or self.provenance.tier not in {'weak_verified', 'human_verified'} or not self.provenance.evidence_ids):
            raise ValueError('已核验事件需要独立核验证据')
        return self

class Prediction(Scope):
    contract_version: Literal['3.1']
    prediction_id: Identifier
    episode_id: Identifier
    source_video_id: Identifier
    feature_space_id: Sha
    kind: Literal['empirical_forecast']
    prediction_kind: Literal['next_event', 'future_state']
    issued_at_pts_ms: int = Field(ge=0)
    input_max_pts_ms: int = Field(ge=0)
    target_start_pts_ms: int = Field(ge=0)
    target_end_pts_ms: int = Field(ge=0)
    distribution: dict[str, float]
    predictor_version: str
    knowledge_version: str | None
    memory_version: str | None

    @model_validator(mode='after')
    def causal(self):
        if not self.input_max_pts_ms <= self.issued_at_pts_ms < self.target_start_pts_ms < self.target_end_pts_ms:
            raise ValueError('预测必须早于非空未来目标窗口')
        p = list(self.distribution.values())
        if not p or any(not 0 <= v <= 1 for v in p) or abs(sum(p)-1) > 1e-6: raise ValueError('非法概率分布')
        return self

class NormativeExpectation(Scope):
    contract_version: Literal['3.1']
    kind: Literal['normative_expected']
    episode_id: Identifier
    workflow_id: Identifier
    workflow_version: str
    knowledge_hash: Sha
    allowed_node_ids: list[Identifier]

class MemorySnapshot(Scope):
    contract_version: Literal['3.1']
    snapshot_id: Identifier
    parent_id: Identifier | None
    feature_space_id: Sha
    knowledge_hash: Sha
    state: Literal['candidate', 'active', 'revoked']
    sample_manifest_hash: Sha
    evaluation_report_id: Identifier | None

    @model_validator(mode='after')
    def activation(self):
        if self.state == 'active' and not self.evaluation_report_id: raise ValueError('active需要评估记录')
        return self

def assert_compatible(left: FeatureBatch, right: FeatureBatch):
    if (left.tenant_id, left.scene_id, left.feature_space_id) != (right.tenant_id, right.scene_id, right.feature_space_id):
        raise ValueError('跨作用域或特征空间不兼容')

def assert_future_pair(history: FeatureBatch, target: FeatureBatch, issued_at_pts_ms: int):
    assert_compatible(history, target)
    if (history.source_video_id, history.source_hash, history.source_id, history.episode_id, history.entity_id, history.split) != (
        target.source_video_id, target.source_hash, target.source_id, target.episode_id, target.entity_id, target.split):
        raise ValueError('禁止跨视频、实体、周期或split配对')
    if history.split != 'train': raise ValueError('自监督训练只能读取train')
    if not history.pts_end_ms <= issued_at_pts_ms < target.pts_start_ms: raise ValueError('目标窗口不在未来')
