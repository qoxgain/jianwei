"""Small reference mechanisms, not a production video learning service."""
import json
import math
import sqlite3
from pathlib import Path
import numpy as np

class RidgePredictor:
    """Fit only a small future-feature head; the supplied features stay frozen."""
    def __init__(self,l2):
        self.l2=float(l2);self.weights=None
    def fit(self,x,y):
        x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float)
        if x.ndim!=2 or y.ndim!=2 or len(x)!=len(y) or not len(x): raise ValueError('shape')
        if not np.isfinite(x).all() or not np.isfinite(y).all(): raise ValueError('nonfinite')
        a=np.column_stack([x,np.ones(len(x))]);reg=np.eye(a.shape[1])*self.l2;reg[-1,-1]=0
        self.weights=np.linalg.solve(a.T@a+reg,a.T@y)
        return self
    def predict(self,x):
        if self.weights is None: raise ValueError('not fitted')
        a=np.atleast_2d(np.asarray(x,dtype=float))
        return np.column_stack([a,np.ones(len(a))])@self.weights

class MemoryStore:
    """Single-process SQLite candidate memory, with causal observations and dedupe.

    This deliberately does not implement production activation or concurrent leases.
    """
    def __init__(self,path,actions,space_id,alpha,quality_min):
        self.actions=list(actions);self.space_id=space_id;self.alpha=float(alpha);self.quality_min=quality_min
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, episode TEXT NOT NULL,
          seq INTEGER NOT NULL, action TEXT, accepted INTEGER NOT NULL, reason TEXT NOT NULL,
          evidence TEXT NOT NULL, pts_ms INTEGER NOT NULL, UNIQUE(episode,seq));
        CREATE TABLE IF NOT EXISTS transitions(src TEXT,dst TEXT,n INTEGER NOT NULL,PRIMARY KEY(src,dst));
        CREATE TABLE IF NOT EXISTS prototypes(action TEXT PRIMARY KEY,n INTEGER NOT NULL,sum_json TEXT NOT NULL);
        ''')
        signature=json.dumps({'space':space_id,'actions':self.actions},sort_keys=True)
        old=self.db.execute("SELECT value FROM metadata WHERE key='signature'").fetchone()
        if old and old[0]!=signature: raise ValueError('FEATURE_OR_ONTOLOGY_MISMATCH')
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES('signature',?)",(signature,));self.db.commit()
    def probabilities(self,action):
        counts={k:self.alpha for k in self.actions}
        for dst,n in self.db.execute('SELECT dst,n FROM transitions WHERE src=?',(action,)):
            counts[dst]+=n
        total=sum(counts.values());return {k:v/total for k,v in counts.items()}
    def retrieve(self,vector,space_id,top_k=3):
        if space_id!=self.space_id: raise ValueError('FEATURE_SPACE_MISMATCH')
        v=np.asarray(vector,dtype=float)
        if v.ndim!=1 or not np.isfinite(v).all() or np.linalg.norm(v)==0: raise ValueError('BAD_VECTOR')
        results=[]
        for action,n,total in self.db.execute('SELECT action,n,sum_json FROM prototypes'):
            center=np.asarray(json.loads(total))/n
            if center.shape!=v.shape: raise ValueError('VECTOR_DIM_MISMATCH')
            denom=np.linalg.norm(v)*np.linalg.norm(center)
            similarity=float(v@center/denom) if denom else 0.
            results.append({'action_id':action,'cosine_similarity':similarity,'samples':n,'status':'candidate_only'})
        return sorted(results,key=lambda r:r['cosine_similarity'],reverse=True)[:top_k]
    def observe(self,*,event_id,episode,seq,action,vector,quality,source,evidence,pts_ms,space_id,split):
        if space_id!=self.space_id: raise ValueError('FEATURE_SPACE_MISMATCH')
        if split!='train': raise ValueError('HOLDOUT_CANNOT_ENTER_MEMORY')
        v=np.asarray(vector,dtype=float)
        if v.ndim!=1 or not np.isfinite(v).all(): raise ValueError('BAD_VECTOR')
        ok=(action in self.actions and quality>=self.quality_min and source in ['human','independent_visual','synthetic_truth'] and bool(evidence))
        reason='accepted' if ok else 'quarantined'
        with self.db:
            if self.db.execute('SELECT 1 FROM observations WHERE id=?',(event_id,)).fetchone(): return 'duplicate'
            prev=self.db.execute('SELECT seq,action,accepted,pts_ms FROM observations WHERE episode=? ORDER BY seq DESC LIMIT 1',(episode,)).fetchone()
            if prev and (seq<=prev[0] or pts_ms<=prev[3]): raise ValueError('OUT_OF_ORDER_REQUIRES_REPLAY')
            self.db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)',(event_id,episode,seq,action,int(ok),reason,evidence,pts_ms))
            if ok:
                row=self.db.execute('SELECT n,sum_json FROM prototypes WHERE action=?',(action,)).fetchone()
                if row:
                    prior=np.asarray(json.loads(row[1]));
                    if prior.shape!=v.shape: raise ValueError('VECTOR_DIM_MISMATCH')
                    n=row[0]+1;total=prior+v
                else: n=1;total=v
                self.db.execute('INSERT INTO prototypes VALUES(?,?,?) ON CONFLICT(action) DO UPDATE SET n=excluded.n,sum_json=excluded.sum_json',(action,n,json.dumps(total.tolist())))
                if prev and prev[2] and seq==prev[0]+1:
                    self.db.execute('INSERT INTO transitions VALUES(?,?,1) ON CONFLICT(src,dst) DO UPDATE SET n=n+1',(prev[1],action))
        return reason
    def close(self): self.db.close()

def validate_manifest(rows):
    """Reject shared source/episode/hash across train/calibration/dev/test splits."""
    groups={};sample_ids=set()
    allowed={'train','calibration','dev','test'}
    for r in rows:
        required={'sample_id','episode_id','source_video_id','source_hash','split','start_ms','end_ms','data_origin'}
        if not required<=set(r): raise ValueError('MANIFEST_FIELDS_MISSING')
        if r['sample_id'] in sample_ids: raise ValueError('DUPLICATE_SAMPLE')
        sample_ids.add(r['sample_id'])
        if r['split'] not in allowed or r['start_ms']<0 or r['end_ms']<=r['start_ms']: raise ValueError('INVALID_RANGE_OR_SPLIT')
        for k in ['episode_id','source_video_id','source_hash']:
            if not r[k]: raise ValueError('EMPTY_GROUP_KEY')
            key=(k,r[k]);previous=groups.setdefault(key,r['split'])
            if previous!=r['split']: raise ValueError(f'SPLIT_LEAKAGE:{k}')
    return {'samples':len(rows),'groups':len(groups)}

def compare_versions(champion,candidate,policy):
    """Offline classification gate only. Never deploys a model."""
    reasons=[]
    required={'macro_f1','per_class_f1','per_class_gold_n','coverage','false_alarms_per_hour','p95_ms','gain_ci_lower','dataset_hash','protocol_id'}
    if not required<=set(candidate) or not required<=set(champion):
        return {'passed':False,'reasons':['METRICS_MISSING'],'deployment':'not_implemented'}
    for data in [champion,candidate]:
        scalar=[data[k] for k in required if k not in ['per_class_f1','per_class_gold_n','dataset_hash','protocol_id']]
        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in scalar): reasons.append('INVALID_METRICS')
        if any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in data['per_class_f1'].values()): reasons.append('INVALID_CLASS_METRIC')
        if not (0<=data['macro_f1']<=1 and 0<=data['coverage']<=1 and data['false_alarms_per_hour']>=0 and data['p95_ms']>=0): reasons.append('METRIC_RANGE')
    if champion['dataset_hash']!=candidate['dataset_hash'] or champion['protocol_id']!=candidate['protocol_id']:
        reasons.append('INCOMPARABLE_PROTOCOL_OR_DATA')
    if reasons: return {'passed':False,'reasons':reasons,'deployment':'not_implemented'}
    if candidate['macro_f1']-champion['macro_f1']<policy.required_macro_f1_gain: reasons.append('GAIN_TOO_SMALL')
    if candidate['gain_ci_lower']<policy.gain_ci_lower_min: reasons.append('UNCERTAIN_GAIN')
    # Per-class checks protect rare old classes even if global mean improves.
    for cls,old in champion['per_class_f1'].items():
        if cls not in candidate['per_class_f1']: reasons.append('OLD_CLASS_MISSING:'+cls);continue
        if old-candidate['per_class_f1'][cls]>policy.max_old_class_f1_drop: reasons.append('OLD_CLASS_REGRESSION:'+cls)
        if min(candidate['per_class_gold_n'].get(cls,0),champion['per_class_gold_n'].get(cls,0))<policy.minimum_gold_events_per_class: reasons.append('INSUFFICIENT_GOLD:'+cls)
    for cls in candidate['per_class_f1']:
        n=candidate['per_class_gold_n'].get(cls,0)
        if n<policy.minimum_gold_events_per_class: reasons.append('INSUFFICIENT_GOLD:'+cls)
    old_ids=set(champion['per_class_f1'])
    if old_ids and old_ids<=set(candidate['per_class_f1']):
        drop=sum(champion['per_class_f1'][c]-candidate['per_class_f1'][c] for c in old_ids)/len(old_ids)
        if drop>policy.max_old_macro_f1_drop: reasons.append('OLD_MACRO_REGRESSION')
    if champion['coverage']-candidate['coverage']>policy.max_coverage_drop: reasons.append('COVERAGE_REGRESSION')
    if candidate['false_alarms_per_hour']>champion['false_alarms_per_hour']*policy.max_false_alarm_ratio: reasons.append('FALSE_ALARM_REGRESSION')
    if candidate['p95_ms']>policy.online_p95_ms_budget: reasons.append('LATENCY_OVER_BUDGET')
    return {'passed':not reasons,'reasons':reasons,'deployment':'not_implemented'}
