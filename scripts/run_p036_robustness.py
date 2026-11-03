"""Private retrospective multi-seed runs; all model parameters from system.yaml."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from packages.config.loader import PROJECT_ROOT, load_bundle
from packages.ingestion.pipeline import file_hash
from packages.exploratory.robustness import run_asset
from packages.exploratory.experiment import save


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--features',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--asset',action='append')
    args=parser.parse_args();bundle=load_bundle(args.config);options=bundle.config.exploratory
    if options is None or not options.enabled or options.formal_research_access:
        parser.error('Opt-in retrospective development settings required')
    if bundle.config.learning.history_windows!=8 or 8 not in options.history_lengths:
        parser.error('Keep the historical eight-window primary baseline and fixed time split')
    if bundle.config.learning.forecast_horizons_seconds!=[.5,2.,5.] or bundle.config.temporal.target_tolerance_ms!=1000:
        parser.error('Historical horizons and tolerance must stay fixed')
    out=args.output.resolve();features=args.features.resolve()
    if not out.is_relative_to(PROJECT_ROOT/'artifacts') or not features.is_relative_to(PROJECT_ROOT/'artifacts'):
        parser.error('Use isolated private artifacts')
    out.mkdir(parents=True,exist_ok=False)
    catalog=[a for a in json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())['assets'] if a['kind']=='video']
    if args.asset:
        if not set(args.asset)<={a['asset_id'] for a in catalog}:parser.error('Unknown development source')
        catalog=[a for a in catalog if a['asset_id'] in args.asset]
    code={str(p.relative_to(PROJECT_ROOT)):file_hash(p) for folder in ('packages/temporal','packages/exploratory','packages/ingestion','packages/encoders','packages/contracts','packages/config') for p in (PROJECT_ROOT/folder).glob('*.py')}
    save(out/'run-config.json',{'git_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'git_dirty':bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip()),
        'system_config_hash':bundle.config_hash,'system_config':bundle.snapshot,'code_files':code,
        'started_utc':datetime.now(timezone.utc).isoformat(),'evidence_class':'retrospective_development',
        'formal_database_accessed':False,'independent_generalization_evidence':False})
    results=[]
    for asset in catalog:
        name=asset['asset_id'];started=time.monotonic();print(name,'started',flush=True)
        try:
            if not (features/name/'manifest.json').is_file():
                result={'asset_id':name,'status':'blocked','reason':'real_feature_extraction_not_complete'}
            else:result=run_asset(features/name,asset,bundle,out/name)
        except Exception as error:
            result={'asset_id':name,'status':'failed','exception_type':type(error).__name__,
                'error_code':getattr(error,'code','EXPERIMENT_FAILED'),'conditions_relaxed':False}
            save(out/name/'failure.json',result)
        result['elapsed_seconds']=round(time.monotonic()-started,3);results.append(result)
        save(out/'results.json',{'assets':results,'formal_status':'blocked','p04_admitted':False,
             'evidence_class':'retrospective_development','independent_sessions_verified':0})
        print(name,result['status'],result['elapsed_seconds'],flush=True)
    return 1 if any(r['status']=='failed' for r in results) else 2 if any(r['status']=='blocked' for r in results) else 0


if __name__=='__main__':raise SystemExit(main())
