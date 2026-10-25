"""Train-only fitting, calibration selection and explicit sealed-test replay."""
from collections import defaultdict, Counter
import json
from pathlib import Path
import hashlib
import os
import numpy as np
from packages.knowledge.service import content_hash
from packages.ingestion.pipeline import file_hash
from .data import fail, values, window_refs, has_barrier
from .provenance import assert_evaluation_independent
from .models import (fit_projection, project, reconstruct, kmeans, assign, ridge_fit,
                     ridge_predict, tcn_fit, tcn_forward)

MODELS=('persistence','empirical','ridge','causal_tcn','shuffled_ridge')


def code_hash():
    paths=sorted(Path(__file__).parent.glob('*.py'))
    return content_hash({p.name:file_hash(p) for p in paths})


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def atomic_json(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(_json(value)+'\n',encoding='utf-8');os.replace(temp,path)


def save_model(directory,payload,arrays):
    directory.mkdir(parents=True,exist_ok=True)
    temp=directory/'weights.tmp'
    with temp.open('wb') as stream:np.savez_compressed(stream,**arrays)
    os.replace(temp,directory/'weights.npz')
    payload={**payload,'weights_sha256':file_hash(directory/'weights.npz')}
    atomic_json(directory/'model.json',payload)
    return {**payload,'model_metadata_sha256':file_hash(directory/'model.json')}


def load_model(directory,expected):
    if file_hash(directory/'model.json')!=expected['model_metadata_sha256'] or file_hash(directory/'weights.npz')!=expected['weights_sha256']:
        fail('TEMPORAL_MODEL_CORRUPTED','候选模型文件与登记hash不一致')
    payload=json.loads((directory/'model.json').read_text())
    with np.load(directory/'weights.npz',allow_pickle=False) as data:arrays={key:data[key] for key in data.files}
    if any(not np.isfinite(a).all() for a in arrays.values()):fail('TEMPORAL_MODEL_CORRUPTED','模型权重包含非法数值')
    return payload,arrays


def _projection(arrays):return {k:arrays['projection_'+k] for k in ('mean','scale','components')}
def _params(arrays,prefix):return {k.removeprefix(prefix+'_'):v for k,v in arrays.items() if k.startswith(prefix+'_')}


def predict(history,arrays,horizons):
    p=_projection(arrays);x=project(history,p);centers=arrays['centers'];current=int(assign(x[-1],centers))
    result={};probs={}
    result['persistence']=np.repeat(history[-1:,:],horizons,axis=0)
    probs['empirical']=arrays['horizon_transitions'][:,current,:]
    result['empirical']=probs['empirical']@arrays['raw_centers']
    for name in ('ridge','shuffled_ridge','causal_tcn'):
        parameters=_params(arrays,name)
        y=tcn_forward(x[None],parameters) if name=='causal_tcn' else ridge_predict(x[None],parameters)
        result[name]=reconstruct(y.reshape(horizons,-1),p)
    for name in MODELS:
        if name not in probs:
            distance=((project(result[name],p)[:,None,:]-centers[None,:,:])**2).mean(-1)
            logits=-distance;logits-=logits.max(axis=1,keepdims=True)
            exp=np.exp(logits);probs[name]=exp/exp.sum(axis=1,keepdims=True)
        if not np.isfinite(result[name]).all():fail('TEMPORAL_NUMERICAL_FAILURE','预测出现非有限数值')
    return result,probs


class Journal:
    """Append and flush predictions BEFORE the caller accesses future targets."""
    def __init__(self,path):self.stream=path.open('w',encoding='utf-8');self.sequence=0
    def append(self,kind,payload):
        self.sequence+=1;record={'sequence':self.sequence,'kind':kind,**payload}
        record['record_hash']=content_hash(record)
        self.stream.write(_json(record)+'\n');self.stream.flush()
        if kind in {'empirical_forecast','next_anonymous_state_forecast'}:os.fsync(self.stream.fileno())
        return record['record_hash']
    def close(self):self.stream.close()


def replay(sequences,windows,arrays,learning,directory,split):
    p=_projection(arrays);centers=arrays['centers'];stats={n:[] for n in MODELS};per_source=defaultdict(lambda:defaultdict(list))
    journal=Journal(directory/f'{split}-predictions.jsonl')
    try:
        for w in windows:
            seq=sequences[w.sequence];history=values(seq,w.history)
            predictions,probabilities=predict(history,arrays,len(w.targets))
            refs=[seq.batches[i] for i in w.targets]
            record={'source_hash':seq.manifest['source_hash'],'episode_id':seq.batches[0].episode_id,
                    'time_domain_id':seq.batches[0].time_domain_id,'input_max_pts_ms':max(seq.batches[i].pts_end_ms for i in w.history),
                    'issued_at_pts_ms':w.issued_ms,'target_windows':[[b.pts_start_ms,b.pts_end_ms] for b in refs],
                    'requested_horizons_seconds':learning.forecast_horizons_seconds,
                    'actual_horizons_seconds':[(b.pts_start_ms-w.issued_ms)/1000 for b in refs],
                    'prediction_vectors':{n:y.tolist() for n,y in predictions.items()},
                    'anonymous_state_probabilities':{n:y.tolist() for n,y in probabilities.items()}}
            prediction_hash=journal.append('empirical_forecast',record)
            # Target vectors are not passed to predict(), nor read before its log.
            target=values(seq,w.targets);target_ids=assign(project(target,p),centers)
            metrics={}
            for name in MODELS:
                output=predictions[name];delta=(output-target)/p['scale']
                mse=np.mean(delta**2,axis=-1)
                denominator=np.linalg.norm(output,axis=-1)*np.linalg.norm(target,axis=-1)
                cosine=np.divide(np.sum(output*target,axis=-1),denominator,out=np.zeros(len(target)),where=denominator>1e-12)
                probabilities_ = probabilities[name]
                nll=-np.log(np.maximum(probabilities_[np.arange(len(target_ids)),target_ids],1e-12))
                onehot=np.eye(len(centers))[target_ids];brier=np.sum((probabilities_-onehot)**2,axis=-1)
                item=np.stack([mse,cosine,nll,brier],axis=-1)
                stats[name].append(item);per_source[seq.manifest['source_hash']][name].append(item)
                metrics[name]=item.tolist()
            journal.append('future_feature_observation',{'prediction_hash':prediction_hash,
                'observed_anonymous_ids':target_ids.tolist(),'target_vectors_sha':hashlib.sha256(target.tobytes()).hexdigest(),
                'available_at_pts_ms':max(b.pts_end_ms for b in refs),'metrics':metrics})
    finally:journal.close()
    names=('normalized_feature_mse','original_feature_cosine','anonymous_state_nll','anonymous_state_brier')
    summary={name:{metric:np.mean(np.stack(items),axis=0)[:,i].tolist() for i,metric in enumerate(names)} for name,items in stats.items()}
    # Sources, not correlated windows, receive equal weight in model selection.
    source_metrics={source:{name:{metric:np.mean(np.stack(items),axis=0)[:,i].tolist() for i,metric in enumerate(names)}
                    for name,items in models.items()} for source,models in per_source.items()}
    source_macro={name:float(np.mean([np.mean(v[name]['normalized_feature_mse']) for v in source_metrics.values()])) for name in MODELS}
    return {'split':split,'windows':len(windows),'independent_sources':len(source_metrics),'metrics':summary,
            'per_source':source_metrics,'source_macro_mse':source_macro,'prediction_log_sha256':file_hash(directory/f'{split}-predictions.jsonl'),
            'metric_scope':'unlabeled feature forecasting / anonymous cluster proxy; not business action accuracy; distance-softmax probabilities are uncalibrated',
            'causal_mode':'offline replay; each prediction flushed before reading its future target'}


def discovery(sequences,arrays,settings,split):
    p=_projection(arrays);centers=arrays['centers'];segments=[];uniform=[]
    for si,seq in enumerate(sequences):
        if seq.batches[0].split!=split:continue
        x=project(values(seq,range(len(seq.rows))),p);begin=0
        for i in range(1,len(x)+1):
            barrier=i<len(x) and has_barrier(seq,i-1,i,settings)
            change=i<len(x) and np.linalg.norm(x[i]-x[i-1])>float(arrays['change_threshold']) and i-begin>=settings.min_segment_windows
            if i==len(x) or barrier or change:
                rep=x[begin:i].mean(0)
                segments.append({'source_hash':seq.manifest['source_hash'],'episode_id':seq.batches[0].episode_id,
                    'time_domain_id':seq.batches[0].time_domain_id,'start_ms':seq.batches[begin].pts_start_ms,
                    'end_ms':seq.batches[i-1].pts_end_ms,'anonymous_cluster_id':f'cluster_{int(assign(rep,centers))}',
                    'status':'candidate','boundary_reason':'gap' if barrier else 'change' if change else 'episode_end'})
                begin=i
        # A true fixed-window baseline, also reset at missing rows.
        begin=0
        for i in range(1,len(x)+1):
            barrier=i<len(x) and has_barrier(seq,i-1,i,settings)
            if i==len(x) or barrier or i-begin>=settings.min_segment_windows:
                uniform.append({'source_hash':seq.manifest['source_hash'],'episode_id':seq.batches[0].episode_id,
                    'start_ms':seq.batches[begin].pts_start_ms,'end_ms':seq.batches[i-1].pts_end_ms,
                    'anonymous_cluster_id':f'cluster_{int(assign(x[begin:i].mean(0),centers))}'})
                begin=i
    return {'method':'train-threshold change points + minimum windows + train k-means',
            'segments':segments,'uniform_segments':uniform,'boundary_accuracy':None,
            'note':'No boundary truth supplied; these are anonymous candidates, not named actions.'}


def next_cluster_replay(sequences,arrays,settings,directory,split):
    """Next *different anonymous state*, distinct from fixed-horizon forecasting."""
    journal=Journal(directory/f'{split}-next-cluster.jsonl');items=[];censored=0
    try:
        for seq in sequences:
            if seq.batches[0].split!=split:continue
            for i in range(0,len(seq.rows)-1,settings.min_segment_windows):
                current=int(assign(project(values(seq,[i]),_projection(arrays))[0],arrays['centers']))
                probs=arrays['next_transitions'][current]
                issued=seq.batches[i].pts_end_ms
                prediction=journal.append('next_anonymous_state_forecast',{'source_hash':seq.manifest['source_hash'],
                    'episode_id':seq.batches[i].episode_id,'time_domain_id':seq.batches[i].time_domain_id,
                    'issued_at_pts_ms':issued,'current_cluster':current,'probabilities':probs.tolist()})
                target=None
                for j in range(i+1,len(seq.rows)):
                    if has_barrier(seq,j-1,j,settings):break
                    if seq.batches[j].pts_start_ms<=issued:continue
                    actual=int(assign(project(values(seq,[j]),_projection(arrays))[0],arrays['centers']))
                    if actual!=current:target=(j,actual);break
                if target is None:censored+=1;continue
                j,actual=target;nll=float(-np.log(max(probs[actual],1e-12)))
                items.append({'source_hash':seq.manifest['source_hash'],'nll':nll,'top1':int(np.argmax(probs)==actual)})
                journal.append('next_anonymous_state_observation',{'prediction_hash':prediction,'actual_cluster':actual,
                    'target_start_pts_ms':seq.batches[j].pts_start_ms,'available_at_pts_ms':seq.batches[j].pts_end_ms})
    finally:journal.close()
    return {'observed_transitions':len(items),'censored_windows':censored,
            'nll':float(np.mean([i['nll'] for i in items])) if items else None,
            'top1':float(np.mean([i['top1'] for i in items])) if items else None,
            'independent_sources':len({i['source_hash'] for i in items}),
            'scope':'next different anonymous cluster, not semantic event detection',
            'log_sha256':file_hash(directory/f'{split}-next-cluster.jsonl')}


def validate_windows(sequences,windows,settings,splits):
    for split in splits:
        selected=[w for w in windows if w.split==split]
        if len(selected)<settings.min_pairs_per_split:fail('TEMPORAL_INSUFFICIENT_PAIRS',f'{split}没有足够因果窗口')
        sources={sequences[w.sequence].manifest['source_hash'] for w in selected}
        minimum=getattr(settings,'min_'+('calibration' if split=='calibration' else split)+'_sources')
        if len(sources)<minimum:fail('TEMPORAL_INSUFFICIENT_SOURCES',f'{split}有效窗口来源不足')


def train(sequences,snapshot,learning,settings,directory,config_hash,knowledge_hash):
    if any(s.batches[0].split not in {'train','calibration'} for s in sequences):
        fail('TEMPORAL_SPLIT_FORBIDDEN','训练不可读取test或未分配来源')
    if not learning.enabled or not learning.freeze_foundation_models or 'temporal_head' not in learning.train_adapters:
        fail('TEMPORAL_DISABLED','配置未启用冻结特征上的独立时序学习')
    if learning.temporal_adapter not in {'ridge','causal_tcn'}:fail('TEMPORAL_ADAPTER_UNAVAILABLE','P0.3实现ridge与causal_tcn，尚未实现GRU')
    windows,rejected=window_refs(sequences,learning,settings);validate_windows(sequences,windows,settings,{'train','calibration'})
    train_windows=[w for w in windows if w.split=='train'];cal=[w for w in windows if w.split=='calibration']
    raw=np.concatenate([values(s,range(len(s.rows))) for s in sequences if s.batches[0].split=='train'])
    if not np.any(raw.std(0)>1e-6):fail('TEMPORAL_CONSTANT_FEATURES','训练特征没有可学习变化，请检查编码器或采集')
    projection,explained=fit_projection(raw,settings.projection_dim)
    z=project(raw,projection);centers=kmeans(z,settings.cluster_count,settings.cluster_iterations,settings.seed)
    ids=assign(z,centers)
    raw_centers=np.array([raw[ids==i].mean(0) if np.any(ids==i) else raw.mean(0) for i in range(len(centers))])
    x=np.stack([project(values(sequences[w.sequence],w.history),projection) for w in train_windows])
    y=np.stack([project(values(sequences[w.sequence],w.targets),projection) for w in train_windows])
    arrays={'projection_'+k:v for k,v in projection.items()};arrays.update(centers=centers,raw_centers=raw_centers)
    diffs=[];next_counts=np.full((len(centers),len(centers)),learning.dirichlet_alpha)
    for seq in sequences:
        if seq.batches[0].split!='train':continue
        points=project(values(seq,range(len(seq.rows))),projection);labels=assign(points,centers)
        for i in range(1,len(points)):
            if has_barrier(seq,i-1,i,settings):continue
            diffs.append(float(np.linalg.norm(points[i]-points[i-1])))
            if labels[i]!=labels[i-1]:next_counts[labels[i-1],labels[i]]+=1
    np.fill_diagonal(next_counts,0.)
    arrays['next_transitions']=next_counts/next_counts.sum(1,keepdims=True)
    arrays['change_threshold']=np.array(np.quantile(diffs,settings.change_quantile) if diffs else 0.)
    transitions=np.full((y.shape[1],len(centers),len(centers)),learning.dirichlet_alpha)
    for current,targets in zip(assign(x[:,-1],centers),assign(y,centers)):
        for h,target in enumerate(targets):transitions[h,current,target]+=1
    arrays['horizon_transitions']=transitions/transitions.sum(-1,keepdims=True)
    for name,target in [('ridge',y),('shuffled_ridge',y[np.random.default_rng(settings.seed).permutation(len(y))])]:
        arrays.update({name+'_'+k:v for k,v in ridge_fit(x,target,learning.ridge_l2).items()})
    tcn,training=tcn_fit(x,y,learning,settings.seed,settings.gradient_clip_norm)
    arrays.update({'causal_tcn_'+k:v for k,v in tcn.items()})
    directory.mkdir(parents=True,exist_ok=True)
    calibration=replay(sequences,cal,arrays,learning,directory,'calibration')
    # The shuffled model is a falsification control, never an activation candidate.
    selected=min(MODELS[:-1],key=lambda n:calibration['source_macro_mse'][n])
    cluster_version=content_hash({'centers':centers.tolist(),'projection':{k:v.tolist() for k,v in projection.items()},'train_sources':[e['source_hash'] for e in snapshot['entries'] if e['split']=='train']})
    payload={'protocol_version':settings.protocol_version,'engine_sha256':code_hash(),'state':'candidate','selected_model':selected,
             'selection_split':'calibration','snapshot':snapshot,'config_hash':config_hash,'knowledge_hash':knowledge_hash,
             'learning':learning.model_dump(),'settings':settings.model_dump(),'cluster_version':cluster_version,
             'numpy_version':np.__version__,'models':list(MODELS),'encoder_training':False}
    model=save_model(directory,payload,arrays)
    return {'stage':'P0.3','kind':'train','data_origin':snapshot['data_origin'],'snapshot':snapshot,'model':model,
            'calibration':calibration,'training':training,'train_pairs':len(train_windows),'rejected_windows':rejected,
            'used_training_sources':len({sequences[w.sequence].manifest['source_hash'] for w in train_windows}),
            'used_training_episodes':len({(sequences[w.sequence].manifest['source_hash'],sequences[w.sequence].batches[0].episode_id,sequences[w.sequence].batches[0].time_domain_id) for w in train_windows}),
            'projection':{'fit_split':'train','dimension':z.shape[1],'explained_variance_ratio':explained,
                'normalized_train_reconstruction_mse':float(np.mean(((raw-reconstruct(z,projection))/projection['scale'])**2))},
            'anonymous_discovery':discovery(sequences,arrays,settings,'calibration'),
            'next_anonymous_state':next_cluster_replay(sequences,arrays,settings,directory,'calibration'),
            'semantic_accuracy':None,'real_world_evidence':False,'auto_activated':False,'test_read':False}


def evaluate(sequences,snapshot,model_directory,expected_model,directory):
    from packages.contracts.models import Learning
    from packages.contracts.temporal import TemporalSettings
    if any(s.batches[0].split!='test' for s in sequences):
        fail('TEMPORAL_SPLIT_FORBIDDEN','独立评估只能读取test')
    model,arrays=load_model(model_directory,expected_model)
    if model['engine_sha256']!=code_hash():fail('TEMPORAL_ENGINE_CHANGED','算法实现已变化，请使用原代码版本评估此候选')
    if any(snapshot[k]!=model['snapshot'][k] for k in ('tenant_id','scene_id','view_id','feature_space_id','data_origin')):
        fail('TEMPORAL_SCOPE_MISMATCH','评估输入与模型作用域不一致')
    assert_evaluation_independent(snapshot, model['snapshot'])
    learning=Learning.model_validate(model['learning']);settings=TemporalSettings.model_validate(model['settings'])
    windows,rejected=window_refs(sequences,learning,settings);validate_windows(sequences,windows,settings,{'test'})
    if any(w.split!='test' for w in windows):fail('TEMPORAL_SPLIT_FORBIDDEN','独立评估只能读取test')
    directory.mkdir(parents=True,exist_ok=True)
    result=replay(sequences,windows,arrays,learning,directory,'test')
    return {'stage':'P0.3','kind':'evaluate','data_origin':snapshot['data_origin'],'snapshot':snapshot,
            'model_hash':expected_model['weights_sha256'],'selected_model':model['selected_model'],'selection_split':'calibration',
            'test':result,'rejected_windows':rejected,'anonymous_discovery':discovery(sequences,arrays,settings,'test'),
            'next_anonymous_state':next_cluster_replay(sequences,arrays,settings,directory,'test'),
            'semantic_accuracy':None,'real_world_evidence':False,'auto_activated':False,'refit_on_test':False}
