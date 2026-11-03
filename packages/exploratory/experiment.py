"""A: resubstitution, B: purged within-video holdout. Never independent sessions.

Use the production numerical primitives, not the formal admission/consumption
service. Original manifests retain unassigned splits and unknown acquisition.
"""
from dataclasses import replace
import json
from pathlib import Path
import numpy as np

from packages.config.errors import ContractError
from packages.ingestion.pipeline import file_hash
from packages.knowledge.service import content_hash
from packages.temporal import models
from packages.temporal.data import metadata_sequences, window_refs, values
from packages.encoders.pretrained import PREPROCESSING_VERSION

NAMES=('persistence','train_mean','ridge','causal_tcn','shuffled_ridge','shuffled_tcn','initial_tcn')


def save(path, obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    temp.replace(path)


def partitions(duration_ms, guard_ms, mode):
    if mode=='A':return {'train':[0,duration_ms],'validation':[0,duration_ms],'holdout':[0,duration_ms]}
    if mode!='B' or guard_ms<2000:raise ValueError('B requires at least a 2-second raw-time guard')
    a,b=int(duration_ms*.5),int(duration_ms*.75)
    return {'train':[0,a],'validation':[a+guard_ms,b],'holdout':[b+guard_ms,duration_ms]}


def within(sequences, interval):
    """Purge clips BEFORE building windows, without editing source metadata."""
    left,right=interval;result=[]
    for seq in sequences:
        selected=[i for i,b in enumerate(seq.batches) if left<=b.pts_start_ms and b.pts_end_ms<=right]
        if selected:result.append(replace(seq,batches=[seq.batches[i] for i in selected],rows=[seq.rows[i] for i in selected]))
    return result


def tensors(sequences, windows):
    return (np.stack([values(sequences[w.sequence],w.history) for w in windows]),
            np.stack([values(sequences[w.sequence],w.targets) for w in windows]))


def metrics(prediction,target,scale):
    delta=prediction-target
    norms=np.linalg.norm(prediction,axis=-1)*np.linalg.norm(target,axis=-1)
    cosine=np.divide((prediction*target).sum(-1),norms,out=np.zeros_like(norms),where=norms>1e-12)
    return {'feature_mse':np.mean(delta**2,axis=(0,2)).tolist(),
            'normalized_feature_mse':np.mean((delta/scale)**2,axis=(0,2)).tolist(),
            'feature_cosine':cosine.mean(0).tolist()}


def load_source(directory,asset,bundle,model_sha):
    m=json.loads((directory/'manifest.json').read_text())
    if (m.get('split')!='unassigned' or m.get('data_origin')!='real_video'
            or m.get('source_hash')!=asset['sha256'] or m.get('tenant_id')!=bundle.profile.tenant_id
            or m.get('scene_id')!='anonymous_observation'):
        raise ContractError('EXPLORATORY_SCOPE','Only allowlisted unassigned development recordings are allowed; never sealed/test sources')
    evidence=m.get('encoder_evidence',{})
    if (evidence.get('weights_sha')!=model_sha or evidence.get('execution')!='frozen_onnx_cpu'
            or evidence.get('vector_production')!='executed_from_this_source'
            or m['feature_space']['dimension']!=512 or m['feature_space']['encoder_sha']!=model_sha):
        raise ContractError('EXPLORATORY_ENCODER','Real frozen encoder evidence must match')
    sampling=content_hash({'clip_ms':round(bundle.config.video.clip_seconds*1000),
        'stride_ms':round(bundle.config.video.stride_seconds*1000),'frames_per_clip':bundle.config.video.frames_per_clip,'method':'nearest_original_pts_v1'})
    if sampling!=m['feature_space']['sampling_sha']:
        raise ContractError('EXPLORATORY_SAMPLING','Stale sampling cache: re-extract, never relabel timestamps')
    if (content_hash(m['feature_space'])!=m['feature_space_id']
            or content_hash(m['preprocessing'])!=m['feature_space']['preprocessing_sha']
            or m['preprocessing'].get('version')!=PREPROCESSING_VERSION):
        raise ContractError('EXPLORATORY_RECIPE','Feature identity or preprocessing recipe differs')
    path=directory/'vectors.npy'
    if file_hash(path)!=m['feature_cache']['sha256']:raise ContractError('EXPLORATORY_CACHE','Cache bytes differ')
    vectors=np.load(path,allow_pickle=False,mmap_mode='r')
    if vectors.shape!=(m['total_rows'],512) or vectors.dtype.kind!='f':raise ValueError('Invalid real features')
    seqs=metadata_sequences(m,bundle.config.temporal)
    for seq in seqs:seq.vectors=vectors
    return m,seqs


def run(directory,asset,bundle,options,out,model_sha):
    manifest,seqs=load_source(directory,asset,bundle,model_sha)
    duration=max(d['end_ms'] for d in manifest['domains'])
    learning=bundle.config.learning.model_copy(update=options['learning'])
    settings=bundle.config.temporal
    result={'asset_id':asset['asset_id'],'source_sha256':asset['sha256'],'domain':asset['domain'],
        'station_label':asset['station_label'],'feature_space_id':manifest['feature_space_id'],
        'feature_cache_sha256':manifest['feature_cache']['sha256'],'manifest_sha256':file_hash(directory/'manifest.json'),
        'rows':manifest['eligible_rows'],'duration_ms':duration,'source_split':'unassigned',
        'acquisition':manifest.get('acquisition'),'capture_identity':manifest.get('capture_identity'),
        'evidence_class':'retrospective_development','formal_status':'blocked',
        'independent_sessions_verified':0,'confidence_intervals':None,'semantic_accuracy':None,
        'learning':learning.model_dump(mode='json'),'seed':settings.seed,'modes':{},
        'C':{'status':'not_executed','reason':'No verified independent comparable sources; stations differ, garment relation unverified'},
        'auto_activated':False,'formal_database_accessed':False}
    for mode in ('A','B'):
        output=out/mode;output.mkdir(parents=True,exist_ok=True)
        if mode=='B' and (len(manifest['domains'])!=1 or manifest['domains'][0]['start_ms']!=0):
            result['modes'][mode]={'status':'blocked','reason':'ambiguous raw chronology; no automatic PTS reset concatenation',
                'intervals_ms':{},'counts':{},'shared_rows':{}}
            save(output/'report.json',result['modes'][mode]);continue
        spans=partitions(duration,options['guard_ms'],mode)
        pools={k:within(seqs,v) for k,v in spans.items()}
        windows={};rejected={}
        for key,pool in pools.items():windows[key],rejected[key]=window_refs(pool,learning,settings)
        summary={'status':'blocked','intervals_ms':spans,'guard_ms':options['guard_ms'] if mode=='B' else 0,
            'counts':{k:len(v) for k,v in windows.items()},'rejected':rejected,
            'evidence':'resubstitution_only' if mode=='A' else 'within_video_purged_holdout',
            'shared_rows':{},'independent_sessions':0}
        row_sets={k:{(s.manifest['source_hash'],r) for s in pool for r in s.rows} for k,pool in pools.items()}
        summary['shared_rows']={a+'_'+b:len(row_sets[a]&row_sets[b]) for a,b in [('train','validation'),('train','holdout'),('validation','holdout')]}
        if mode=='B' and any(summary['shared_rows'].values()):raise AssertionError('Partition feature overlap')
        if any(len(w)<options['minimum_windows'] for w in windows.values()):
            summary['reason']='insufficient_time_coverage_after_purge; policy not relaxed'
            result['modes'][mode]=summary;save(output/'report.json',summary);continue
        raw=np.concatenate([values(s,range(len(s.rows))) for s in pools['train']])
        projection,explained=models.fit_projection(raw,settings.projection_dim)
        x,y=tensors(pools['train'],windows['train']);px=models.project(x,projection);py=models.project(y,projection)
        parameters={}
        permutation=np.random.default_rng(settings.seed).permutation(len(y))
        parameters['ridge']=models.ridge_fit(px,py,learning.ridge_l2)
        parameters['shuffled_ridge']=models.ridge_fit(px,py[permutation],learning.ridge_l2)
        tcn,train_log=models.tcn_fit(px,py,learning,settings.seed,settings.gradient_clip_norm)
        shuffled,negative_log=models.tcn_fit(px,py[permutation],learning,settings.seed,settings.gradient_clip_norm)
        initial=models.tcn_initialize(px.shape[-1],learning.hidden_dim,py.shape[1]*py.shape[2],settings.seed)
        parameters.update(causal_tcn=tcn,shuffled_tcn=shuffled,initial_tcn=initial)
        arrays={'projection_'+k:v for k,v in projection.items()}
        for name,params in parameters.items():arrays.update({name+'_'+k:v for k,v in params.items()})
        weight=output/'weights.npz';np.savez_compressed(weight,**arrays)
        with np.load(weight,allow_pickle=False) as reloaded:
            assert all(np.array_equal(reloaded[k],v) for k,v in arrays.items())
        summary.update(status='executed',projection_explained_variance=explained,
            training=train_log,shuffled_training=negative_log,weights_sha256=file_hash(weight),weight_reload_exact=True,
            metrics={},comparisons={},prediction_traces={},timeline=[])
        for split in ('train','validation','holdout'):
            if split=='holdout':
                summary['selected_by_validation']=min(('persistence','train_mean','ridge','causal_tcn'),key=lambda n:np.mean(summary['metrics']['validation'][n]['feature_mse']))
                save(output/'selection-before-holdout.json',{'selected_model':summary['selected_by_validation'],
                    'weights_sha256':summary['weights_sha256'],'selection_split':'validation','holdout_metrics_used':False})
            hx,target=tensors(pools[split],windows[split])
            zz=models.project(hx,projection)
            predictions={'persistence':np.repeat(hx[:,-1:, :],target.shape[1],axis=1),
                         'train_mean':np.broadcast_to(projection['mean'],target.shape).copy()}
            for name,params in parameters.items():
                latent=models.tcn_forward(zz,params) if 'tcn' in name else models.ridge_predict(zz,params)
                predictions[name]=models.reconstruct(latent.reshape(len(hx),target.shape[1],-1),projection)
            summary['metrics'][split]={n:metrics(v,target,projection['scale']) for n,v in predictions.items()}
            summary['comparisons'][split]={n:{base:[None if abs(b)<1e-12 else (b-c)/b for b,c in zip(summary['metrics'][split][base]['feature_mse'],summary['metrics'][split][n]['feature_mse'])] for base in ('persistence','ridge')} for n in NAMES}
            logs=[]
            for i,w in enumerate(windows[split]):
                seq=pools[split][w.sequence]
                refs=[seq.batches[j] for j in w.targets]
                assert all(w.issued_ms<b.pts_start_ms for b in refs)
                logs.append({'issued_ms':w.issued_ms,'target_ms':[[b.pts_start_ms,b.pts_end_ms] for b in refs],
                    'history_ms':[[seq.batches[j].pts_start_ms,seq.batches[j].pts_end_ms] for j in w.history],
                    'mse':{n:np.mean((v[i]-target[i])**2,axis=-1).tolist() for n,v in predictions.items()},
                    'target_l2':np.linalg.norm(target[i],axis=-1).tolist(),
                    'prediction_l2':{n:np.linalg.norm(v[i],axis=-1).tolist() for n,v in predictions.items()}})
            summary['prediction_traces'][split]=logs
            np.savez_compressed(output/f'{split}-predictions.npz',target=target,**predictions)
        # Anonymous proxy timeline fitted solely on the training partition.
        centers=models.kmeans(models.project(raw,projection),settings.cluster_count,settings.cluster_iterations,settings.seed)
        for seq in seqs:
            ids=models.assign(models.project(values(seq,range(len(seq.rows))),projection),centers)
            summary['timeline'].extend({'start_ms':b.pts_start_ms,'end_ms':b.pts_end_ms,'anonymous_cluster':int(c)} for b,c in zip(seq.batches,ids))
        summary['timeline_scope']='Train-only k-means proxy; no semantic state truth or NLL claim'
        result['modes'][mode]=summary;save(output/'report.json',summary)
        save(out/'report.json',result)
    save(out/'report.json',result)
    return result
