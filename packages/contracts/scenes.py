"""Versioned, industry-neutral knowledge contracts. No inference is implemented."""
from typing import Literal, Annotated
from pydantic import Field, model_validator
from .models import Strict, Action, PatchOperation

Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')]
TaskMode = Literal['activity_recognition', 'event_monitoring', 'procedure_compliance']

class EpisodePolicy(Strict):
    mode: Literal['manual_cycle', 'external_cycle', 'fixed_window', 'manual_or_external_cycle', 'manual_or_fixed_window']
    window_seconds: float | None
    observation_contract: Literal['p033-observation-v1'] | None = None
    partial_window: Literal['exclude', 'extract_only'] = 'exclude'

    @model_validator(mode='after')
    def windows(self):
        if self.observation_contract and self.mode not in {'fixed_window', 'manual_or_fixed_window'}:
            raise ValueError('观察窗口契约仅适用于固定观察策略')
        if self.partial_window == 'extract_only' and self.observation_contract is None:
            raise ValueError('尾部提特征需要显式观察窗口版本')
        if self.mode in {'fixed_window', 'manual_or_fixed_window'}:
            if self.window_seconds is None or self.window_seconds <= 0:
                raise ValueError('固定窗口需要正数 window_seconds')
        elif self.window_seconds is not None:
            raise ValueError('非固定窗口策略不接受 window_seconds')
        return self

class SessionPolicy(Strict):
    required_context_keys: list[Identifier]
    exclusive_resource_key: Identifier | None
    lease_ttl_seconds: int = Field(ge=1)
    lease_heartbeat_seconds: int = Field(ge=1)
    max_session_minutes: int = Field(ge=1)

    @model_validator(mode='after')
    def lease(self):
        if self.lease_heartbeat_seconds >= self.lease_ttl_seconds:
            raise ValueError('heartbeat必须小于TTL')
        if len(set(self.required_context_keys)) != len(self.required_context_keys):
            raise ValueError('重复context键')
        if self.exclusive_resource_key and self.exclusive_resource_key not in self.required_context_keys:
            raise ValueError('独占资源必须是必填上下文')
        return self

class ContextField(Strict):
    type: Literal['string', 'integer', 'number', 'boolean']
    description: str

class GarmentExtension(Strict):
    type: Literal['garment_inspection']
    score_base: int
    verified_missing_penalty: int = Field(ge=0)
    missing_without_evidence: Literal['REVIEW_REQUIRED']
    separate_rework: bool

class SceneSpec(Strict):
    display_name: str = Field(min_length=1)
    task_modes: list[TaskMode] = Field(min_length=1)
    learning_capabilities: list[Literal['unlabeled_temporal_learning', 'prototype_learning', 'anonymous_cluster_discovery']]
    knowledge_file: str = Field(min_length=1)
    episode_policy: EpisodePolicy
    session_policy: SessionPolicy
    context_schema: dict[Identifier, ContextField]
    business_extension: GarmentExtension | None

    @model_validator(mode='after')
    def policy(self):
        if len(set(self.task_modes)) != len(self.task_modes):
            raise ValueError('重复task_mode')
        if not set(self.session_policy.required_context_keys) <= self.context_schema.keys():
            raise ValueError('session_policy引用未定义context键')
        return self

class StateDefinition(Strict):
    id: Identifier
    name: str = Field(min_length=1)
    description: str
    status: Literal['draft', 'approved']
    observable_cues: list[str]

class ManualRule(Strict):
    type: Literal['manual_review']

class TimeEvidenceRule(Strict):
    type: Literal['time_evidence']
    action_id: Identifier
    min_seconds: float = Field(ge=0)

class ObjectInteractionRule(Strict):
    type: Literal['object_interaction']
    action_id: Identifier
    object_name: str = Field(min_length=1)
    min_seconds: float = Field(ge=0)

class StateTransitionRule(Strict):
    type: Literal['state_transition']
    from_state: Identifier
    to_state: Identifier

EventRule = Annotated[ManualRule | TimeEvidenceRule | ObjectInteractionRule | StateTransitionRule, Field(discriminator='type')]

class EventDefinition(Strict):
    id: Identifier
    name: str = Field(min_length=1)
    description: str
    status: Literal['draft', 'approved']
    risk_level: Literal['general', 'critical']
    trigger: EventRule

