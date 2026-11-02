"""Explicit retrospective experiment runner; separate from formal research jobs."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import yaml

from packages.config.loader import PROJECT_ROOT, load_bundle
from packages.ingestion.pipeline import file_hash
from packages.exploratory.experiment import run,save


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--wait-for-extraction',action='store_true')
    args=parser.parse_args();cfg=yaml.safe_load(args.config.read_text())
    if cfg['evidence_class']!='retrospective_development' or cfg['formal_research_access'] is not False:
        parser.error('Explicit retrospective scope required; formal research access forbidden')
    bundle=load_bundle(PROJECT_ROOT/cfg['system_config'])
    if (cfg['options']['learning']!={} or not 2000<=cfg['options']['guard_ms']<=60000
            or not 8<=cfg['options']['minimum_windows']<=bundle.config.temporal.max_pairs_per_split
            or not 1<=cfg['extraction_wait_seconds']<=10800):
        parser.error('Model parameters must come from system.yaml; bounded guard/window/wait options required')
    out=(PROJECT_ROOT/cfg['output']).resolve();extraction=(PROJECT_ROOT/cfg['extraction']).resolve()
    if not out.is_relative_to(PROJECT_ROOT/'artifacts') or not extraction.is_relative_to(PROJECT_ROOT/'artifacts'):
        parser.error('Use isolated artifacts directories')
    out.mkdir(parents=True,exist_ok=True)
    recipe_files={str(p.relative_to(PROJECT_ROOT)):file_hash(p) for folder in ('packages/ingestion','packages/encoders','packages/temporal','packages/exploratory') for p in (PROJECT_ROOT/folder).glob('*.py')}
    recipe_files['scripts/run_exploratory_real_video.py']=file_hash(Path(__file__))
    save(out/'run-config.json',{'options':cfg,'runtime_config':bundle.snapshot,'runtime_config_hash':bundle.config_hash,
        'experiment_config_sha256':file_hash(args.config),'git_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'code_files':recipe_files,'numpy_version':__import__('numpy').__version__,
        'git_dirty':bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip()),
        'started_utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        'formal_database_accessed':False,'p04_admitted':False})
    assets=[a for a in json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())['assets'] if a['kind']=='video']
    if set(cfg['assets'])!={a['asset_id'] for a in assets}:parser.error('This version audits exactly the nine allowlisted development videos')
    remaining={a['asset_id']:a for a in assets};reports=[]
    deadline=time.monotonic()+cfg['extraction_wait_seconds']
    while remaining:
        for identifier,a in list(remaining.items()):
            directory=extraction/identifier
            if not (directory/'manifest.json').is_file() or not (directory/'vectors.npy').is_file():continue
            target=out/identifier
            print(identifier,'training actual real-video features',flush=True)
            try:
                result=run(directory,a,bundle,cfg['options'],target,bundle.config.encoder.weights_sha256)
            except Exception as error:
                result={'asset_id':identifier,'status':'failed','error_type':type(error).__name__,'error':str(error),
                    'evidence_class':cfg['evidence_class'],'formal_status':'blocked'}
                save(target/'failure.json',result)
            reports.append(result);del remaining[identifier]
            save(out/'results.json',{'scope':cfg['evidence_class'],'formal_status':'blocked','assets':reports,
                'pending':sorted(remaining),'independent_sessions_verified':0,'auto_activated':False})
            print(identifier,'completed',[(k,v['status']) for k,v in result.get('modes',{}).items()],flush=True)
        if not remaining:break
        if not args.wait_for_extraction or time.monotonic()>deadline:
            save(out/'pending.json',{'status':'incomplete','missing_extraction':sorted(remaining)});return 2
        time.sleep(5)
    return 1 if any(r.get('status')=='failed' for r in reports) else 0


if __name__=='__main__':raise SystemExit(main())
