"""Reviewed acquisition lineage, separate from byte-level import verification."""
from typing import Literal
from datetime import datetime
from pydantic import AwareDatetime, Field, model_validator, field_validator
from .models import Strict
from .scenes import Identifier
from .learning import Sha
from .ingestion import Split


def _timestamp(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('采集和审查时间必须为带时区 ISO8601 时间') from None
    return value


class SourceReview(Strict):
    status: Literal['pending', 'confirmed'] = 'pending'
    reviewer: Identifier | None = None
    reviewed_at: AwareDatetime | None = None
    method: str | None = Field(default=None, min_length=5, max_length=1000)
    notes: str | None = Field(default=None, min_length=5, max_length=2000)

    _reviewed = field_validator('reviewed_at', mode='before')(_timestamp)

    @model_validator(mode='after')
    def evidence(self):
        if self.status == 'confirmed' and any(value is None for value in
                (self.reviewer, self.reviewed_at, self.method, self.notes)):
            raise ValueError('已确认来源审查必须记录审查人、时间、方法及证据说明')
        return self


class DatasetSource(Strict):
    source_sha256: Sha
    root_source_sha256: Sha
    parent_source_sha256: Sha | None = None
    relation: Literal['original', 'clip', 'transcode', 'other_derived']
    capture_session_id: Identifier | None = None
    camera_id: Identifier | None = None
    view_id: Identifier | None = None
    captured_at: AwareDatetime | None = None
    split: Split = 'unassigned'
    provenance_review: SourceReview = Field(default_factory=SourceReview)
    near_duplicate_review: SourceReview = Field(default_factory=SourceReview)

    _captured = field_validator('captured_at', mode='before')(_timestamp)

    @model_validator(mode='after')
    def identity(self):
        if self.relation == 'original':
            if self.source_sha256 != self.root_source_sha256 or self.parent_source_sha256 is not None:
                raise ValueError('原始来源必须以自身为根且没有父来源')
        elif self.parent_source_sha256 is None or self.source_sha256 == self.root_source_sha256:
            raise ValueError('派生来源必须记录父来源和不同的原始根来源')
        if self.provenance_review.status == 'confirmed' and any(value is None for value in
                (self.capture_session_id, self.camera_id, self.view_id, self.captured_at)):
            raise ValueError('已核验来源必须记录采集会话、相机、视角和带时区采集时间')
        return self


class DuplicateReview(Strict):
    left_sha256: Sha
    right_sha256: Sha
    resolution: Literal['pending', 'same_session', 'independent'] = 'pending'
    reviewer: Identifier | None = None
    reviewed_at: AwareDatetime | None = None
    notes: str | None = Field(default=None, min_length=5, max_length=2000)

    _reviewed = field_validator('reviewed_at', mode='before')(_timestamp)

    @model_validator(mode='after')
    def evidence(self):
        if self.left_sha256 == self.right_sha256:
            raise ValueError('近重复审查需要两个不同文件；相同字节已有 hash 分组')
        if self.resolution != 'pending' and any(value is None for value in
                (self.reviewer, self.reviewed_at, self.notes)):
            raise ValueError('近重复审查结论需要审查人、时间和证据说明')
        return self


class DatasetManifest(Strict):
    manifest_version: Literal['p031-sources-v1'] = 'p031-sources-v1'
    dataset_id: Identifier
    tenant_id: Identifier
    sources: list[DatasetSource] = Field(min_length=1, max_length=1000)
    duplicate_reviews: list[DuplicateReview] = Field(default_factory=list, max_length=10000)

    @model_validator(mode='after')
    def lineage_and_splits(self):
        by_hash = {source.source_sha256: source for source in self.sources}
        if len(by_hash) != len(self.sources):
            raise ValueError('数据集包含重复来源 hash')
        session_splits = {}
        for source in self.sources:
            root = by_hash.get(source.root_source_sha256)
            if root is None or root.relation != 'original':
                raise ValueError('派生来源的原始根必须登记在同一清单中')
            if source.split != root.split:
                raise ValueError('同一原始来源及所有派生文件必须固定到同一分区')
            if source.capture_session_id is not None:
                previous = session_splits.setdefault(source.capture_session_id, source.split)
                if previous != source.split:
                    raise ValueError('同一采集会话不能跨 train/calibration/test 或 unassigned 分区')
            seen = {source.source_sha256}
            node = source
            while node.parent_source_sha256 is not None:
                parent = by_hash.get(node.parent_source_sha256)
                if parent is None or parent.source_sha256 in seen:
                    raise ValueError('来源谱系父节点缺失或存在循环')
                if parent.root_source_sha256 != source.root_source_sha256:
                    raise ValueError('来源谱系的原始根不一致')
                if any(getattr(node, key) != getattr(parent, key) for key in
                       ('capture_session_id', 'camera_id', 'view_id', 'captured_at')):
                    raise ValueError('派生文件必须继承原始采集会话、相机、视角和采集时间')
                seen.add(parent.source_sha256)
                node = parent
            if node.source_sha256 != source.root_source_sha256:
                raise ValueError('来源谱系未终止于已登记原始根')
        pairs = set()
        for review in self.duplicate_reviews:
            pair = tuple(sorted((review.left_sha256, review.right_sha256)))
            if pair in pairs or any(key not in by_hash for key in pair):
                raise ValueError('近重复审查记录重复或引用未登记来源')
            pairs.add(pair)
            left, right = (by_hash[key] for key in pair)
            same_root = left.root_source_sha256 == right.root_source_sha256
            same_session = left.capture_session_id is not None and left.capture_session_id == right.capture_session_id
            if review.resolution == 'same_session' and (not same_session or left.split != right.split):
                raise ValueError('同会话审查结论必须落实到相同会话和分区')
            if review.resolution == 'independent' and (same_root or same_session):
                raise ValueError('同源或同会话不能被审查记录声明为独立')
        return self
