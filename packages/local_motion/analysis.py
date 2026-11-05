"""Retrospective scalar responses/candidates; no action labels or accuracy proxy."""
from pathlib import Path
import hashlib,json
import numpy as np
from packages.config.errors import ContractError
from packages.knowledge.service import content_hash
from .extraction import sha,write


def normalize(points,until):
    baseline=np.array([p['raw'] for p in points if p['pts_ms']<=until and p['raw'] is not None],dtype=float)
    if len(baseline)<3:
        for p in points:p['normalized']=None
        return {'status':'insufficient_prefix_baseline','count':len(baseline)}
    median=float(np.median(baseline));scale=max(float(np.median(abs(baseline-median)))*1.4826,1e-6)
    for p in points:p['normalized']=None if p['raw'] is None else float(np.clip((p['raw']-median)/scale,-100,100))
    return {'status':'ok','count':len(baseline),'median':median,'mad_scale':scale,'floor':1e-6,'fit_until_ms':until}


def smoothed(points,radius):
    result=[];groups=[];current=[]
    for i,p in enumerate(points):
        if p['normalized'] is None:
            if current:groups.append(current);current=[]
        else:current.append(i)
    if current:groups.append(current)
    values=[None]*len(points)
    for group in groups:
        for i in group:
            neighbors=[j for j in group if abs(points[j]['pts_ms']-points[i]['pts_ms'])<=radius]
            values[i]=float(np.mean([points[j]['normalized'] for j in neighbors]))
    return values


def suppress(candidates,refractory,budget=None):
    selected=[];suppressed=0
    for c in sorted(candidates,key=lambda v:(-v['score'],v['pts_ms'],v['id'])):
        if any(abs(v['pts_ms']-c['pts_ms'])<refractory for v in selected):suppressed+=1;continue
        selected.append(c)
    omitted=max(0,len(selected)-(budget or len(selected)))
    if budget is not None:selected=selected[:budget]
    return sorted(selected,key=lambda v:v['pts_ms']),{'eligible':len(candidates),'temporal_suppressed':suppressed,'budget_omitted':omitted}


def candidates(series,threshold,smoothing,refractory):
    points=series['points'];values=smoothed(points,smoothing);found=[]
    for i,(point,value) in enumerate(zip(points,values)):
        if value is None or value<threshold:continue
        previous=values[i-1] if i else None;following=values[i+1] if i+1<len(values) else None
        peak=(previous is None or value>=previous) and (following is None or value>following)
        onset=series['method']!='r3d' and (previous is None or previous<threshold)
        offset=series['method']!='r3d' and (following is None or following<threshold)
        if not (peak or onset or offset):continue
        kind='representation_change' if series['method']=='r3d' else 'motion_onset' if onset else 'motion_offset' if offset else 'motion_peak'
        identity={'series':series['id'],'arm':series.get('arm_id','standalone'),'pts':point['pts_ms'],'threshold':threshold,'smoothing':smoothing,'kind':kind}
        found.append({'id':'cand_'+content_hash(identity)[:20],'series':series['id'],'roi':series['roi'],'role':series['role'],
            'pts_ms':point['pts_ms'],'start_ms':point['start_ms'],'end_ms':point['end_ms'],'available_after_ms':point['end_ms'],
            'kind':kind,'score':value,'semantic_action':None})
    return suppress(found,refractory)[0]


def paired(a,b):
    by_time={p['pts_ms']:p['raw'] for p in b['points'] if p['raw'] is not None}
    pairs=[(p['raw'],by_time[p['pts_ms']]) for p in a['points'] if p['raw'] is not None and p['pts_ms'] in by_time]
    values=np.asarray(pairs,dtype=float).reshape(-1,2)
    if not len(values):return {'aligned_points':0,'correlation':None,'mean_ratio':None,'relative_absolute_change':None}
    x,y=values.T;corr=float(np.corrcoef(x,y)[0,1]) if np.std(x)>1e-12 and np.std(y)>1e-12 else None
    return {'aligned_points':len(values),'correlation':corr,'mean_ratio':float(x.mean()/y.mean()) if abs(y.mean())>1e-8 else None,
        'relative_absolute_change':float(np.mean(abs(x-y))/max(abs(x.mean()),1e-8)),
        'interpretation':'descriptive_only_not_boundary_accuracy; ROI magnification and content differ'}


