"""Run registered real assets through HTTP streaming uploads and frozen feature extraction.

Outputs are local ignored artifacts. No video frames or PDF renders are published.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess, sys, threading, time
from pathlib import Path
import httpx, yaml
from packages.config.loader import PROJECT_ROOT, load_bundle
from packages.ingestion.pipeline import file_hash
from packages.temporal.data import metadata_sequences, window_refs
from packages.temporal.exploration import explore


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temporary.replace(path)


def tree_rss(pid):
    seen=set();pending=[pid];total=0
    while pending:
        item=pending.pop()
        if item in seen:continue
        seen.add(item)
        try:
            for line in Path(f'/proc/{item}/status').read_text().splitlines():
                if line.startswith('VmRSS:'):total+=int(line.split()[1])*1024
            for task in Path(f'/proc/{item}/task').iterdir():
                pending.extend(map(int,(task/'children').read_text().split()))
        except (FileNotFoundError,ProcessLookupError):pass
    return total


def disk_bytes(root):
    return sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not p.is_symlink())


class Monitor:
    def __init__(self,pid,root):
        self.pid,self.root=pid,root;self.stage='startup';self.values={};self.stop=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        while not self.stop.is_set():
            try:
                record=self.values.setdefault(self.stage,{'peak_process_tree_rss_bytes':0,'peak_owned_disk_bytes':0,'samples':0})
                record['peak_process_tree_rss_bytes']=max(record['peak_process_tree_rss_bytes'],tree_rss(self.pid))
                record['peak_owned_disk_bytes']=max(record['peak_owned_disk_bytes'],disk_bytes(self.root))
                record['samples']+=1
            except FileNotFoundError:pass
            self.stop.wait(.2)
    def close(self):self.stop.set();self.thread.join()


def blocks(path):
    with path.open('rb') as source:
        while chunk:=source.read(1024*1024):yield chunk


def server(config,log):
    bundle=load_bundle(config);port=bundle.profile.api_port
    process=subprocess.Popen([sys.executable,'-c',
        "import sys,uvicorn;from apps.api.main import create_app;uvicorn.run(create_app(sys.argv[1]),host='127.0.0.1',port=int(sys.argv[2]),log_level='warning')",
        str(config),str(port)],cwd=PROJECT_ROOT,stdout=log,stderr=subprocess.STDOUT)
    client=httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=3600,trust_env=False)
    for _ in range(300):
        if process.poll() is not None:raise RuntimeError('API server failed; inspect log')
        try:
            if client.get('/api/health').status_code==200:return process,client,bundle
        except httpx.ConnectError:pass
        time.sleep(.2)
    process.terminate();raise RuntimeError('API startup deadline exceeded')


def default_limit(config,output,assets):
    raw=yaml.safe_load(config.read_text());raw['ingestion']['max_upload_bytes']=67108864
    path=config.with_name('default-limit.yaml');path.write_text(yaml.safe_dump(raw,allow_unicode=True,sort_keys=False))
    with (output/'default-limit.log').open('w') as log:
        process,client,_=server(path,log)
        try:
            report=[]
            for asset in assets:
                if asset['bytes']<=67108864:continue
                # Send real declared size; server must reject before consuming a body.
                response=client.post('/api/uploads?kind=mp4',content=blocks(PROJECT_ROOT/asset['path']),headers={'Content-Length':str(asset['bytes'])})
                data=response.json();assert response.status_code==413 and data['detail']['code']=='UPLOAD_TOO_LARGE',data
                report.append({'asset_id':asset['asset_id'],'size_bytes':asset['bytes'],'status':response.status_code,'code':data['detail']['code']})
            save(output/'default-limit.json',report)
        finally:client.close();process.terminate();process.wait(timeout=15)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,default=PROJECT_ROOT/'artifacts/p033-runtime/config/system.yaml');parser.add_argument('--output',type=Path,default=PROJECT_ROOT/'artifacts/p033');parser.add_argument('--asset',action='append');parser.add_argument('--skip-default-limit',action='store_true');args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    assets=[a for a in json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())['assets'] if a['kind']=='video']
    if args.asset:assets=[a for a in assets if a['asset_id'] in args.asset]
    if not args.skip_default_limit:default_limit(args.config,output,assets)
    prior=json.loads((output/'acceptance.json').read_text()) if (output/'acceptance.json').exists() else {}
    results=prior.get('assets',[])
    with (output/'api.log').open('a') as log:
        process,client,bundle=server(args.config,log)
        monitor=Monitor(process.pid,bundle.root/'data_demo')
        try:
            capabilities=client.get('/api/ingestion/capabilities').json()
            assert capabilities['max_upload_bytes']==536870912,capabilities
            tenant=hashlib.sha256(bundle.profile.tenant_id.encode()).hexdigest()
            for asset in assets:
                asset_id=asset['asset_id']
                previous=next((r for r in results if r['asset_id']==asset_id),None)
                if previous and previous['config_hash']==bundle.config_hash:
                    job=client.get('/api/scenes/anonymous_observation/imports/'+previous['import_id']).json()
                    if job.get('status')=='succeeded' and (output/asset_id/'exploration.json').is_file():
                        print(asset_id,'resumed completed evidence',flush=True);continue
                path=PROJECT_ROOT/asset['path'];start=time.monotonic();monitor.stage=asset_id+':upload'
                assert file_hash(path)==asset['sha256']
                response=client.post('/api/uploads?kind=mp4',content=blocks(path),headers={'Content-Length':str(path.stat().st_size),'Content-Type':'application/octet-stream'})
                response.raise_for_status();uploaded=response.json();assert uploaded['sha256']==asset['sha256']
                upload_elapsed=time.monotonic()-start
                body={'upload_id':uploaded['id'],'format':'mp4','source_id':asset_id,'data_origin':'real_video','requested_split':'unassigned','context':{},'episodes':[],'clock_anchors':[],'acquisition':None,'view_intervals':[]}
                response=client.post('/api/scenes/anonymous_observation/imports',json=body);response.raise_for_status();job=response.json()
                job_id=job['id'];history=[];last=None
                print(asset_id,'uploaded',uploaded['size_bytes'],'job',job_id,flush=True)
                deadline=time.monotonic()+7200
                while job['status'] in {'queued','running'}:
                    if time.monotonic()>deadline:raise TimeoutError(asset_id)
                    time.sleep(1)
                    job=client.get('/api/scenes/anonymous_observation/imports/'+job_id).json()
                    progress=job.get('progress') or {};stage=progress.get('stage',job['status']);monitor.stage=asset_id+':'+stage
                    marker=(stage,progress.get('completed'))
                    if marker!=last:
                        history.append({'observed_elapsed_seconds':round(time.monotonic()-start,3),'progress':progress});last=marker
                        if len(history)%20==0:print(asset_id,stage,progress.get('completed'),progress.get('total'),flush=True)
                save(output/asset_id/'job.json',job);save(output/asset_id/'progress.json',history)
                if job['status']!='succeeded':raise RuntimeError((asset_id,job.get('error')))
                manifest=job['manifest'];vectors=bundle.paths['datasets']/'imports'/tenant/job_id/'vectors.npy'
                destination=output/asset_id;shutil.copyfile(vectors,destination/'vectors.npy');save(destination/'manifest.json',manifest)
                monitor.stage=asset_id+':explore'
                report=explore(manifest,vectors,path,bundle.config.temporal,bundle.config_hash);save(destination/'exploration.json',report)
                pairs,rejected=window_refs(metadata_sequences(manifest,bundle.config.temporal),bundle.config.learning,bundle.config.temporal)
                item={'asset_id':asset_id,'source_sha256':asset['sha256'],'bytes':path.stat().st_size,'upload_seconds':round(upload_elapsed,3),'total_seconds':round(time.monotonic()-start,3),'import_id':job_id,'config_hash':bundle.config_hash,'feature_space_id':manifest['feature_space_id'],'feature_cache_sha256':manifest['feature_cache']['sha256'],'eligible_rows':manifest['eligible_rows'],'total_rows':manifest['total_rows'],'coverage':report['coverage'],'geometry':manifest['video']['geometry'],'quality':manifest['quality'],'episodes':manifest['episodes'],'view_id':manifest['view_id'],'view_review':manifest['view_review'],'capture_identity':manifest['capture_identity'],'acquisition':manifest['acquisition'],'anonymous_segments':len(report['segments']),'available_causal_pairs_metadata_only':len(pairs),'rejected_pairs':rejected,'split':manifest['split'],'research_status':'blocked_unreviewed_capture_and_no_independent_heldout','training_executed':False,'encoder_evidence':manifest['encoder_evidence'],'stage_timings_seconds':manifest['video'].get('stage_timings_seconds'),'resources':{k.split(':',1)[1]:dict(v) for k,v in monitor.values.items() if k.startswith(asset_id+':')}}
                results=[r for r in results if r['asset_id']!=asset_id]+[item];save(output/'acceptance.json',{'schema_version':'p033-real-acceptance-v1','assets':results,'resource_measurement':{'interval_seconds':.2,'rss':'sum of Linux VmRSS for API process and all descendants; includes shared pages per process','disk':'apparent bytes under isolated data_demo; cumulative retained uploads plus temporary files; no original media/model files'},'independent_learning_status':'blocked','semantic_accuracy':None})
                print(asset_id,'succeeded',manifest['eligible_rows'],'rows',round(report['coverage']['fraction'],4),'coverage',flush=True)
        finally:
            monitor.close();save(output/'resources.json',monitor.values);client.close();process.terminate()
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':main()
