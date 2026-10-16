"""Runnable synthetic-feature experiment. No camera/model/API calls occur."""
import argparse
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
import numpy as np
from config_doctor import load_bundle,resolve_path
from learning_core import RidgePredictor,MemoryStore,validate_manifest

def generate(cfg,actions):
    d=cfg.demo;rng=np.random.default_rng(d.seed);step_ms=round(cfg.video.clip_seconds*1000)
    q,_=np.linalg.qr(rng.normal(size=(d.feature_dim,len(actions))))
    centers=q.T
    result={};manifest=[]
    for split,n in [('train',d.train_episodes),('test',d.test_episodes)]:
        episodes=[]
        for i in range(n):
            episode=f'{split}_{i:04d}'
            order=list(range(len(actions)))
            # Deliberate atypical path: observed empirical behavior is not an SOP edit.
            if rng.random()<d.skip_branch_probability: order.remove(2)
            vec=centers[order]+rng.normal(0,d.noise_std,size=(len(order),d.feature_dim))
            labels=[actions[j] for j in order]
            episodes.append((episode,vec,labels))
            digest=hashlib.sha256(vec.tobytes()).hexdigest()
            for j in range(len(vec)):
                manifest.append({'sample_id':f'{episode}_{j}','episode_id':episode,'source_video_id':episode,'source_hash':digest,'split':split,'start_ms':j*step_ms,'end_ms':(j+1)*step_ms,'data_origin':'synthetic_features'})
        result[split]=episodes
    validate_manifest(manifest)
    return result,manifest

def pairs(episodes):
    return np.concatenate([v[:-1] for _,v,_ in episodes]),np.concatenate([v[1:] for _,v,_ in episodes])

def run(config_path):
    cfg,kn,root=load_bundle(config_path)
    if cfg.profiles[cfg.active_profile].mode!='demo': raise ValueError('DEMO_ONLY: no live data/model changes')
    ids=[n.action_id for n in kn.sop.nodes]
    if len(ids)>cfg.demo.feature_dim: raise ValueError('demo dimension must cover demo classes')
    source,manifest=generate(cfg,ids)
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid4().hex[:8]
    out=resolve_path(root,cfg.profiles[cfg.active_profile].data_root)/cfg.paths.demo_output/run_id
    out.mkdir(parents=True,exist_ok=False)
    (out/'dataset_manifest.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in manifest),encoding='utf-8')
    x_train,y_train=pairs(source['train']);x_test,y_test=pairs(source['test'])
    head=RidgePredictor(cfg.demo.ridge_l2).fit(x_train,y_train)
    np.savez(out/'candidate_head.npz',weights=head.weights)
    np.savez(out/'champion_persistence.npz',weights=np.vstack([np.eye(cfg.demo.feature_dim),np.zeros(cfg.demo.feature_dim)]))
    space='synthetic-demo-frozen-v1'
    mem=MemoryStore(out/'memory.sqlite',ids,space,cfg.learning.dirichlet_alpha,cfg.video.quality_min)
    log=[];step_ms=round(cfg.video.clip_seconds*1000)
    for ep,vec,labels in source['train']:
        for j,label in enumerate(labels):
            if j>0:
                probs=mem.probabilities(labels[j-1])
                # Prediction is built before observe(); no future label is supplied to it.
                log.append({'type':'prediction','prediction_id':f'{ep}_{j}','issued_at_pts_ms':j*step_ms,'input_max_pts_ms':j*step_ms-1,'target_start_pts_ms':j*step_ms,'target_end_pts_ms':(j+1)*step_ms,'distribution':probs,'data_origin':'synthetic_features'})
            status=mem.observe(event_id=f'{ep}_{j}',episode=ep,seq=j,action=label,vector=vec[j],quality=1.0,source='synthetic_truth',evidence=f'synthetic:{ep}:{j}',pts_ms=(j+1)*step_ms,space_id=space,split='train')
            if j>0:
                log.append({'type':'outcome','prediction_id':f'{ep}_{j}','available_at_pts_ms':(j+1)*step_ms,'actual_action':label,'source':'independent_synthetic_truth','memory_result':status,'data_origin':'synthetic_features'})
    nll=[];correct=[]
    for ep,vec,labels in source['test']:
        for current,nxt in zip(labels[:-1],labels[1:]):
            p=mem.probabilities(current);nll.append(-np.log(p[nxt]));correct.append(max(p,key=p.get)==nxt)
    transitions=[{'from':r[0],'to':r[1],'count':r[2]} for r in mem.db.execute('SELECT src,dst,n FROM transitions ORDER BY src,dst')]
    proto_count=mem.db.execute('SELECT COUNT(*) FROM prototypes').fetchone()[0]
    mem.close()
    (out/'prediction_log.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in log),encoding='utf-8')
    report={'scope':'synthetic_features_only','real_video_evaluation':False,'production_deployment':False,'train_episodes':len(source['train']),'test_episodes':len(source['test']),
      'future_feature_prediction':{'baseline':'persistence','candidate':'ridge_head','train_pairs':len(x_train),'test_pairs':len(x_test),'baseline_mse':float(np.mean((x_test-y_test)**2)),'candidate_mse':float(np.mean((head.predict(x_test)-y_test)**2))},
      'next_event_prediction':{'baseline':'uniform','baseline_nll':float(np.log(len(ids))),'candidate_nll':float(np.mean(nll)),'candidate_top1':float(np.mean(correct))},
      'candidate_memory':{'prototypes':proto_count,'transitions':transitions,'activation':'not_performed'},
      'knowledge_sha256':hashlib.sha256(resolve_path(root,cfg.knowledge_file).read_bytes()).hexdigest(),
      'limitations':['No video decoding or encoder is implemented here.','Synthetic event semantics are provided by the independent generator.','The ridge head uses only training feature pairs; holdout data never updates memory.','This experiment cannot validate industrial accuracy or annotation savings.']}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(out),'report':report},ensure_ascii=False,indent=2))
    return out,report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--config',required=True);args=ap.parse_args();run(args.config)
