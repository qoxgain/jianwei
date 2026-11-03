"""Metadata-only research registration, frozen before opening feature values.

The registry supplies human-reviewed acquisition assertions, not automated proof
of independence. No hashes or timestamps are inferred from filenames.
"""
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from packages.config.errors import ContractError
from packages.contracts.research import ResearchProtocol
from packages.knowledge.service import content_hash
from packages.temporal.data import fail
from packages.temporal.provenance import source_provenance
from packages.ingestion.capture import roi_identity
from packages.ingestion.pipeline import file_hash
from packages.encoders import pretrained
from packages.ingestion.recipe import production_snapshot


def _bindings(bundle, scene):
    from .engine import code_hash
    config = bundle.config
    registry = config.temporal.dataset_manifest
    return {
        'config_hash': bundle.config_hash,
        'engine_hash': code_hash(),
        'knowledge_hash': content_hash(bundle.current_knowledge(scene)[0].model_dump()),
        'dataset_manifest_hash': content_hash(registry.model_dump(mode='json')) if registry else None,
        'sampling': config.video.model_dump(mode='json'),
        'learning': config.learning.model_dump(mode='json'),
        'temporal': config.temporal.model_dump(mode='json', exclude={'dataset_manifest', 'research_protocol'}),
        'encoder': config.encoder.model_dump(mode='json'),
        'feature_recipe': {'production_dependencies': production_snapshot(), 'decode_width': config.ingestion.decode_width,
            'encoder_input_size': config.ingestion.encoder_input_size,
            'video_implementation_sha': file_hash(Path(__file__).resolve().parents[1]/'ingestion/video.py'),
            'recipe_implementation_sha': file_hash(Path(pretrained.__file__)),
            'roi_hashes': {source.id: roi_identity(source) for source in bundle.profile.sources}},
    }


def _rosters(bundle):
    settings = bundle.config.temporal
    registry, policy = settings.dataset_manifest, settings.research_protocol
    if registry is None:
        fail('TEMPORAL_PROVENANCE_REQUIRED', '请先登记真实采集会话、来源谱系、固定分区与近重复审查')
    if registry.tenant_id != bundle.profile.tenant_id:
        fail('TEMPORAL_PROVENANCE_SCOPE', '研究来源清单不属于当前租户')
    selected = set(policy.source_sha256s) if policy and policy.source_sha256s else None
    known = {item.source_sha256 for item in registry.sources}
    if selected and not selected <= known:
        fail('TEMPORAL_PROTOCOL_ROSTER', '预注册来源必须全部登记在 dataset_manifest 中')
    groups = defaultdict(lambda: {'train': [], 'calibration': [], 'test': []})
    sessions = set()
    for item in sorted(registry.sources, key=lambda value: value.source_sha256):
        if selected is not None and item.source_sha256 not in selected:
            continue
        if item.split == 'unassigned':
            fail('TEMPORAL_PROTOCOL_ROSTER', '预注册研究来源仍有未固定分区；请审核或显式选定研究来源')
        source_provenance(bundle, {'data_origin': 'real_video', 'source_hash': item.source_sha256,
                                  'split': item.split, 'view_id': item.view_id})
        if item.capture_session_id in sessions:
            fail('TEMPORAL_DUPLICATE_SESSION', '协议包含同会话的多个文件；请用 source_sha256s 显式选择每会话一个来源')
        sessions.add(item.capture_session_id)
        groups[item.view_id][item.split].append(item.model_dump(mode='json'))
    if not groups:
        fail('TEMPORAL_PROTOCOL_ROSTER', '尚无已预注册来源')
    return [{'view_id': view, 'partitions': partitions} for view, partitions in sorted(groups.items())]


