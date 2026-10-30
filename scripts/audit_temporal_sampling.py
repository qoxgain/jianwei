"""Audit development manifest timestamps without opening vectors or changing splits."""
import argparse
import json
from pathlib import Path

from packages.config.loader import PROJECT_ROOT, load_bundle
from packages.config.errors import ContractError
from packages.temporal_jobs.sampling import analyze, grid_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=PROJECT_ROOT/'config/system.yaml')
    parser.add_argument('--manifest', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= len(args.manifest) <= 100:
        parser.error('provide 1–100 manifests')
    # Do not allow the output to replace a source manifest or the runtime config.
    if args.output.resolve() in {p.resolve() for p in [args.config, *args.manifest]}:
        parser.error('output must not overwrite an input')
    bundle = load_bundle(args.config)
    reports = []
    for path in args.manifest:
        if path.stat().st_size > 8 * 1024 * 1024:
            parser.error('manifest exceeds 8 MiB metadata budget')
        manifest = json.loads(path.read_text())
        if manifest['tenant_id'] != bundle.profile.tenant_id or manifest['scene_id'] not in bundle.config.scenes:
            raise ContractError('TEMPORAL_SCOPE_MISMATCH', '清单不属于配置租户或已注册场景')
        reports.append(analyze(manifest, bundle.config.learning, bundle.config.temporal))
    result = {'schema_version': 'p034-sampling-audit-v1', 'config_hash': bundle.config_hash,
              'metadata_only': True, 'feature_values_read': False, 'training_admission_checked': False,
              'sampling_plan': grid_plan(bundle.config.video, bundle.config.learning, bundle.config.temporal),
              'sources': reports, 'note': '各源单独诊断，不合并工位/视角，不改变分区，也不运行训练。'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'sources': len(reports), 'windows_by_source': [r['causal_windows'] for r in reports]}))
    return 0 if all(r['causal_windows'] for r in reports) else 2


if __name__ == '__main__':
    raise SystemExit(main())