def analyze(directory,output=None):
    directory=Path(directory);manifest=json.loads((directory/'manifest.json').read_text());s=manifest['settings']
    if sha(directory/'features.npz')!=manifest['features_sha256']:raise ContractError('MOTION_FEATURE_CHANGED','特征文件哈希与提取清单不符')
    with np.load(directory/'features.npz',allow_pickle=False) as arrays:features=arrays['features'].copy()
    if features.shape!=(len(manifest['rows']),512):raise ContractError('MOTION_FEATURE_INVALID','特征形状与逐行PTS清单不符')
    until=s['start_ms']+(s['end_ms']-s['start_ms'])*s['baseline_fraction'];arms=[]
    for window in s['window_ms']:
        for fps in s['sample_fps']:
            series=[]
            for roi in s['rois']:
                rows=sorted((r for r in manifest['rows'] if r['roi']==roi['id'] and r['window_ms']==window and r['fps']==fps),key=lambda r:r['start_ms'])
                for method in ['r3d','frame_difference','block_motion']:
                    if method=='r3d' and not roi.get('encode_r3d',True):continue
                    points=[];previous=None
                    for row in rows:
                        raw=None;status=row['status']
                        if status=='valid':
                            if method=='r3d':
                                vector=features[row['index']]
                                if np.isfinite(vector).all():
                                    if previous is not None:
                                        old,old_row=previous
                                        if row['start_ms']-old_row['start_ms']==s['stride_ms']:
                                            denominator=float(np.linalg.norm(old)*np.linalg.norm(vector))
                                            raw=float(np.clip(1-np.dot(old,vector)/denominator,0,2)) if denominator>1e-12 else None
                                    previous=(vector,row)
                                else:previous=None
                                if raw is None:status='no_previous_valid_embedding'
                            else:raw=row.get(method)
                        else:previous=None
                        points.append({'pts_ms':row['center_ms'],'start_ms':row['start_ms'],'end_ms':row['end_ms'],'raw':raw,'status':status})
                    baseline=normalize(points,until);valid=[p['raw'] for p in points if p['raw'] is not None]
                    series.append({'id':roi['id']+':'+method,'arm_id':f'w{window}-f{fps}','roi':roi['id'],'role':roi['role'],'method':method,'points':points,'baseline':baseline,
                        'stats':{'valid':len(valid),'missing':len(points)-len(valid),'mean':float(np.mean(valid)) if valid else None,
                            'p50':float(np.median(valid)) if valid else None,'p90':float(np.quantile(valid,.9)) if valid else None}})
            grid=[]
            for item in series:
                for smoothing in s['smoothing_ms']:
                    for threshold in s['thresholds']:
                        cs=candidates(item,threshold,smoothing,s['refractory_ms'])
                        grid.append({'series':item['id'],'threshold':threshold,'smoothing_ms':smoothing,'count':len(cs),'candidates':cs})
            comparison=[]
            background=next(r['id'] for r in s['rois'] if r['role']=='background')
            for method in ['r3d','frame_difference','block_motion']:
                bg=next((v for v in series if v['id']==background+':'+method),None)
                for item in series:
                    if bg and item['method']==method and item['role'] in {'hands','interaction'}:
                        comparison.append({'operation':item['id'],'control':bg['id'],**paired(item,bg)})
            offsets=[];hands=next(r['id'] for r in s['rois'] if r['role']=='hands')
            for item in series:
                if item['role']=='offset_control':
                    base=next(v for v in series if v['id']==hands+':'+item['method'])
                    offsets.append({'base':base['id'],'offset':item['id'],**paired(base,item),'deep_offset_tested':False})
            arms.append({'id':f'w{window}-f{fps}','window_ms':window,'fps':fps,'series':series,'grid':grid,'operation_background':comparison,'offset_sensitivity':offsets})
    arm=next(a for a in arms if a['window_ms']==s['primary_window_ms'] and a['fps']==s['primary_fps'])
    local_ids={v['id'] for v in arm['series'] if v['role'] in {'hands','interaction'}}
    pool=[c for g in arm['grid'] if g['series'] in local_ids and g['threshold']==s['primary_threshold'] and g['smoothing_ms']==s['primary_smoothing_ms'] for c in g['candidates']]
    selected,selection=suppress(pool,s['refractory_ms'],s['candidate_budget'])
    analysis_source={p.name:sha(p) for p in Path(__file__).parent.glob('*.py')}
    identity={'feature_recipe':manifest['recipe_hash'],'features_sha256':manifest['features_sha256'],'analysis_sources':analysis_source,
        'version':'b1-prefix-mad-time-smooth-candidates-v1','settings':s}
    report={'schema':'b1-local-motion-report-v1','run_id':content_hash(identity),'recipe_hash':manifest['recipe_hash'],
        'analysis_hash':content_hash(identity),'source':manifest['source'],'settings':s,'evidence_class':manifest['evidence_class'],
        'encoder':manifest['encoder'],'model_sha256':manifest['recipe']['model_sha256'],'features_sha256':manifest['features_sha256'],
        'analysis_source_sha256':analysis_source,'feature_source_sha256':manifest['recipe']['source_files'],
        'semantics':'offline descriptive responses, centered smoothing; no observed action labels','arms':arms,
        'primary':{'arm':arm['id'],'threshold':s['primary_threshold'],'smoothing_ms':s['primary_smoothing_ms'],'candidates':selected,'selection':selection,
            'count':len(selected),'target_review_budget':[30,50],'budget_is_not_a_quota':True},
        'boundary_evaluation':{'status':'blocked','reason':'NO_EXHAUSTIVE_HUMAN_BOUNDARIES','precision':None,'recall':None,'f1':None,'tolerance_ms':s['boundary_tolerance_ms']},
        'quality':manifest['quality'],'feature_rows':len(manifest['rows']),'elapsed_extraction_seconds':manifest['elapsed_seconds'],
        'formal_research_passed':False,'b2_started':False,'b2_recommendation':'pending_manual_boundary_evidence'}
    if output:write(output,report)
    return report
