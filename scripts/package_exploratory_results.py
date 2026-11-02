"""Package actual numerical results only, excluding source/derived media."""
import hashlib,json,shutil,zipfile
from pathlib import Path
import numpy as np
from packages.config.loader import PROJECT_ROOT
from packages.exploratory.experiment import save


def main():
    base=PROJECT_ROOT/'artifacts/exploratory';learn=base/'final';features=base/'collected'
    results=json.loads((learn/'results.json').read_text())
    if results.get('pending') or len(results['assets'])!=9:raise ValueError('Do not package an incomplete batch as complete')
    delivery=PROJECT_ROOT/'docs/delivery/exploratory-real-video';delivery.mkdir(parents=True,exist_ok=True)
    for name in ('inventory.json','garment-overlap.json','extraction-inventory.json','final-experiment.yaml'):
        shutil.copyfile(base/name,delivery/name)
    for name in ('run-config.json',):shutil.copyfile(learn/name,delivery/name)
    shutil.copytree(base/'failures',delivery/'failures',dirs_exist_ok=True)
    assets=[];table=[]
    for r in sorted(results['assets'],key=lambda x:x['asset_id']):
        if r.get('status')=='failed':raise ValueError('Preserve failed result explicitly; do not package as successful')
        identifier=r['asset_id'];source=features/identifier;item=dict(r)
        progress=json.loads((source/'progress.json').read_text());last={}
        for event in progress:
            stage=event.get('progress',{}).get('stage',event.get('stage','unknown'));last[stage]=event
        item['progress']={'observed_stage_records':last,'extraction_status':'succeeded','training_status':{k:v['status'] for k,v in r['modes'].items()}}
        # Decimate only display traces/timeline, always retaining endpoints.
        for mode,m in item['modes'].items():
            m=json.loads(json.dumps(m));item['modes'][mode]=m
            if m['status']!='executed':continue
            m['shuffle_diagnostics']={'fixed_points':int((np.random.default_rng(r['seed']).permutation(m['counts']['train'])==np.arange(m['counts']['train'])).sum()),'preserves_target_marginal':True,'scope':'permutes training targets only; overlapping neighbors can remain similar'}
            for key in ('prediction_traces',):
                for split,rows in m[key].items():
                    idx=np.linspace(0,len(rows)-1,min(80,len(rows)),dtype=int)
                    m[key][split]=[{'issued_ms':rows[i]['issued_ms'],
                        'mse':{n:rows[i]['mse'][n] for n in ('persistence','ridge','causal_tcn','shuffled_tcn')},
                        'target_l2':rows[i]['target_l2'],'prediction_l2':{'causal_tcn':rows[i]['prediction_l2']['causal_tcn']}} for i in idx]
            rows=m.get('timeline',[]);idx=np.linspace(0,len(rows)-1,min(300,len(rows)),dtype=int)
            m['timeline']=[rows[i] for i in idx];m['display_decimation']='Evenly spaced actual points, max80 traces/300 timeline; full reports in numerical bundle'
        assets.append(item)
        b=r['modes']['B'];row={'asset':identifier,'rows':r['rows'],'A_windows':r['modes']['A']['counts']['train'],'B_status':b['status'],'B_counts':b['counts']}
        if b['status']=='executed':
            row['holdout_mse']={n:float(np.mean(v['feature_mse'])) for n,v in b['metrics']['holdout'].items()}
            row['selected_by_validation']=b['selected_by_validation']
        table.append(row)
    demo={**results,'assets':assets,'display_only_recorded_data':True}
    (PROJECT_ROOT/'apps/web/src/data/realVideoLearning.json').write_text(json.dumps(demo,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    save(delivery/'summary.json',{'assets':table,'formal_status':'blocked','independent_sessions_verified':0,'confidence_intervals':None})
    bundle=base/'jianwei-exploratory-numerical-bundle.zip'
    members={}
    def include(path,arc):
        if path.suffix.lower() in {'.mp4','.pdf','.png','.jpg','.jpeg','.webp'}:raise ValueError('Media forbidden')
        members[arc]=path
    for name in ('inventory.json','garment-overlap.json','extraction-inventory.json','final-experiment.yaml'):include(base/name,name)
    include(learn/'run-config.json','run-config.json')
    for p in (base/'failures').rglob('*'):
        if p.is_file() and p.suffix in {'.json','.log','.txt'}:include(p,'failures/'+str(p.relative_to(base/'failures')))
    for r in results['assets']:
        identifier=r['asset_id']
        for name in ('manifest.json','vectors.npy','progress.json'):
            include(features/identifier/name,'features/'+identifier+'/'+name)
        include(learn/identifier/'report.json','models/'+identifier+'/report.json')
        for mode,m in r['modes'].items():
            for name in ('report.json','selection-before-holdout.json','weights.npz'):
                path=learn/identifier/mode/name
                if path.is_file():include(path,'models/'+identifier+'/'+mode+'/'+name)
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,path in sorted(members.items()):z.write(path,name)
        inventory={name:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for name,p in sorted(members.items())}
        z.writestr('CONTENTS.json',json.dumps(inventory,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(bundle) as z:assert z.testzip() is None
    save(delivery/'bundle.json',{'file':bundle.name,'sha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),'bytes':bundle.stat().st_size,'entries':len(members)+1,'media_included':False,'contents':inventory})
    print('Numerical bundle:',bundle.stat().st_size,'bytes; actual sources:',len(assets))


if __name__=='__main__':main()
