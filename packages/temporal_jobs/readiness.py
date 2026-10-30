"""Read-only research admission. Never open vectors, fit models or enqueue work."""
import json
from collections import defaultdict

from sqlalchemy import func, select
from packages.config.errors import ContractError
from packages.contracts.models import Learning
from packages.contracts.temporal import TemporalSettings
from packages.knowledge.service import content_hash
from packages.temporal import engine
from packages.temporal.data import metadata_sequences, snapshot_imports, window_refs, fail
from packages.temporal.provenance import source_provenance, assert_evaluation_independent
from infra.models import ImportJob
from .sampling import analyze, grid_plan

SPLITS = ('train', 'calibration', 'test', 'unassigned')


def selected_imports(service, scene, identifiers):
    service.check_scene(scene)
    scope = (ImportJob.tenant_id == service.tenant, ImportJob.scene_id == scene, ImportJob.id.in_(identifiers))
    # Check scalar bounds before materializing potentially large JSON manifests.
    with service.database.session() as session:
        fields = ('split', 'total_rows', 'feature_space.dimension')
        rows = session.execute(select(ImportJob.id, *[
            func.json_extract(ImportJob.manifest_json, '$.'+name) for name in fields]).where(*scope)).all()
        if len(rows) != len(identifiers):
            fail('IMPORT_NOT_FOUND', '研究输入不属于当前租户/场景')
        budget = defaultdict(lambda: [0, 0])
        for _, split, count, dimension in rows:
            count, dimension = count or 0, dimension or 0
            group = 'fit' if split in {'train', 'calibration'} else split
            budget[group][0] += count
            budget[group][1] += count * dimension
            if (dimension > service.settings.max_dimension or budget[group][0] > service.settings.max_rows
                    or budget[group][1] > service.settings.max_feature_values):
                fail('TEMPORAL_RESOURCE_LIMIT', '所选分区元数据超过研究行数、维度或数值总量预算')
        jobs = session.scalars(select(ImportJob).where(*scope)).all()
        return sorted([{'id': row.id, 'status': row.status,
                        'manifest': json.loads(row.manifest_json) if row.manifest_json else None}
                       for row in jobs], key=lambda row: ((row['manifest'] or {}).get('source_hash', ''), row['id']))


def _issue(error):
    return {'code': error.code, 'message': error.message}


def missing_records(registry, manifest):
    if manifest.get('data_origin') != 'real_video':
        return []
    source = next((s for s in registry.sources if s.source_sha256 == manifest['source_hash']), None) if registry else None
    if source is None:
        return ['dataset_manifest', 'capture_session_id', 'camera_id', 'view_id', 'captured_at',
                'provenance_review', 'near_duplicate_review']
    missing = [name for name in ('capture_session_id', 'camera_id', 'view_id', 'captured_at')
               if getattr(source, name) is None]
    missing.extend(name for name in ('provenance_review', 'near_duplicate_review')
                   if getattr(source, name).status != 'confirmed')
    if source.split == 'unassigned':
        missing.append('split')
    return missing


