"""Fixed retrospective cohorts and train/validation-only model selection.

No formal database is imported or opened. Local weights/caches remain private;
publication is an explicit scalar-statistics export by the caller.
"""
from pathlib import Path
import numpy as np
from packages.temporal import models
from packages.temporal.data import window_refs, values
from packages.ingestion.pipeline import file_hash
from packages.knowledge.service import content_hash
from .experiment import load_source, partitions, within, tensors, metrics, save
from . import heads


# Algorithm definitions, not data-selected hyperparameters. Matched input/target
# cohorts include all baselines and all negative controls at every seed.
HEADS={
 'causal_tcn':('direct',(1,2),False,False),
 'initial_tcn':('direct',(1,2),False,False),
 'shuffled_tcn':('direct',(1,2),False,True),
 'direct_tcn_val':('direct',(1,2),True,False),
 'direct_last_only':('direct_last_only',(1,2),True,False),
 'delta_tcn':('delta',(1,2),True,False),
 'shuffled_delta_tcn':('delta',(1,2),True,True),
 'delta_last_only':('delta_last_only',(1,2),True,False),
 'delta_shuffled_history':('delta_shuffled_history',(1,2),True,False),
 'multiscale_delta_tcn':('delta',None,True,False),
 'velocity_delta_tcn':('delta_velocity',None,True,False),
}
NEGATIVE={'initial_tcn','shuffled_tcn','shuffled_ridge','shuffled_delta_tcn',
          'delta_last_only','delta_shuffled_history','direct_last_only'}


def motion(x,scale):return np.mean(((x[:,-1]-x[:,-2])/scale)**2,axis=-1)


def make_cohort(sequences,duration,learning,settings,history,minimum,guard):
    spans=partitions(duration,guard,'B');pools={k:within(sequences,v) for k,v in spans.items()}
    windows={};coverage={};rejected={}
    use=learning.model_copy(update={'history_windows':history})
    for name,pool in pools.items():
        details={};windows[name],rejected[name]=window_refs(pool,use,settings,coverage=details);coverage[name]=details
    row_sets={k:{r for s in pool for r in s.rows} for k,pool in pools.items()}
    if any(row_sets[a]&row_sets[b] for a,b in [('train','validation'),('train','holdout'),('validation','holdout')]):
        raise ValueError('Raw feature row overlap across purged partitions')
    cohorts={name:[{'issued_ms':w.issued_ms,'history_rows':[pools[name][w.sequence].rows[j] for j in w.history],
             'target_rows':[pools[name][w.sequence].rows[j] for j in w.targets]} for w in refs]
             for name,refs in windows.items()}
    meta={'history_windows':history,'intervals_ms':spans,'counts':{k:len(v) for k,v in windows.items()},
        'row_overlap':False,'coverage':coverage,'rejected':rejected,
        'cohort_hash':content_hash(cohorts),'status':'ready' if all(len(w)>=minimum for w in windows.values()) else 'blocked'}
    return pools,windows,meta