class WorkflowNode(Strict):
    node_id: Identifier
    action_id: str | None
    state_id: Identifier | None
    required: bool
    min_count: int = Field(ge=0)
    max_count: int = Field(ge=1)
    role: str
    group: str

    @model_validator(mode='after')
    def target(self):
        if (self.action_id is None) == (self.state_id is None):
            raise ValueError('节点须且仅引用动作或状态之一')
        if self.min_count > self.max_count:
            raise ValueError('min_count大于max_count')
        return self

class WorkflowEdge(Strict):
    from_node: Identifier
    to_node: Identifier

class Workflow(Strict):
    id: Identifier
    version: str = Field(min_length=1)
    ordering: Literal['strict', 'partial', 'unordered']
    nodes: list[WorkflowNode] = Field(min_length=1)
    edges: list[WorkflowEdge]
    repeatable_edges: list[WorkflowEdge]

    @model_validator(mode='after')
    def graph(self):
        nodes = [n.node_id for n in self.nodes]
        if len(nodes) != len(set(nodes)):
            raise ValueError('重复node_id')
        graph = {n: [] for n in nodes}
        pairs = []
        for edge in self.edges + self.repeatable_edges:
            if edge.from_node not in graph or edge.to_node not in graph:
                raise ValueError('边引用不存在')
            pairs.append((edge.from_node, edge.to_node))
        if len(pairs) != len(set(pairs)):
            raise ValueError('重复或普通/返工混用边')
        for edge in self.edges:
            graph[edge.from_node].append(edge.to_node)
        visiting, done = set(), set()
        def visit(n):
            if n in visiting: raise ValueError('未声明的流程环路')
            if n in done: return
            visiting.add(n)
            for target in graph[n]: visit(target)
            visiting.remove(n); done.add(n)
        for node in nodes: visit(node)
        if self.ordering == 'unordered' and self.edges:
            raise ValueError('无序流程不得有顺序边')
        return self

class SceneKnowledge(Strict):
    schema_version: Literal['3.1']
    knowledge_version: str = Field(min_length=1)
    scene_id: Identifier
    task_modes: list[TaskMode] = Field(min_length=1)
    approval_status: Literal['draft', 'approved']
    approved_by: str | None
    actions: list[Action]
    states: list[StateDefinition]
    events: list[EventDefinition]
    workflow: Workflow | None
    curated_memory: list[PatchOperation]

    @model_validator(mode='after')
    def references(self):
        if not self.knowledge_version.strip(): raise ValueError('版本不能为空')
        if len(set(self.task_modes)) != len(self.task_modes): raise ValueError('重复task_mode')
        if self.approval_status == 'approved' and not (self.approved_by or '').strip():
            raise ValueError('正式知识需要批准人')
        groups = [self.actions, self.states, self.events]
        for items in groups:
            if len({i.id for i in items}) != len(items): raise ValueError('同类型定义ID重复')
            if any(not i.name.strip() for i in items): raise ValueError('名称不能为空')
        actions = {a.id: a for a in self.actions}; states = {s.id: s for s in self.states}
        for action in self.actions:
            if not set(action.confusable_with) <= actions.keys(): raise ValueError('混淆动作引用不存在')
            d = action.duration_prior_seconds
            if d is not None and (len(d) != 2 or not 0 <= d[0] <= d[1]): raise ValueError('duration范围无效')
        if 'procedure_compliance' in self.task_modes and self.workflow is None:
            raise ValueError('procedure_compliance需要workflow')
        if self.workflow:
            for node in self.workflow.nodes:
                ref = actions.get(node.action_id) if node.action_id is not None else states.get(node.state_id)
                if ref is None: raise ValueError('流程节点引用不存在或跨场景')
                if self.approval_status == 'approved' and ref.status != 'approved':
                    raise ValueError('正式规范不得引用草案定义')
        for event in self.events:
            rule = event.trigger
            refs = []
            if isinstance(rule, (TimeEvidenceRule, ObjectInteractionRule)):
                refs = [actions.get(rule.action_id)]
            if isinstance(rule, StateTransitionRule):
                refs = [states.get(rule.from_state), states.get(rule.to_state)]
            if any(ref is None for ref in refs): raise ValueError('事件规则引用不存在')
            if event.status == 'approved' and any(ref.status != 'approved' for ref in refs):
                raise ValueError('正式事件规则不得引用草案')
        for op in self.curated_memory:
            if any(ref is not None and ref not in actions for ref in (op.action_id, op.related_action_id)):
                raise ValueError('记忆修正动作引用不存在')
        return self
