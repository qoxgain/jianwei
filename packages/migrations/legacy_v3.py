"""Never edits the input. The original YAML/id/hash remain authoritative history."""
import hashlib
from packages.contracts.models import Knowledge, SystemConfig
from packages.contracts.scenes import SceneKnowledge, SceneSpec
from packages.contracts.configuration import SceneConfig

def adapt_knowledge(legacy: Knowledge) -> tuple[SceneKnowledge, dict[str, str]]:
    raw = legacy.model_dump()
    sop = raw.pop('sop')
    mapping = {n['action_id']: f"node_{i+1:03d}_{hashlib.sha256(n['action_id'].encode()).hexdigest()[:12]}"
               for i, n in enumerate(sop['nodes'])}
    nodes = [dict(n, node_id=mapping[n['action_id']], state_id=None) for n in sop['nodes']]
    edge = lambda e: {'from_node': mapping[e['from_action']], 'to_node': mapping[e['to_action']]}
    raw.update(schema_version='3.1', task_modes=['activity_recognition', 'procedure_compliance'], states=[], events=[],
               workflow={**sop, 'nodes': nodes, 'edges': [edge(e) for e in sop['edges']],
                         'repeatable_edges': [edge(e) for e in sop['repeatable_edges']]})
    return SceneKnowledge.model_validate(raw), mapping

def legacy_scene(config: SystemConfig) -> SceneSpec:
    session = config.session.model_dump()
    return SceneSpec.model_validate({
        'display_name': '服装SOP检验', 'task_modes': ['activity_recognition', 'procedure_compliance'],
        'learning_capabilities': ['unlabeled_temporal_learning', 'prototype_learning'],
        'knowledge_file': config.knowledge_file,
        'episode_policy': {'mode': 'manual_or_external_cycle', 'window_seconds': None},
        'session_policy': {'required_context_keys': ['camera_id', 'product_style_id'], 'exclusive_resource_key': 'camera_id',
            **{k: session[k] for k in ('lease_ttl_seconds', 'lease_heartbeat_seconds', 'max_session_minutes')}},
        'context_schema': {'camera_id': {'type': 'string', 'description': '注册相机'},
                           'product_style_id': {'type': 'string', 'description': '款式'}},
        'business_extension': {'type': 'garment_inspection', **{k: session[k] for k in
            ('score_base', 'verified_missing_penalty', 'missing_without_evidence', 'separate_rework')}}})

def adapt_config(config: SystemConfig) -> SceneConfig:
    raw = config.model_dump()
    raw.pop('knowledge_file'); raw.pop('session')
    for profile in raw['profiles'].values():
        for source in profile['sources']:
            source['scene_ids'] = [source.pop('scene_id')]
    raw.update(config_version='3.1', scenes={config.active_scene: legacy_scene(config).model_dump()})
    return SceneConfig.model_validate(raw)
