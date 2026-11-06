"""Support-purged forecasting proxies. Encoder and all fit statistics frozen."""
import numpy as np
from packages.temporal.models import ridge_fit,ridge_predict
from .algorithms import descriptor


def pairs(z,starts,ends,history,lag):
    xs=[];ys=[];indices=[]
    for i in range(history-1,len(z)-lag):
        if starts[i+lag]<ends[i]:continue
        xs.append(z[i-history+1:i+1]);ys.append(z[i+lag]);indices.append(i)
    return np.asarray(xs).reshape(-1,history,z.shape[1]),np.asarray(ys).reshape(-1,z.shape[1]),indices


def fit_forecasts(z,starts,ends,s,prototypes=(),activity_floor=0.):
    fitted={}
    for lag in s.forecast_lags:
        x,y,indices=pairs(z,starts,ends,s.forecast_history,lag)
        if len(x)<3:
            fitted[lag]={'status':'insufficient_disjoint_training_pairs'};continue
        ridge=ridge_fit(x,y,s.ridge_lambda);shuffled={}
        for seed in s.seeds:
            order=np.random.default_rng(seed).permutation(len(y));shuffled[seed]=ridge_fit(x,y[order],s.ridge_lambda)
        flat=x.reshape(len(x),-1);distance=np.linalg.norm(flat[:,None]-flat[None,:],axis=-1)
        # Do not calibrate retrieval radius on identical/overlapping raw frames.
        for a,i in enumerate(indices):
            for b,j in enumerate(indices):
                if starts[i-s.forecast_history+1]<ends[j] and starts[j-s.forecast_history+1]<ends[i]:distance[a,b]=np.inf
        nearest=distance.min(1);finite=nearest[np.isfinite(nearest)]
        limit=float(np.quantile(finite,.75)) if len(finite) else 0.
        for p in prototypes:
            deltas=[]
            for member in p['members']:
                end=p['records'][member]['row_end']-1;target=end+lag
                if target<len(z) and starts[target]>=ends[end]:deltas.append(z[target]-z[end])
            p.setdefault('next_deltas',{})[lag]=np.mean(deltas,axis=0) if len(deltas)>=2 else None
            p.setdefault('next_delta_support',{})[lag]=len(deltas)
        fitted[lag]={'status':'fitted','ridge':ridge,'shuffled_ridge':shuffled,'history':x,'target':y,'radius':limit,'fit_mean':z.mean(0),
            'train_delta_mean':(y-x[:,-1]).mean(0),'prototypes':list(prototypes),'activity_floor':activity_floor}
    return fitted


def forecast_metrics(prediction,target,persistence_mse):
    mse=float(np.mean((prediction-target)**2));relative=1-mse/persistence_mse if persistence_mse>1e-12 else None
    return {'normalized_latent_mse':mse,'gain_vs_persistence':relative,'real_action_accuracy':None}


def score_forecasts(z,starts,ends,s,fitted,activity=None):
    results=[]
    for lag in s.forecast_lags:
        f=fitted[lag];x,y,indices=pairs(z,starts,ends,s.forecast_history,lag)
        if f['status']!='fitted' or not len(x):results.append({'lag_windows':lag,'status':'blocked','reason':'NO_SUPPORT_DISJOINT_FORECAST_PAIRS'});continue
        current=x[:,-1];persistence=float(np.mean((current-y)**2));train=np.broadcast_to(f['fit_mean'],y.shape)
        methods={'persistence':current,'train_mean':train,'fixed_history_mean':x.mean(1),'ridge':ridge_predict(x,f['ridge']),
            'train_delta_mean':current+f['train_delta_mean']}
        flat=f['history'].reshape(len(f['history']),-1);retrieval=[];matched=0
        for h in x:
            distance=np.linalg.norm(flat-h.ravel(),axis=1);ids=np.argsort(distance,kind='stable')[:s.retrieval_k]
            if not len(ids) or distance[ids[0]]>f['radius']:
                retrieval.append(h[-1]);continue
            matched+=1;weights=1/np.maximum(distance[ids],1e-6);weights/=weights.sum()
            delta=f['target'][ids]-f['history'][ids,-1]
            retrieval.append(h[-1]+(weights[:,None]*delta).sum(0))
        methods['motif_history_retrieval']=np.asarray(retrieval)
        proto_predictions=[];proto_matches=0
        for row,(h,origin) in enumerate(zip(x,indices)):
            choices=[]
            for p in f['prototypes']:
                m=p['length'];delta=p['next_deltas'].get(lag)
                if delta is None or len(h)<m:continue
                sequence=h[-m:]
                if float(np.sqrt(np.mean((sequence-sequence.mean(0))**2)))<s.min_shape_rms:continue
                if activity is not None and float(activity[origin-m+1:origin+1].mean())<f['activity_floor']:continue
                distance=float(np.linalg.norm(descriptor(sequence,s.context_weight)-p['descriptor']))
                if distance<=p['radius']:choices.append((distance/max(p['radius'],1e-9),p['id'],delta))
            if choices:
                choices.sort(key=lambda v:(v[0],v[1]));proto_predictions.append(h[-1]+choices[0][2]);proto_matches+=1
            else:proto_predictions.append(h[-1])
        methods['prototype_continuation']=np.asarray(proto_predictions)
        shuffled=[{'seed':seed,**forecast_metrics(ridge_predict(x,p),y,persistence)} for seed,p in f['shuffled_ridge'].items()]
        metrics={name:forecast_metrics(value,y,persistence) for name,value in methods.items()}
        results.append({'status':'computed','lag_windows':lag,'pairs':len(x),'history_windows':s.forecast_history,
            'target_original_support_overlaps_observed':False,'forecast_origin':'last input original support end, not its center',
            'methods':metrics,'shuffled_target_ridge_all_seeds':shuffled,'retrieval_coverage':matched/len(x),'prototype_forecast_coverage':proto_matches/len(x),
            'interpretation':'within-representation proxy ratios; MSE across feature spaces is not an accuracy ranking'})
    return results