def preview_protocol(bundle, scene):
    """Only registry and current configuration; never imports, vectors or jobs."""
    if scene not in bundle.scenes:
        fail('SCENE_NOT_FOUND', '场景不存在')
    settings = getattr(bundle.config, 'temporal', None)
    if settings is None:
        fail('TEMPORAL_DISABLED', '当前配置未启用时序研究')
    policy = settings.research_protocol
    blockers, missing, rosters = [], [], []
    if policy is None:
        missing.append('temporal.research_protocol')
        blockers.append({'code': 'TEMPORAL_PROTOCOL_REQUIRED',
                         'message': '真实研究须在 system.yaml 预注册主要指标、双对照、独立会话门槛与改进阈值'})
    elif policy.registered_at > datetime.now(timezone.utc):
        blockers.append({'code': 'TEMPORAL_PROTOCOL_FUTURE_REGISTRATION', 'message': '研究登记时间不能晚于当前冻结时间'})
    try:
        rosters = _rosters(bundle)
        for roster in rosters:
            for split in ('train', 'calibration', 'test'):
                minimum = getattr(settings, 'min_' + split + '_sources')
                # This count only establishes that final uncertainty can be
                # computed; the configured sample size remains a study decision.
                if split == 'test' and policy:
                    minimum = max(minimum, policy.min_independent_sessions)
                if len(roster['partitions'][split]) < minimum:
                    blockers.append({'code': 'TEMPORAL_PROTOCOL_INSUFFICIENT_SESSIONS',
                        'message': f"视角 {roster['view_id']} 的 {split} 独立会话少于预注册门槛 {minimum}"})
    except ContractError as error:
        blockers.append({'code': error.code, 'message': error.message})
        if settings.dataset_manifest is None:
            missing.append('temporal.dataset_manifest')
    wire = policy.model_dump(mode='json') if policy else None
    return {'stage': 'P0.3.5', 'scene_id': scene, 'status': 'blocked' if blockers else 'configured',
        'protocol_hash': content_hash(wire) if wire else None, 'protocol': wire,
        'missing_fields': missing, 'bindings': _bindings(bundle, scene), 'rosters': rosters,
        'blockers': blockers, 'metadata_only': True, 'feature_values_read': False,
        'training_executed': False, 'test_values_read': False, 'jobs_created': False}


def _assert_entries(snapshot, roster, splits):
    registered = {item['source_sha256']: item for split in splits for item in roster['partitions'][split]}
    entries = snapshot['entries']
    if len(entries) != len(registered) or {entry['source_hash'] for entry in entries} != set(registered):
        fail('TEMPORAL_PROTOCOL_ROSTER_MISMATCH', '请求必须包含该视角预注册分区的完整来源，不能事后挑选来源子集')
    for entry in entries:
        item = registered[entry['source_hash']]
        provenance = entry.get('provenance') or {}
        if (entry['split'] != item['split'] or snapshot['view_id'] != item['view_id']
                or any(provenance.get(key) != item[key] for key in ('capture_session_id', 'root_source_sha256'))):
            fail('TEMPORAL_PROTOCOL_ROSTER_MISMATCH', '请求的采集身份、视角或分区与预注册来源不一致')


def freeze_protocol(bundle, scene, snapshot):
    if snapshot['data_origin'] == 'synthetic_features':
        return None
    preview = preview_protocol(bundle, scene)
    if preview['blockers']:
        first = preview['blockers'][0]
        fail(first['code'], first['message'])
    roster = next((item for item in preview['rosters'] if item['view_id'] == snapshot['view_id']), None)
    if roster is None:
        fail('TEMPORAL_PROTOCOL_ROSTER_MISMATCH', '预注册清单不包含当前研究视角')
    _assert_entries(snapshot, roster, ('train', 'calibration'))
    _assert_features(snapshot, preview['bindings'])
    frozen = {'version': 'p035-preregistered-v1', 'protocol': preview['protocol'],
        'bindings': preview['bindings'], 'tenant_id': snapshot['tenant_id'], 'scene_id': scene,
        'feature_space_id': snapshot['feature_space_id'], 'roster': roster,
        'training_snapshot_hash': snapshot['hash']}
    return {'snapshot': frozen, 'hash': content_hash(frozen)}


def assert_frozen_protocol(binding, snapshot, bundle=None, scene=None):
    if snapshot['data_origin'] == 'synthetic_features':
        return
    if not isinstance(binding, dict) or not isinstance(binding.get('snapshot'), dict):
        fail('TEMPORAL_PROTOCOL_REQUIRED', '候选模型没有训练前冻结的研究协议；请使用新封存资料建立预注册研究')
    frozen = binding['snapshot']
    if content_hash(frozen) != binding.get('hash'):
        fail('TEMPORAL_PROTOCOL_CHANGED', '冻结研究协议摘要不匹配')
    try:
        ResearchProtocol.model_validate(frozen['protocol'])
        if (frozen['version'] != 'p035-preregistered-v1'
                or any(frozen[key] != snapshot[key] for key in ('tenant_id', 'scene_id', 'feature_space_id'))):
            fail('TEMPORAL_PROTOCOL_CHANGED', '冻结研究协议的作用域或契约版本不一致')
        _assert_entries(snapshot, frozen['roster'], ('test',))
        _assert_features(snapshot, frozen['bindings'])
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError):
        fail('TEMPORAL_PROTOCOL_CHANGED', '冻结研究协议内容无效')
    if bundle is not None:
        current = preview_protocol(bundle, scene or snapshot['scene_id'])
        if (current['blockers'] or current['protocol'] != frozen['protocol']
                or current['bindings'] != frozen['bindings']
                or frozen['roster'] not in current['rosters']):
            fail('TEMPORAL_PROTOCOL_CHANGED', '配置、知识、研究算法或预注册来源已变化；必须另设新的封存测试')


