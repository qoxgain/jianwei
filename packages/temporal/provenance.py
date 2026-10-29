"""Admission of reviewed acquisition groups; no claim of automated authenticity."""
import json
from pydantic import ValidationError
from packages.config.errors import ContractError
from packages.contracts.capture import Acquisition, CaptureIdentity, ViewInterval
from packages.knowledge.service import content_hash


def fail(code, message):
    raise ContractError(code, message)


def _reviewed_coverage(manifest):
    """A confirmed flag alone cannot replace complete reviewed time coverage."""
    try:
        intervals = [ViewInterval.model_validate(item) for item in manifest.get('view_review', [])]
        domains = manifest['domains']
        if not intervals or not domains or any(item.status == 'unreviewed' for item in intervals):
            raise ValueError
        by_index = {}
        for domain in domains:
            index, start, end = domain['domain_index'], domain['start_ms'], domain['end_ms']
            if (any(type(value) is not int for value in (index, start, end))
                    or index < 0 or start < 0 or end <= start or index in by_index):
                raise ValueError
            by_index[index] = (start, end)
        if any(item.domain_index not in by_index or not item.reason.strip() for item in intervals):
            raise ValueError
        for index, (start, end) in by_index.items():
            cursor = start
            for item in sorted((item for item in intervals if item.domain_index == index), key=lambda item:item.start_ms):
                if item.start_ms != cursor or item.end_ms > end:
                    raise ValueError
                cursor = item.end_ms
            if cursor != end:
                raise ValueError
    except (ValidationError, KeyError, TypeError, ValueError):
        fail('TEMPORAL_VIEW_REVIEW_PENDING', '完整来源仍有未审查、缺失、重叠或无效的镜头稳定性区间')


def source_provenance(bundle, manifest):
    """Return immutable evidence only for real research, not exploratory imports."""
    if manifest['data_origin'] == 'synthetic_features':
        return None
    identity = acquisition = None
    if manifest.get('capture_contract') == 'p033-capture-v1':
        raw_identity, raw_acquisition = manifest.get('capture_identity') or {}, manifest.get('acquisition') or {}
        if (not isinstance(raw_identity, dict) or not isinstance(raw_acquisition, dict)
                or raw_identity.get('review_status') != 'confirmed'
                or raw_acquisition.get('review_status') != 'confirmed'):
            fail('TEMPORAL_CAPTURE_PENDING', '机位、稳定视角或采集会话尚未核实，特征可探索但不可作为独立研究来源')
        try:
            identity = CaptureIdentity.model_validate(raw_identity)
            # Manifests persist JSON, including an ISO datetime string. JSON mode
            # retains the strict field contract while accepting its wire format.
            acquisition = Acquisition.model_validate_json(json.dumps(raw_acquisition))
        except (ValidationError, TypeError, ValueError):
            fail('TEMPORAL_CAPTURE_INVALID', '确认采集记录缺少有效机位、稳定视角、会话或带时区时间')
        expected_view = content_hash({'contract': 'physical-view-v1',
            'physical_camera_id': identity.physical_camera_id, 'stable_view_id': identity.stable_view_id})
        if manifest.get('view_id') != expected_view:
            fail('TEMPORAL_PROVENANCE_SCOPE', '导入视角摘要与已确认机位及稳定视角不一致')
        _reviewed_coverage(manifest)
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
    if identity is not None:
        if (source.capture_session_id != acquisition.capture_session_id
                or source.camera_id != identity.physical_camera_id
                or source.captured_at != acquisition.captured_at):
            fail('TEMPORAL_PROVENANCE_SCOPE', '导入采集身份与已复核研究来源记录不一致')
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
