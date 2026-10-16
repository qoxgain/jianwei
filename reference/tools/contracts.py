"""Strict configuration/knowledge contracts for the V3 specification toolkit."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)

class Source(Strict):
    id: str
    type: Literal['synthetic','file','rtsp']
    uri: str
    scene_id: str
    roi_xyxy: list[float] = Field(min_length=4,max_length=4)

class Profile(Strict):
    mode: Literal['demo','replay','live']
    tenant_id: str
    data_root: str
    model_root: str
    api_host: str
    api_port: int = Field(ge=1,le=65535)
    public_base_url: str
    database_url: str
    redis_url: str
    teacher_enabled: bool
    teacher_base_url: str
    teacher_api_key_ref: str
    device: str
    encoder_weights: str
    auth_enabled: bool
    jwt_secret_ref: str
    sources: list[Source] = Field(min_length=1)

class Paths(Strict):
    inbox: str
    clips: str
    features: str
    datasets: str
    memory: str
    reports: str
    logs: str
    demo_output: str

class Video(Strict):
    clip_seconds: float = Field(gt=0)
    stride_seconds: float = Field(gt=0)
    frames_per_clip: int = Field(ge=2)
    quality_min: float = Field(ge=0,le=1)
    missing_frame_fraction_max: float = Field(ge=0,le=1)
    reconnect_seconds: int = Field(ge=1)
    queue_size: int = Field(ge=1)
    queue_policy: Literal['drop_oldest']
    pre_event_seconds: float = Field(ge=0)
    post_event_seconds: float = Field(ge=0)
    allowed_lateness_seconds: float = Field(ge=0)

class Encoder(Strict):
    adapter: Literal['existing_features','videomae','vjepa2']
    frozen: Literal[True]
    feature_dim: int = Field(ge=1)
    weights_sha256: str | None
    preprocessing_version: str
    pooling: str
    checkpoint_revision: str

class Teacher(Strict):
    model: str
    frozen: Literal[True]
    timeout_seconds: int = Field(gt=0)
    max_concurrency: int = Field(ge=1)
    max_queue: int = Field(ge=1)
    max_frames: int = Field(ge=1)
    max_calls_per_camera_hour: int = Field(ge=0)
    include_forecast_in_prompt: Literal[False]
    output_schema: str

class Region(Strict):
    enabled: bool
    original_resolution: Literal[True]
    proposal_methods: list[Literal['configured_roi','detector','motion','tiling']]
    tile_size_px: int = Field(ge=32)
    tile_overlap: float = Field(ge=0,lt=1)
    expand_ratio: float = Field(ge=0,le=1)
    max_regions: int = Field(ge=1)
    min_original_short_side_px: int = Field(ge=1)

class Learning(Strict):
    enabled: bool
    auto_train: bool
    auto_evaluate: bool
    freeze_foundation_models: Literal[True]
    min_new_episodes: int = Field(ge=1)
    cooldown_hours: float = Field(gt=0)
    history_windows: int = Field(ge=1)
    forecast_horizons_seconds: list[float] = Field(min_length=1)
    temporal_adapter: Literal['ridge','causal_tcn','gru']
    hidden_dim: int = Field(ge=1)
    epochs: int = Field(ge=1)
    batch_size: int = Field(ge=1)
    learning_rate: float = Field(gt=0)
    ridge_l2: float = Field(gt=0)
    dirichlet_alpha: float = Field(gt=0)
    pseudo_label_fraction_max: float = Field(ge=0,le=1)
    replay_old_fraction: float = Field(ge=0,le=1)
    min_independent_cycles_per_action: int = Field(ge=1)
    min_audited_events_per_action: int = Field(ge=1)
    pseudo_precision_wilson_lower_min: float = Field(ge=0,le=1)
    empirical_prior_weight_max: float = Field(ge=0,le=0.5)
    allow_cross_tenant: Literal[False]
    train_split: Literal['train']
    train_adapters: list[Literal['temporal_head','tsm','yolo_detector']]
    require_verified_boxes_for_detector: Literal[True]

class Evaluation(Strict):
    minimum_gold_events_per_class: int = Field(ge=1)
    seeds: list[int] = Field(min_length=1)
    bootstrap_iterations: int = Field(ge=100)
    confidence_level: float = Field(gt=0.5,lt=1)
    required_macro_f1_gain: float = Field(ge=0,le=1)
    max_old_macro_f1_drop: float = Field(ge=0,le=1)
    max_old_class_f1_drop: float = Field(ge=0,le=1)
    gain_ci_lower_min: float
    max_false_alarm_ratio: float = Field(ge=1)
    max_coverage_drop: float = Field(ge=0,le=1)
    online_p95_ms_budget: int = Field(ge=1)
    protocol_version: str

class Release(Strict):
    memory_policy: Literal['gated_auto_known_class','manual']
    student_policy: Literal['shadow_then_gated_canary','manual']
    new_class_requires_confirmation: Literal[True]
    critical_requires_confirmation: Literal[True]
    shadow_min_episodes: int = Field(ge=1)
    shadow_min_hours: float = Field(gt=0)
    canary_percentages: list[int] = Field(min_length=1)
    switch_at_episode_boundary: Literal[True]
    preserve_previous_versions: int = Field(ge=2)

class Resources(Strict):
    max_training_jobs: int = Field(ge=1)
    training_window_local: str
    max_train_minutes: int = Field(ge=1)
    gpu_memory_fraction_max: float = Field(gt=0,le=1)
    pause_train_on_inference_queue: int = Field(ge=1)
    random_seed: int

class Media(Strict):
    engine: Literal['mediamtx']
    proxy_rtsp_base: str
    webrtc_public_base: str
    hls_public_base: str

class Session(Strict):
    require_style_and_camera: Literal[True]
    exclusive_camera: Literal[True]
    lease_ttl_seconds: int = Field(ge=1)
    lease_heartbeat_seconds: int = Field(ge=1)
    max_session_minutes: int = Field(ge=1)
    score_base: int
    verified_missing_penalty: int = Field(ge=0)
    missing_without_evidence: Literal['REVIEW_REQUIRED']
    separate_rework: Literal[True]

class Retention(Strict):
    raw_video_days: int = Field(ge=1)
    evidence_days: int = Field(ge=1)
    feature_days: int = Field(ge=1)
    audit_days: int = Field(ge=1)

class UI(Strict):
    title: str
    theme: Literal['dark','light']
    video_ratio: float = Field(ge=0.3,le=0.8)
    timezone: str
    demo_banner: Literal[True]

class Demo(Strict):
    seed: int
    train_episodes: int = Field(ge=5)
    test_episodes: int = Field(ge=5)
    feature_dim: int = Field(ge=6)
    noise_std: float = Field(gt=0)
    ridge_l2: float = Field(gt=0)
    skip_branch_probability: float = Field(ge=0,le=1)

class SystemConfig(Strict):
    config_version: Literal['3.0']
    active_profile: str
    active_scene: str
    knowledge_file: str
    profiles: dict[str,Profile]
    paths: Paths
    video: Video
    encoder: Encoder
    teacher: Teacher
    region: Region
    learning: Learning
    evaluation: Evaluation
    release: Release
    resources: Resources
    media: dict[str,Media]
    session: Session
    retention: Retention
    ui: UI
    demo: Demo

    @model_validator(mode='after')
    def consistency(self):
        if self.active_profile not in self.profiles:
            raise ValueError('active_profile不存在')
        if set(self.profiles)!=set(self.media):
            raise ValueError('每个profile必须有对应media配置')
        if any(h<=0 for h in self.learning.forecast_horizons_seconds):
            raise ValueError('未来窗口起点必须大于0')
        if self.session.lease_heartbeat_seconds>=self.session.lease_ttl_seconds:
            raise ValueError('lease heartbeat必须小于TTL')
        ps=self.release.canary_percentages
        if ps!=sorted(set(ps)) or ps[-1]!=100 or ps[0]<1:
            raise ValueError('灰度比例递增且以100结束')
        for name,p in self.profiles.items():
            ids=[s.id for s in p.sources]
            if len(ids)!=len(set(ids)): raise ValueError('重复camera id')
            for s in p.sources:
                x1,y1,x2,y2=s.roi_xyxy
                if not (0<=x1<x2<=1 and 0<=y1<y2<=1): raise ValueError('ROI坐标无效')
                if s.scene_id!=self.active_scene: raise ValueError('示例只支持当前scene的相机')
                if p.mode!='demo' and s.type=='synthetic': raise ValueError('非demo禁止synthetic')
            if p.mode=='live' and not p.auth_enabled: raise ValueError('live必须开启鉴权')
        return self

class CompletionRule(Strict):
    name: Literal['visible_motion_over_interval','manual_review_only']
    min_visible_seconds: float = Field(ge=0)
    require_object_contact: bool

class Action(Strict):
    id: str
    name: str
    aliases: list[str]
    description: str
    status: Literal['draft','approved']
    risk_level: Literal['general','critical']
    observable_cues: list[str]
    negative_cues: list[str]
    confusable_with: list[str]
    completion_rule: CompletionRule
    duration_prior_seconds: list[float] | None
    positive_examples: list[str]
    negative_examples: list[str]
    prototype_refs: list[str]

class SOPNode(Strict):
    action_id: str
    required: bool
    min_count: int = Field(ge=0)
    max_count: int = Field(ge=1)
    role: str
    group: str

class Edge(Strict):
    from_action: str
    to_action: str

class SOP(Strict):
    id: str
    version: str
    ordering: Literal['strict','partial','unordered']
    nodes: list[SOPNode]
    edges: list[Edge]
    repeatable_edges: list[Edge]

class PatchOperation(Strict):
    op: Literal['reject_sample','relabel_sample','map_cluster','add_negative_pair']
    target_id: str
    action_id: str | None
    related_action_id: str | None
    reason: str = Field(min_length=1)

class Knowledge(Strict):
    schema_version: Literal['3.0']
    knowledge_version: str
    scene_id: str
    approval_status: Literal['draft','approved']
    approved_by: str | None
    actions: list[Action]
    sop: SOP
    curated_memory: list[PatchOperation]

    @model_validator(mode='after')
    def references(self):
        ids=[a.id for a in self.actions]; known=set(ids)
        if len(ids)!=len(known): raise ValueError('重复action_id')
        nodes=[n.action_id for n in self.sop.nodes]
        if len(nodes)!=len(set(nodes)) or not set(nodes)<=known: raise ValueError('SOP节点无效')
        for a in self.actions:
            if not set(a.confusable_with)<=known: raise ValueError('混淆类引用不存在')
            if a.duration_prior_seconds is not None:
                if len(a.duration_prior_seconds)!=2 or not (0<=a.duration_prior_seconds[0]<=a.duration_prior_seconds[1]):
                    raise ValueError('duration范围无效')
        graph={n:[] for n in nodes}
        for edge in self.sop.edges+self.sop.repeatable_edges:
            if edge.from_action not in graph or edge.to_action not in graph: raise ValueError('edge引用不存在')
        for edge in self.sop.edges: graph[edge.from_action].append(edge.to_action)
        active=set();done=set()
        def visit(n):
            if n in active: raise ValueError('未声明为repeatable的SOP环路')
            if n in done: return
            active.add(n)
            for j in graph[n]: visit(j)
            active.remove(n);done.add(n)
        for n in graph: visit(n)
        for n in self.sop.nodes:
            if n.min_count>n.max_count: raise ValueError('min_count大于max_count')
        if self.sop.ordering=='unordered' and self.sop.edges: raise ValueError('无序流程不得包含顺序edges')
        if self.approval_status=='approved' and not self.approved_by: raise ValueError('正式规范缺少批准人')
        for op in self.curated_memory:
            for ref in [op.action_id,op.related_action_id]:
                if ref is not None and ref not in known: raise ValueError('人工记忆引用未知action')
        return self

class MemoryPatch(Strict):
    schema_version: Literal['3.0']
    base_memory_version: str
    base_knowledge_version: str
    actor: str
    reason: str
    operations: list[PatchOperation]

class TeacherResult(Strict):
    schema_version: Literal['3.0']
    request_id: str
    observed_action_id: str | None
    time_range_ms: list[int] = Field(min_length=2,max_length=2)
    evidence_frame_ids: list[str]
    visible_cues: list[str]
    uncertainty: str
    support_level: Literal['insufficient','partial','strong']
    alternative_action_ids: list[str]

    @model_validator(mode='after')
    def valid_evidence(self):
        a,b=self.time_range_ms
        if a<0 or b<=a: raise ValueError('invalid time range')
        if self.observed_action_id is not None and not self.evidence_frame_ids:
            raise ValueError('action needs evidence')
        if self.support_level=='insufficient' and self.observed_action_id is not None:
            raise ValueError('insufficient evidence must be unknown')
        return self

class Forecast(Strict):
    schema_version: Literal['3.0']
    prediction_id: str
    episode_id: str
    issued_at_pts_ms: int = Field(ge=0)
    input_max_pts_ms: int = Field(ge=0)
    target_start_pts_ms: int = Field(ge=0)
    target_end_pts_ms: int = Field(ge=0)
    distribution: dict[str,float]
    prediction_kind: Literal['next_event','future_state']
    model_version: str
    memory_version: str
    knowledge_version: str
    data_origin: Literal['model','synthetic_features']

    @model_validator(mode='after')
    def causal(self):
        if not (self.input_max_pts_ms<=self.issued_at_pts_ms<=self.target_start_pts_ms<self.target_end_pts_ms):
            raise ValueError('noncausal prediction')
        values=list(self.distribution.values())
        if not values or any(not 0<=v<=1 for v in values) or abs(sum(values)-1)>1e-6:
            raise ValueError('invalid distribution')
        return self