def _assert_features(snapshot, bindings):
    """Reject external/diagnostic/stale caches without opening any feature values."""
    encoder, sampling, recipe = bindings['encoder'], bindings['sampling'], bindings['feature_recipe']
    if (encoder['adapter'] != 'onnx' or not encoder['weights_sha256']
            or encoder['preprocessing_version'] != pretrained.PREPROCESSING_VERSION
            or encoder['checkpoint_revision'] != 'torchvision-v0.22.1-R3D_18_Weights.KINETICS400_V1'
            or encoder['feature_dim'] != pretrained.FEATURE_DIM
            or sampling['frames_per_clip'] != pretrained.FRAMES_PER_CLIP
            or recipe['encoder_input_size'] != pretrained.INPUT_SIZE):
        fail('TEMPORAL_FORMAL_ENCODER_REQUIRED', '真实研究须配置已核验的正式冻结 R3D-18 ONNX 与官方输入配方；诊断编码器仅用于工程探索')
    sampling_hash = content_hash({'clip_ms': int(round(sampling['clip_seconds']*1000)),
        'stride_ms': int(round(sampling['stride_seconds']*1000)),
        'frames_per_clip': sampling['frames_per_clip'], 'method': 'nearest_original_pts_v1'})
    for entry in snapshot['entries']:
        # source_provenance has a read-compatible legacy path. New registered
        # studies require the physical-view contract, whose full reviewed time
        # coverage was validated during snapshot admission before any values.
        if (entry.get('capture_contract') != 'p033-capture-v1'
                or (entry.get('capture_identity') or {}).get('review_status') != 'confirmed'
                or (entry.get('acquisition') or {}).get('review_status') != 'confirmed'):
            fail('TEMPORAL_CAPTURE_REQUIRED', '预注册真实研究需要经过核验的物理机位、稳定视角、采集会话及完整视角区间；历史清单须按新契约核验后重新导入')
        space, preprocessing, evidence = (entry.get('feature_space') or {},
            entry.get('preprocessing') or {}, entry.get('encoder_evidence') or {})
        if preprocessing.get('production_dependencies') != recipe['production_dependencies']:
            fail('TEMPORAL_PRODUCTION_RECIPE_REQUIRED','实际特征缺少当前完整生产依赖快照或版本已变化；旧缓存保留可读，须按新配方重新提取后开展新研究')
        if (evidence.get('artifact_sha_verified') is not True
                or evidence.get('execution') != 'frozen_onnx_cpu'
                or evidence.get('vector_production') != 'executed_from_this_source'
                or evidence.get('weights_sha') != encoder['weights_sha256']):
            fail('TEMPORAL_FORMAL_ENCODER_REQUIRED', '来源缺少按本协议正式冻结编码器执行的证据；外部特征声明不能替代实际执行')
        expected = {'encoder_sha': encoder['weights_sha256'], 'sampling_sha': sampling_hash,
                    'dimension': encoder['feature_dim'], 'pooling': encoder['pooling'],
                    'roi_policy_hash': recipe['roi_hashes'].get(entry.get('source_id'))}
        if (expected['roi_policy_hash'] is None or any(space.get(k) != v for k,v in expected.items())
                or content_hash(space) != snapshot['feature_space_id']
                or content_hash(preprocessing) != space.get('preprocessing_sha')
                or preprocessing.get('implementation_sha') != recipe['video_implementation_sha']
                or preprocessing.get('recipe_implementation_sha') != recipe['recipe_implementation_sha']
                or preprocessing.get('version') != encoder['preprocessing_version']
                or preprocessing.get('input_size') != recipe['encoder_input_size']
                or preprocessing.get('decode_width') != recipe['decode_width']
                or preprocessing.get('normalization') != 'official_mean_std_embedded_in_onnx'
                or preprocessing.get('resize') != 'bounded_bilinear_decode_then_roi_float_bilinear_128x171_center112'):
            fail('TEMPORAL_PROTOCOL_FEATURE_MISMATCH', '实际缓存的采样、预处理、ROI、维度或编码器与冻结协议不一致；请按协议重新提取整个来源')


def heldout_identity(roster):
    """Tenant-wide identity: changing scene, encoding or derived bytes cannot reset it."""
    entries = roster['partitions']['test']
    return {'source_hashes': sorted({item['source_sha256'] for item in entries}),
            'root_source_hashes': sorted({item['root_source_sha256'] for item in entries}),
            'capture_session_ids': sorted({item['capture_session_id'] for item in entries})}
