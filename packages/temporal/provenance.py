"""Admission of reviewed acquisition groups; no claim of automated authenticity."""
from packages.config.errors import ContractError
from packages.knowledge.service import content_hash


def fail(code, message):
    raise ContractError(code, message)


def source_provenance(bundle, manifest):
    """Return immutable evidence only for real research, not exploratory imports."""
    if manifest['data_origin'] == 'synthetic_features':
        return None
    registry = bundle.config.temporal.dataset_manifest
    if registry is None:
        fail('TEMPORAL_PROVENANCE_REQUIRED', '真实研究需要在 system.yaml 登记采集会话与近重复审查清单')
    if registry.tenant_id != bundle.profile.tenant_id:
        fail('TEMPORAL_PROVENANCE_SCOPE', '数据集来源清单不属于当前租户')
    sources = {item.source_sha256: item for item in registry.sources}
    source = sources.get(manifest['source_hash'])
    if source is None:
        fail('TEMPORAL_PROVENANCE_REQUIRED', '真实原始来源尚未登记到研究数据集')
    # Review every ancestor too: approving a transcoded child cannot manufacture
    # reviewed lineage from an unknown parent or acquisition session.
    node = source
    lineage = []
    while True:
        lineage.append(node.source_sha256)
        if node.provenance_review.status != 'confirmed' or node.near_duplicate_review.status != 'confirmed':
            fail('TEMPORAL_PROVENANCE_PENDING', '来源或父来源的采集关系、近重复审查尚未完成')
        if node.parent_source_sha256 is None:
            break
        node = sources[node.parent_source_sha256]
    if source.split == 'unassigned' or source.split != manifest['split']:
        fail('TEMPORAL_PROVENANCE_SPLIT', '导入分区与已固定采集会话分区不一致')
    if source.view_id != manifest['view_id']:
        fail('TEMPORAL_PROVENANCE_SCOPE', '采集来源视角与导入视角不一致')
    # A flagged duplicate involving any file of this root/session is a blocker,
    # even when the selected import happens to be another derived file.
    group_hashes = {item.source_sha256 for item in registry.sources
                   if item.root_source_sha256 == source.root_source_sha256
                   or item.capture_session_id == source.capture_session_id}
    if any(review.resolution == 'pending' and
           group_hashes.intersection((review.left_sha256, review.right_sha256))
           for review in registry.duplicate_reviews):
        fail('TEMPORAL_DUPLICATE_REVIEW_PENDING', '该采集会话存在尚未排除的近重复来源')
    return {'dataset_id': registry.dataset_id,
            'dataset_manifest_sha256': content_hash(registry.model_dump(mode='json')),
            'capture_session_id': source.capture_session_id,
            'root_source_sha256': source.root_source_sha256,
            'lineage_sha256': lineage,
            'source': source.model_dump(mode='json'),
            'verification_scope': 'human-reviewed acquisition and duplicate provenance; not automated authenticity proof'}


def assert_evaluation_independent(test_snapshot, model_snapshot):
    if {e['source_hash'] for e in test_snapshot['entries']} & {e['source_hash'] for e in model_snapshot['entries']}:
        fail('TEMPORAL_TEST_LEAKAGE', '封存 test 原始来源与训练/校准重叠')
    if test_snapshot['data_origin'] == 'synthetic_features':
        return
    test = [e.get('provenance') for e in test_snapshot['entries']]
    fitted = [e.get('provenance') for e in model_snapshot['entries']]
    if not all(test) or not all(fitted):
        fail('TEMPORAL_PROVENANCE_REQUIRED', '候选模型或评估快照缺少已核验的采集来源记录')
    if {p['dataset_manifest_sha256'] for p in test} != {p['dataset_manifest_sha256'] for p in fitted}:
        fail('TEMPORAL_DATASET_CHANGED', '研究数据集清单已变化；必须在训练前固定全部分区并建立新候选')
    for key in ('capture_session_id', 'root_source_sha256'):
        if {p[key] for p in test} & {p[key] for p in fitted}:
            fail('TEMPORAL_TEST_LEAKAGE', '封存 test 采集会话或原始根与训练/校准重叠')