def fit_seed(train_x,train_y,val_x,val_y,projection,bundle,seed,history):
    settings=bundle.config.exploratory;learning=bundle.config.learning
    rank=projection['components'].shape[1];horizons=train_y.shape[1]
    z=models.project(train_x,projection);vz=models.project(val_x,projection)
    target=models.project(train_y,projection);delta=target-z[:,-1:, :]
    permutation=np.random.default_rng(seed).permutation(len(target))
    configs={};parameters={};logs={}
    # The original Ridge and direct heads see the last eight clips on the SAME
    # longer-history cohort. The long delta Ridge explicitly gets all history.
    for name,residual,shuffled,long in [('ridge',False,False,False),('shuffled_ridge',False,True,False),
            ('delta_ridge',True,False,False),('long_delta_ridge',True,False,True)]:
        tx=z if long else z[:,-8:]
        x=heads.delta_input(tx,'delta' if residual else 'direct',seed)
        y=delta if residual else target
        parameters[name]=models.ridge_fit(x,y[permutation] if shuffled else y,learning.ridge_l2)
        configs[name]={'kind':'ridge','residual':residual,'mode':'delta' if residual else 'direct',
                       'history':history if long else min(8,history)}
    for name,(mode,dilations,select,shuffled) in HEADS.items():
        residual=mode.startswith('delta') or name in {'multiscale_delta_tcn','velocity_delta_tcn'}
        long=dilations is None
        if long:
            dilations=list(settings.dilations)
            while 1+2*sum(dilations)<history:dilations.append(2*dilations[-1])
        count=history if long else min(8,history)
        if mode=='delta_shuffled_history':count=min(count,1+2*sum(dilations))
        x=heads.delta_input(z[:,-count:],mode,seed);vx=heads.delta_input(vz[:,-count:],mode,seed)
        y=delta if residual else target
        def score(p):
            predicted=heads.decode(heads.forward(vx,p,dilations),projection,val_x[:,-1],horizons,residual)
            return np.mean((predicted-val_y)**2)
        def train_score(p):
            predicted=heads.decode(heads.forward(x,p,dilations),projection,train_x[:,-1],horizons,residual)
            return np.mean((predicted-train_y)**2)
        if name=='initial_tcn':
            params=heads.initialize(rank,learning.hidden_dim,horizons*rank,seed,dilations)
            log={'selected_epoch':0,'train_latent_mse':[], 'validation_raw_feature_mse':[],
                 'parameter_delta_l2':0.,'seed':seed,'receptive_field_windows':7,'effective_history_windows':min(count,7)}
        else:
            params,log=heads.fit(x,y[permutation] if shuffled else y,learning,seed,dilations,
                bundle.config.temporal.gradient_clip_norm,score=score,train_score=train_score,select_validation=select,zero_output=residual)
        parameters[name]=params;logs[name]=log
        configs[name]={'kind':'tcn','mode':mode,'residual':residual,'dilations':list(dilations),'history':count}
    parameters['train_delta_mean']={'value':delta.mean(0)}
    configs['train_delta_mean']={'kind':'delta_mean','residual':True}
    return parameters,configs,logs


def predict(x,projection,parameters,configs,seed,horizons):
    z=models.project(x,projection)
    predictions={'persistence':np.repeat(x[:,-1:,:],horizons,axis=1),
        'train_mean':np.broadcast_to(projection['mean'],(len(x),horizons,x.shape[-1])).copy()}
    for name,p in parameters.items():
        c=configs[name]
        if c['kind']=='delta_mean':latent=np.broadcast_to(p['value'],(len(x),*p['value'].shape))
        else:
            tx=heads.delta_input(z[:,-c['history']:],c['mode'],seed)
            latent=models.ridge_predict(tx,p) if c['kind']=='ridge' else heads.forward(tx,p,c['dilations'])
        predictions[name]=heads.decode(latent,projection,x[:,-1],horizons,c['residual'])
    return predictions


def compare_split(x,y,projection,parameters,configs,seed,motion_threshold,refs,pools,trace_limit):
    predictions=predict(x,projection,parameters,configs,seed,y.shape[1])
    activity=motion(x,projection['scale']);low=activity<=motion_threshold
    summary={'metrics':{n:metrics(p,y,projection['scale']) for n,p in predictions.items()},
        'motion_groups':{},'window_count':len(x)}
    for label,mask in [('low_observed_motion',low),('high_observed_motion',~low)]:
        summary['motion_groups'][label]={'windows':int(mask.sum()),'metrics':{
            n:metrics(p[mask],y[mask],projection['scale']) for n,p in predictions.items()} if mask.any() else {}}
    trace=[]
    for index in np.linspace(0,len(x)-1,min(trace_limit,len(x)),dtype=int):
        w=refs[index];seq=pools[w.sequence]
        trace.append({'issued_ms':w.issued_ms,'target_ms':[[seq.batches[j].pts_start_ms,seq.batches[j].pts_end_ms] for j in w.targets],
            'history_start_ms':seq.batches[w.history[0]].pts_start_ms,
            'observed_input_motion':float(activity[index]),'motion_group':'low_observed_motion' if low[index] else 'high_observed_motion',
            'actual_future_l2':np.linalg.norm(y[index],axis=-1).tolist(),
            'predicted_future_l2':{n:np.linalg.norm(p[index],axis=-1).tolist() for n,p in predictions.items()},
            'prediction_mse':{n:np.mean((p[index]-y[index])**2,axis=-1).tolist() for n,p in predictions.items()}})
    summary['scalar_traces']=trace
    return summary


