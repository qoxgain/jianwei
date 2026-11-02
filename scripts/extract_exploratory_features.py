"""File-only execution of the same frozen pipeline, no API or database access."""
import argparse,json,time
from pathlib import Path
from packages.config.loader import PROJECT_ROOT,load_bundle
from packages.contracts.ingestion import ImportRequest
from packages.ingestion.pipeline import process_import,file_hash
from packages.exploratory.experiment import save


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--asset',action='append',required=True);args=p.parse_args()
    bundle=load_bundle(args.config)
    catalog={a['asset_id']:a for a in json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())['assets'] if a['kind']=='video'}
    root=PROJECT_ROOT/'artifacts/exploratory/direct'
    for identifier in args.asset:
        asset=catalog[identifier];path=PROJECT_ROOT/asset['path'];target=root/identifier
        target.mkdir(parents=True,exist_ok=True);events=[];started=time.monotonic();previous=[None]
        def progress(stage,**details):
            event={'stage':stage,'elapsed_seconds':round(time.monotonic()-started,3),**details}
            if stage!=previous[0] or ('completed' in details and details['completed']%20==0 and (not events or events[-1].get('completed')!=details['completed'])):
                events.append(event);save(target/'progress.json',events);print(identifier,stage,details.get('completed'),flush=True)
            previous[0]=stage
        try:
            if file_hash(path)!=asset['sha256']:raise ValueError('Original byte identity differs')
            request=ImportRequest(upload_id='local_development_input',format='mp4',source_id=identifier,data_origin='real_video',requested_split='unassigned')
            manifest=process_import(bundle,'anonymous_observation',request,path,output_dir=target,progress=progress)
            if manifest['status']!='succeeded':raise ValueError(str(manifest.get('blocker')))
            save(target/'manifest.json',manifest)
            save(target/'execution.json',{'asset_id':identifier,'source_sha256':asset['sha256'],'config_hash':bundle.config_hash,
                'eligible_rows':manifest['eligible_rows'],'total_seconds':round(time.monotonic()-started,3),'execution':'direct_same_frozen_pipeline','database_accessed':False})
        except Exception as error:
            save(target/'failure.json',{'asset_id':identifier,'status':'failed','error_type':type(error).__name__,'error':str(error),'conditions_relaxed':False});raise


if __name__=='__main__':main()
