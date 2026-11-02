"""Audit recorded import metadata without reading feature values or fitting models.

Different feature extractions of one source remain versions of that source, not
independent sessions. Test sources are never passed to sampling diagnostics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from pydantic import ValidationError

from packages.config.errors import ContractError
from packages.config.loader import PROJECT_ROOT, load_bundle, resolve_path
from packages.contracts.capture import Acquisition, CaptureIdentity
from packages.contracts.learning import FeatureSpace
from packages.knowledge.service import content_hash
from packages.temporal.protocol import preview_protocol
from packages.temporal.provenance import _reviewed_coverage, source_provenance
from packages.temporal_jobs.sampling import analyze


MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_TOTAL_MANIFEST_BYTES = 64 * 1024 * 1024


def _invalid_json(_):
    raise ValueError('JSON must contain finite numbers')


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        _invalid_json(text)
    return value


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _read_manifest(path):
    # Bound the read itself, including files that grow after a stat call.
    with path.open('rb') as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ContractError('RESEARCH_AUDIT_BUDGET', '单个清单超过 8 MiB 元数据上限')
    try:
        manifest = json.loads(raw, parse_constant=_invalid_json,
                              parse_float=_finite_float, object_pairs_hook=_unique_keys)
        if not isinstance(manifest, dict):
            raise ValueError
        if not re.fullmatch('[0-9a-f]{64}', manifest.get('source_hash', '')):
            raise ValueError
        if manifest.get('split') not in {'unassigned', 'train', 'calibration', 'test'}:
            raise ValueError
        if manifest.get('data_origin') not in {'real_video', 'synthetic_features'}:
            raise ValueError
        if any(manifest.get(key) is not None and not isinstance(manifest[key], dict)
               for key in ('feature_space', 'feature_cache', 'encoder_evidence')):
            raise ValueError
        # This also rejects integers outside the supported canonical JSON range.
        content_hash(manifest)
    except (ValueError, TypeError, RecursionError):
        raise ContractError('RESEARCH_AUDIT_MANIFEST', '清单需要有效对象、来源 SHA、分区及有限 JSON 数值') from None
    return manifest, hashlib.sha256(raw).hexdigest(), len(raw)


def _capture_gaps(manifest, registry_source):
    gaps = []
    if manifest['data_origin'] == 'synthetic_features':
        return gaps
    identity = manifest.get('capture_identity') or {}
    acquisition = manifest.get('acquisition') or {}
    for name, raw, required, contract in (
        ('capture_identity', identity, ('physical_camera_id', 'stable_view_id'), CaptureIdentity),
        ('acquisition', acquisition, ('capture_session_id', 'captured_at'), Acquisition),
    ):
        if not isinstance(raw, dict):
            gaps.append(name)
            continue
        gaps.extend(f'{name}.{key}' for key in required if not raw.get(key))
        if raw.get('review_status') != 'confirmed':
            gaps.append(f'{name}.review_status')
        try:
            contract.model_validate(raw)
        except ValidationError:
            gaps.append(f'{name}.valid_contract')
    if manifest.get('capture_contract') != 'p033-capture-v1':
        gaps.append('capture_contract')
    try:
        _reviewed_coverage(manifest)
    except ContractError:
        gaps.append('view_review.complete_reviewed_coverage')
    if registry_source is None:
        gaps.append('temporal.dataset_manifest.sources[source_hash]')
        gaps.extend(f'dataset_source.{key}' for key in (
            'root_source_sha256', 'relation', 'capture_session_id', 'camera_id', 'view_id',
            'captured_at', 'provenance_review', 'near_duplicate_review'))
    else:
        for key in ('capture_session_id', 'camera_id', 'view_id', 'captured_at'):
            if getattr(registry_source, key) is None:
                gaps.append(f'dataset_source.{key}')
        for key in ('provenance_review', 'near_duplicate_review'):
            if getattr(registry_source, key).status != 'confirmed':
                gaps.append(f'dataset_source.{key}')
        if registry_source.split != manifest['split']:
            gaps.append('dataset_source.split_matches_import')
    if manifest['split'] == 'unassigned':
        gaps.append('split')
    if manifest.get('source_verification') != 'verified':
        gaps.append('source_verification')
    return sorted(set(gaps))


def audit(bundle, scene, paths, *, sampling=False):
    """Return a gap inventory, never research admission or effect acceptance."""
    paths = [Path(path) for path in paths]
    if not 1 <= len(paths) <= 100:
        raise ContractError('RESEARCH_AUDIT_BUDGET', '必须提供 1–100 份清单')
    if scene not in bundle.scenes:
        raise ContractError('SCENE_NOT_FOUND', '场景不存在')
    if bundle.config.temporal is None:
        raise ContractError('TEMPORAL_DISABLED', '当前配置未启用时序研究')
    loaded, total_bytes = [], 0
    for path in paths:
        manifest, digest, size = _read_manifest(path)
        total_bytes += size
        if total_bytes > MAX_TOTAL_MANIFEST_BYTES:
            raise ContractError('RESEARCH_AUDIT_BUDGET', '所选清单合计超过 64 MiB 元数据上限')
        if (manifest.get('tenant_id'), manifest.get('scene_id')) != (bundle.profile.tenant_id, scene):
            raise ContractError('TEMPORAL_SCOPE_MISMATCH', '清单不属于配置租户及所选场景')
        loaded.append((path, manifest, digest))
    registry = bundle.config.temporal.dataset_manifest
    sources = {item.source_sha256: item for item in registry.sources} if registry else {}
    # An older unassigned import of a now-sealed source cannot expose sampling
    # diagnostics. A second supplied version marked test also seals that source.
    sealed = {key for key, item in sources.items() if item.split == 'test'}
    sealed.update(m['source_hash'] for _, m, _ in loaded if m['split'] == 'test')
    groups = {}
    for path, manifest, digest in loaded:
        source_hash = manifest['source_hash']
        gaps = _capture_gaps(manifest, sources.get(source_hash))
        try:
            space = FeatureSpace.model_validate(manifest.get('feature_space'))
            if space.identifier() != manifest.get('feature_space_id'):
                gaps.append('feature_space_id')
        except ValidationError:
            gaps.append('feature_space')
        if not re.fullmatch('[0-9a-f]{64}', (manifest.get('feature_cache') or {}).get('sha256', '') or ''):
            gaps.append('feature_cache.sha256')
        provenance, issue = None, None
        try:
            provenance = source_provenance(bundle, manifest)
        except ContractError as error:
            issue = error.detail()
        except (KeyError, TypeError, ValueError):
            issue = {'code': 'RESEARCH_AUDIT_MANIFEST', 'message': '采集来源元数据不完整或无效', 'errors': []}
        diagnostic, diagnostic_issue = None, None
        if source_hash in sealed:
            diagnostic_status = 'sealed_test_not_inspected'
        elif sampling:
            try:
                diagnostic = analyze(manifest, bundle.config.learning, bundle.config.temporal)
                diagnostic_status = 'development_metadata_only'
            except (ContractError, ValidationError, KeyError, TypeError, ValueError) as error:
                diagnostic_status = 'invalid_metadata'
                diagnostic_issue = error.detail() if isinstance(error, ContractError) else {
                    'code': 'RESEARCH_AUDIT_MANIFEST', 'message': '采样元数据不完整或无效', 'errors': []}
        else:
            diagnostic_status = 'not_requested'
        version = {
            'input_file': str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else path.name,
            'input_file_sha256': digest,
            'manifest_hash': content_hash(manifest), 'config_hash': manifest.get('config_hash'),
            'feature_space_id': manifest.get('feature_space_id'),
            'feature_space': manifest.get('feature_space'),
            'feature_cache_sha256': (manifest.get('feature_cache') or {}).get('sha256'),
            'encoder_evidence': manifest.get('encoder_evidence'),
            'source_id': manifest.get('source_id'), 'view_id': manifest.get('view_id'),
            'split': manifest['split'], 'data_origin': manifest['data_origin'],
            'capture_identity': manifest.get('capture_identity'), 'acquisition': manifest.get('acquisition'),
            'missing_fields': gaps, 'provenance': provenance, 'provenance_issue': issue,
            'sampling_status': diagnostic_status, 'sampling': diagnostic, 'sampling_issue': diagnostic_issue,
            'metadata_status': 'blocked' if gaps or issue or diagnostic_issue else 'metadata_complete',
        }
        group = groups.setdefault(source_hash, {'source_hash': source_hash, 'versions': []})
        # Supplying the exact same manifest twice does not duplicate a version.
        if not any(v['manifest_hash'] == version['manifest_hash'] for v in group['versions']):
            group['versions'].append(version)
    for group in groups.values():
        group['versions'].sort(key=lambda item: item['manifest_hash'])
        group['version_count'] = len(group['versions'])
        group['metadata_status'] = 'blocked' if any(v['metadata_status'] == 'blocked' for v in group['versions']) else 'metadata_complete'
    preview = preview_protocol(bundle, scene)
    registered_sessions = {v['provenance']['capture_session_id']
        for group in groups.values() for v in group['versions']
        if v['provenance'] and v['metadata_status'] == 'metadata_complete'}
    result = {
        'schema_version': 'p035-research-metadata-audit-v1', 'stage': 'P0.3.5',
        'scene_id': scene, 'tenant_id': bundle.profile.tenant_id,
        'status': 'blocked' if preview['status'] == 'blocked' or any(g['metadata_status'] == 'blocked' for g in groups.values()) else 'metadata_ready',
        'config_hash': bundle.config_hash, 'protocol_preview': preview,
        'input_manifest_count': len(loaded), 'unique_source_count': len(groups),
        'feature_version_count': sum(g['version_count'] for g in groups.values()),
        'reviewed_capture_session_count': len(registered_sessions),
        'sources': [groups[key] for key in sorted(groups)],
        'metadata_only': True, 'feature_values_read': False, 'training_executed': False,
        'test_sampling_diagnostics_executed': False, 'training_admission_checked': False,
        'research_effect': 'not_evaluated', 'p04_admitted': False,
        'note': '同源重提仅增加特征版本，不增加独立会话。此报告不检查数据库导入准入，也不替代独立研究验收；现有开发录像不能重新声明为新封存测试。',
    }
    return {**result, 'report_hash': content_hash(result)}


def write_report(bundle, inputs, output, result):
    """Keep configuration, knowledge, source metadata and prior evidence intact."""
    output = Path(output)
    protected = [bundle.path, *map(Path, inputs)]
    protected.extend(resolve_path(bundle.root, scene.knowledge_file) for scene in bundle.scenes.values())
    for path in protected:
        if output.resolve() == path.resolve() or (output.exists() and path.exists() and output.samefile(path)):
            raise ContractError('RESEARCH_AUDIT_OVERWRITE', '输出不能覆盖配置、知识包或输入清单')
    raw = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if output.exists():
        if output.stat().st_size == len(raw) and output.read_bytes() == raw:
            return
        raise ContractError('RESEARCH_AUDIT_OVERWRITE', '已有报告内容不同，请选择新的输出文件以保留证据')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=PROJECT_ROOT/'config/system.yaml')
    parser.add_argument('--scene', default='anonymous_observation')
    parser.add_argument('--manifest', type=Path, action='append', required=True)
    parser.add_argument('--sampling', action='store_true', help='also diagnose development metadata; always skip sealed test')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        bundle = load_bundle(args.config)
        result = audit(bundle, args.scene, args.manifest, sampling=args.sampling)
        write_report(bundle, args.manifest, args.output, result)
    except ContractError as error:
        print(json.dumps(error.detail(), ensure_ascii=False))
        return 2
    except OSError:
        print(json.dumps({'code': 'RESEARCH_AUDIT_IO', 'message': '清单无法读取或报告无法写入，请检查文件路径及权限'}, ensure_ascii=False))
        return 2
    print(json.dumps({key: result[key] for key in (
        'status', 'unique_source_count', 'feature_version_count', 'reviewed_capture_session_count', 'research_effect')}, ensure_ascii=False))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