def run_asset(directory,asset,bundle,out):
    options=bundle.config.exploratory
    manifest,sequences=load_source(directory,asset,bundle,bundle.config.encoder.weights_sha256)
    duration=max(d['end_ms'] for d in manifest['domains'])
    result={'asset_id':asset['asset_id'],'source_sha256':asset['sha256'],'rows':manifest['eligible_rows'],
        'cache_sha256':manifest['feature_cache']['sha256'],'feature_space_id':manifest['feature_space_id'],
        'manifest_sha256':file_hash(Path(directory)/'manifest.json'), 'encoder_sha256':bundle.config.encoder.weights_sha256,
        'evidence_class':'retrospective_development','formal_research_status':'blocked',
        'independent_sessions_verified':0,'confidence_intervals':None,'p04_admitted':False,
        'cohorts':{},'seeds':options.seeds,'horizons_seconds':bundle.config.learning.forecast_horizons_seconds}
    if len(manifest['domains'])!=1 or manifest['domains'][0]['start_ms']!=0:
        result['status']='blocked';result['reason']='ambiguous_chronology';save(out/'report.json',result);return result
    for history in options.history_lengths:
        pools,windows,cohort=make_cohort(sequences,duration,bundle.config.learning,bundle.config.temporal,
            history,options.minimum_windows,options.guard_ms)
        result['cohorts'][str(history)]={'metadata':cohort,'runs':[]}
        if cohort['status']=='blocked':continue
        raw=np.concatenate([values(s,range(len(s.rows))) for s in pools['train']])
        projection,explained=models.fit_projection(raw,bundle.config.temporal.projection_dim)
        train_x,train_y=tensors(pools['train'],windows['train']);val_x,val_y=tensors(pools['validation'],windows['validation'])
        threshold=float(np.quantile(motion(train_x,projection['scale']),options.motion_quantile))
        for seed in options.seeds:
            target=out/f'history-{history}'/f'seed-{seed}';target.mkdir(parents=True,exist_ok=False)
            parameters,configs,logs=fit_seed(train_x,train_y,val_x,val_y,projection,bundle,seed,history)
            arrays={'projection__'+k:v for k,v in projection.items()}
            for name,p in parameters.items():arrays.update({name+'__'+k:v for k,v in p.items()})
            weights=target/'weights.npz';np.savez_compressed(weights,**arrays)
            with np.load(weights,allow_pickle=False) as loaded:
                if set(loaded.files)!=set(arrays) or any(not np.array_equal(loaded[k],v) for k,v in arrays.items()):
                    raise ValueError('Weight reload changed an array')
                projection={k:loaded['projection__'+k].copy() for k in projection}
                parameters={name:{k:loaded[name+'__'+k].copy() for k in p} for name,p in parameters.items()}
            run={'seed':seed,'history':history,'training':logs,'models':configs,'weights_sha256':file_hash(weights),
                 'weight_reload_exact':True,'motion_threshold_train_only':threshold,'projection_explained_variance':explained,
                 'encoder_updated':False,'splits':{}}
            for name,x,y in [('train',train_x,train_y),('validation',val_x,val_y)]:
                run['splits'][name]=compare_split(x,y,projection,parameters,configs,seed,threshold,
                    windows[name],pools[name],options.trace_limit)
            val=run['splits']['validation']['metrics']
            chosen=min((n for n in val if n not in NEGATIVE),key=lambda n:np.mean(val[n]['feature_mse']))
            run['selected_by_validation']=chosen
            save(target/'selection-before-holdout.json',{'selected_model':chosen,'selection_split':'validation',
                'weights_sha256':run['weights_sha256'],'cohort_hash':cohort['cohort_hash'],
                'holdout_metrics_used_for_selection':False,'seed':seed,'models':configs})
            # First access to heldout numeric values follows selection and weights.
            x,y=tensors(pools['holdout'],windows['holdout'])
            run['splits']['holdout']=compare_split(x,y,projection,parameters,configs,seed,threshold,
                windows['holdout'],pools['holdout'],options.trace_limit)
            save(target/'report.json',run)
            result['cohorts'][str(history)]['runs'].append(run)
            save(out/'report.json',result)
    result['status']='executed' if any(c['runs'] for c in result['cohorts'].values()) else 'blocked'
    save(out/'report.json',result)
    return result
