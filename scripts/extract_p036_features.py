"""New private, file-only extraction of allowlisted development videos; no DB."""
import argparse
import json
from pathlib import Path
import time

from packages.config.loader import PROJECT_ROOT, load_bundle
from packages.contracts.ingestion import ImportRequest
from packages.ingestion.pipeline import process_import, file_hash
from packages.ingestion.recipe import production_snapshot


def write(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--asset',action='append')
    args=parser.parse_args();bundle=load_bundle(args.config)
    output=args.output.resolve()
    if not output.is_relative_to(PROJECT_ROOT/'artifacts'):
        parser.error('New private artifacts output directory required')
    output.mkdir(parents=True,exist_ok=False)
    settings=bundle.config.exploratory
    if settings is None or not settings.enabled:parser.error('Explicit retrospective system.yaml required')
    assets=[a for a in json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())['assets'] if a['kind']=='video']
    if args.asset:
        if not set(args.asset)<={a['asset_id'] for a in assets}:parser.error('Only catalog development videos allowed')
        assets=[a for a in assets if a['asset_id'] in args.asset]
    dependencies=production_snapshot()
    write(output/'production-before-extraction.json',{'config_hash':bundle.config_hash,
        'dependencies':dependencies,'evidence_class':'retrospective_development','formal_database_accessed':False})
    results=[]
    for asset in assets:
        name=asset['asset_id'];target=output/name;target.mkdir(exist_ok=False)
        started=time.monotonic();previous=[None,None]
        def progress(stage,completed=0,total=None,**details):
            interval=max(1,(total or 1)//settings.trace_limit)
            marker=(stage,completed)
            if stage!=previous[0] or (total and completed%interval==0 and marker!=tuple(previous)):
                with (target/'progress.jsonl').open('a') as log:
                    log.write(json.dumps({'stage':stage,'completed':completed,'total':total,
                        'elapsed_seconds':round(time.monotonic()-started,3)})+'\n')
                previous[:]=marker
        try:
            path=PROJECT_ROOT/asset['path']
            if file_hash(path)!=asset['sha256']:raise ValueError('Development source SHA mismatch')
            request=ImportRequest(upload_id='local_development_input',format='mp4',source_id=name,
                data_origin='real_video',requested_split='unassigned')
            manifest=process_import(bundle,'anonymous_observation',request,path,output_dir=target,progress=progress)
            if manifest['status']!='succeeded':raise ValueError('Frozen feature extraction blocked: '+str(manifest.get('blocker',{}).get('code')))
            write(target/'manifest.json',manifest)
            result={'asset_id':name,'status':'succeeded','rows':manifest['eligible_rows'],
                'source_sha256':asset['sha256'],'cache_sha256':manifest['feature_cache']['sha256'],
                'feature_space_id':manifest['feature_space_id'],'seconds':round(time.monotonic()-started,3),
                'database_accessed':False,'formal_status':'blocked'}
            write(target/'execution.json',result)
        except Exception as error:
            result={'asset_id':name,'status':'failed','error_type':type(error).__name__,
                'error_code':getattr(error,'code','EXTRACTION_FAILED'),'seconds':round(time.monotonic()-started,3),
                'conditions_relaxed':False,'formal_status':'blocked'}
            write(target/'failure.json',result)
        results.append(result);write(output/'inventory.json',results)
        print(json.dumps(result),flush=True)
    return 0 if all(r['status']=='succeeded' for r in results) else 2


if __name__=='__main__':raise SystemExit(main())