def check(service, scene, request):
    jobs = selected_imports(service, scene, request.import_ids)
    bundle, settings = service.bundle, service.settings
    registry = settings.dataset_manifest
    records, sequences = [], []
    for job in jobs:
        m = job['manifest'] or {}
        record = {'import_id': job['id'], 'source_hash': m.get('source_hash'),
                  'split': m.get('split', 'unassigned'), 'data_origin': m.get('data_origin'),
                  'eligible_rows': m.get('eligible_rows', 0), 'complete_episodes': 0,
                  'reviewed_group': None, 'missing_records': missing_records(registry, m), 'issues': [],
                  'sampling_diagnostics': None}
        records.append(record)
        if job['status'] != 'succeeded' or not m.get('feature_cache'):
            record['issues'].append({'code': 'TEMPORAL_IMPORT_NOT_READY', 'message': '先完成有效特征导入'})
            continue
        record['complete_episodes'] = sum(e['complete'] for e in m['episodes'])
        try:
            provenance = source_provenance(bundle, m)
            record['reviewed_group'] = provenance['capture_session_id'] if provenance else m['source_hash']
        except ContractError as error:
            record['issues'].append(_issue(error))
        try:
            sequences.extend(metadata_sequences(m, settings))
            if m['split'] != 'test':
                record['sampling_diagnostics'] = analyze(m, bundle.config.learning, settings)
        except ContractError as error:
            record['issues'].append(_issue(error))

    gates = {}
    windows_by_split = {split: [] for split in SPLITS}
    for name, splits in [('train', {'train', 'calibration'}), ('evaluation', {'test'})]:
        chosen = [r for r in records if r['split'] in splits]
        gate = {'status': 'blocked', 'import_ids': [r['import_id'] for r in chosen],
                'snapshot_hash': None, 'blockers': [], 'rejected_windows': {}}
        gates[name] = gate
        blockers = gate['blockers']
        for record in chosen:
            blockers.extend(record['issues'])
        if name == 'train' and any(r['split'] == 'unassigned' for r in records):
            blockers.append({'code': 'TEMPORAL_UNASSIGNED_SELECTION', 'message': '所选来源包含未分区数据，请先按采集记录固定分区或取消选择'})
        snapshot = None
        try:
            if not chosen:
                fail('TEMPORAL_INSUFFICIENT_SOURCES', '请选择 train/calibration 来源' if name == 'train' else '尚未选择独立 test 来源')
            snapshot = snapshot_imports(bundle, service.database, scene, gate['import_ids'], sorted(splits))
            gate['snapshot_hash'] = snapshot['hash']
        except ContractError as error:
            blockers.append(_issue(error))
        learning, limits = bundle.config.learning, settings
        if name == 'evaluation':
            try:
                if not request.model_run_id:
                    fail('TEMPORAL_MODEL_REQUIRED', '评估预检还需选择已成功训练的候选模型')
                with service.database.session() as session:
                    model = service._run_row(session, scene, request.model_run_id)
                    if model.kind != 'train' or model.status != 'succeeded':
                        fail('TEMPORAL_MODEL_NOT_READY', '候选模型尚未成功生成')
                    expected = json.loads(model.report_json)['model']
                if expected['engine_sha256'] != engine.code_hash():
                    fail('TEMPORAL_ENGINE_CHANGED', '模型算法版本已变化，请使用原版本或建立新候选')
                learning = Learning.model_validate(expected['learning'])
                limits = TemporalSettings.model_validate(expected['settings'])
                if snapshot:
                    if any(snapshot[k] != expected['snapshot'][k] for k in
                           ('tenant_id', 'scene_id', 'view_id', 'feature_space_id', 'data_origin')):
                        fail('TEMPORAL_SCOPE_MISMATCH', 'test 与候选模型作用域不一致')
                    assert_evaluation_independent(snapshot, expected['snapshot'])
            except ContractError as error:
                blockers.append(_issue(error))
        else:
            if (not learning.enabled or not learning.freeze_foundation_models
                    or 'temporal_head' not in learning.train_adapters):
                blockers.append({'code': 'TEMPORAL_DISABLED', 'message': '配置未启用冻结特征上的时序学习'})
            if learning.temporal_adapter not in {'ridge', 'causal_tcn'}:
                blockers.append({'code': 'TEMPORAL_ADAPTER_UNAVAILABLE', 'message': '当前仅实现 ridge 与 causal_tcn'})
        subset = [s for s in sequences if s.batches[0].split in splits]
        windows, rejected = window_refs(subset, learning, limits)
        gate['rejected_windows'] = rejected
        for window in windows:
            windows_by_split[window.split].append(subset[window.sequence].manifest['source_hash'])
        try:
            engine.validate_windows(subset, windows, limits, sorted(splits))
        except ContractError as error:
            blockers.append(_issue(error))
        # Repeated per-source reasons are shown once at the gate, individually above.
        gate['blockers'] = list({(b['code'], b['message']): b for b in blockers}.values())
        gate['status'] = 'blocked' if gate['blockers'] else 'metadata_ready'
    partitions = {}
    for split in SPLITS:
        selected = [r for r in records if r['split'] == split]
        partitions[split] = {
            'imports': len(selected), 'source_files': len({r['source_hash'] for r in selected if r['source_hash']}),
            'reviewed_independent_groups': len({r['reviewed_group'] for r in selected if r['reviewed_group']}),
            'complete_episodes': sum(r['complete_episodes'] for r in selected),
            'eligible_rows': sum(r['eligible_rows'] for r in selected),
            'causal_windows': len(windows_by_split[split]),
            'sources_with_causal_windows': len(set(windows_by_split[split])),
            'minimum_sources': getattr(settings, 'min_'+split+'_sources') if split != 'unassigned' else None}
    result = {'stage': 'P0.3.4', 'scene_id': scene, 'metadata_only': True, 'feature_files_opened': False,
              'training_executed': False, 'test_values_read': False, 'jobs_created': False,
              'config_hash': bundle.config_hash, 'engine_hash': engine.code_hash(),
              'dataset_manifest_hash': content_hash(registry.model_dump(mode='json')) if registry else None,
              'records': records, 'partitions': partitions, **gates,
              'sampling_plan': grid_plan(bundle.config.video, bundle.config.learning, settings),
              'can_submit_train': gates['train']['status'] == 'metadata_ready',
              'can_submit_evaluate': gates['evaluation']['status'] == 'metadata_ready',
              'note': '仅检查来源与时间元数据；执行仍校验缓存字节、模型文件和数值。软件门槛通过不代表泛化效果通过。'}
    return {**result, 'report_hash': content_hash(result)}


def template(service, scene, request):
    jobs = selected_imports(service, scene, request.import_ids)
    real = [j['manifest'] for j in jobs if j['manifest'] and j['manifest']['data_origin'] == 'real_video']
    if not real:
        fail('DATASET_REAL_SOURCE_REQUIRED', '采集记录模板需要选择真实视频来源')
    registry = service.settings.dataset_manifest
    draft = registry.model_dump(mode='json') if registry else {
        'manifest_version': 'p031-sources-v1', 'dataset_id': 'dataset_pending',
        'tenant_id': service.tenant, 'sources': [], 'duplicate_reviews': []}
    if draft['tenant_id'] != service.tenant:
        fail('TEMPORAL_PROVENANCE_SCOPE', '数据集来源清单不属于当前租户')
    known = {s['source_sha256'] for s in draft['sources']}
    added = []
    for m in real:
        if m['source_hash'] in known:
            continue
        from packages.contracts.dataset import DatasetSource
        source = DatasetSource(source_sha256=m['source_hash'], root_source_sha256=m['source_hash'],
            relation='original', view_id=m['view_id'], split=m['split'])
        draft['sources'].append(source.model_dump(mode='json'))
        known.add(m['source_hash']); added.append(m['source_hash'])
    return {'draft_only': True, 'configuration_written': False, 'manifest': draft,
            'lineage_needs_confirmation': added,
            'notice': '新增来源暂以 original/self-root 占位，须核验原片或派生关系；未知采集时间、相机、会话和审查人保持空值，审查保持 pending。补真实记录后放入 system.yaml 的 temporal.dataset_manifest；本操作不改分区或配置。'}
