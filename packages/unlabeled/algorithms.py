"""Bounded exact profiles and offline segmentation; no semantic labels."""
import numpy as np


def contiguous(starts,ends,begin,count,stride):
    return count>0 and begin+count<=len(starts) and np.all(np.diff(starts[begin:begin+count])==stride)


def descriptor(sequence,context_weight):
    mean=sequence.mean(0);shape=sequence-mean
    return np.r_[shape.ravel()/np.sqrt(shape.size),context_weight*mean/np.sqrt(len(mean))]


def subsequences(z,starts,ends,activity,length,shape_floor,activity_floor,context_weight):
    stride=int(np.median(np.diff(starts))) if len(starts)>1 else 0;records=[];vectors=[]
    for i in range(len(z)-length+1):
        if not contiguous(starts,ends,i,length,stride):continue
        window=z[i:i+length];shape_rms=float(np.sqrt(np.mean((window-window.mean(0))**2)))
        if shape_rms<shape_floor or float(activity[i:i+length].mean())<activity_floor:continue
        records.append({'row_start':i,'row_end':i+length,'start_ms':int(starts[i]),'end_ms':int(ends[i+length-1]),'shape_rms':shape_rms})
        vectors.append(descriptor(window,context_weight))
    dimension=length*z.shape[1]+z.shape[1]
    return records,np.asarray(vectors).reshape(-1,dimension)


def exact_profile(vectors,records):
    """Multivariate L2 self-join; exclude ALL overlapping original support."""
    n=len(records);distance=np.full((n,n),np.inf)
    for i in range(n):
        for j in range(i):
            a,b=records[i],records[j]
            if a['start_ms']<b['end_ms'] and b['start_ms']<a['end_ms']:continue
            distance[i,j]=distance[j,i]=float(np.linalg.norm(vectors[i]-vectors[j]))
    nearest=np.argmin(distance,axis=1) if n else np.array([],dtype=int)
    profile=distance[np.arange(n),nearest] if n else np.array([])
    return profile,nearest,distance


def nonoverlapping(indices,records):
    selected=[];end=-np.inf
    for i in sorted(indices,key=lambda j:(records[j]['end_ms'],records[j]['start_ms'])):
        if records[i]['start_ms']>=end:selected.append(i);end=records[i]['end_ms']
    return selected


def discover(z,starts,ends,activity,s,activity_floor):
    pool=[];profiles=[]
    for length in s.motif_lengths:
        records,vec=subsequences(z,starts,ends,activity,length,s.min_shape_rms,activity_floor,s.context_weight)
        profile,nearest,distance=exact_profile(vec,records);finite=profile[np.isfinite(profile)]
        if not len(finite):profiles.append({'length':length,'eligible_subsequences':len(records),'status':'no_nonoverlapping_dynamic_pairs'});continue
        radius=max(2*s.noise_sigma,float(np.quantile(finite,s.motif_neighbor_quantile))*s.motif_radius_factor)
        all_pairs=distance[np.isfinite(distance)];typical=float(np.median(all_pairs))
        for seed in np.argsort(profile,kind='stable'):
            if not np.isfinite(profile[seed]):continue
            d=np.linalg.norm(vec-vec[seed],axis=1);members=nonoverlapping(np.flatnonzero(d<=radius),records)
            if len(members)<s.min_repeats:continue
            if any(set(members)==set(p['members']) for p in pool if p['length']==length):continue
            within=np.linalg.norm(vec[members,None,:]-vec[None,members,:],axis=-1)
            medoid=members[int(within.sum(1).argmin())];mean_distance=float(np.linalg.norm(vec[members]-vec[medoid],axis=1).mean())
            separation=max(0.,1-mean_distance/max(typical,1e-9))
            pool.append({'length':length,'members':members,'records':records,'medoid':medoid,'descriptor':vec[medoid].copy(),
                'radius':radius,'mean_member_distance':mean_distance,'separation':separation,'support_count':len(members),
                'strength':separation*len(members)/max(1,len(z)/length),'trajectory':z[records[medoid]['row_start']:records[medoid]['row_end']].copy()})
        profiles.append({'length':length,'status':'computed','eligible_subsequences':len(records),'profile_median':float(np.median(finite)),
            'profile_min':float(finite.min()),'radius':radius,'radius_floor':2*s.noise_sigma,'nontrivial_pairs':len(all_pairs)//2,
            'profile':[None if not np.isfinite(v) else float(v) for v in profile]})
    chosen=[]
    for p in sorted(pool,key=lambda v:(-v['strength'],-v['support_count'],v['length'],v['medoid'])):
        spans={(r['start_ms'],r['end_ms']) for r in [p['records'][i] for i in p['members']]}
        redundant=False
        for old in chosen:
            old_spans=[old['records'][i] for i in old['members']]
            overlap=sum(any(a<r['end_ms'] and r['start_ms']<b for r in old_spans) for a,b in spans)/len(spans)
            if overlap>.75:redundant=True;break
        if not redundant:chosen.append(p)
        if len(chosen)>=s.max_prototypes:break
    for i,p in enumerate(chosen):p['id']=f'anonymous_{i:03d}'
    return chosen,profiles


def match(z,starts,ends,activity,prototypes,s,activity_floor):
    proposed=[]
    for p in prototypes:
        records,vec=subsequences(z,starts,ends,activity,p['length'],s.min_shape_rms,activity_floor,s.context_weight)
        d=np.linalg.norm(vec-p['descriptor'],axis=1) if len(vec) else []
        for i,value in enumerate(d):
            if value<=p['radius']:proposed.append({**records[i],'prototype_id':p['id'],'distance':float(value),'relative_distance':float(value/max(p['radius'],1e-9))})
    selected=[]
    for hit in sorted(proposed,key=lambda v:(v['relative_distance'],v['start_ms'],v['prototype_id'])):
        if any(hit['start_ms']<h['end_ms'] and h['start_ms']<hit['end_ms'] for h in selected):continue
        selected.append(hit)
    selected.sort(key=lambda v:v['start_ms']);labels=np.full(len(z),-1,dtype=int)
    for h in selected:labels[h['row_start']:h['row_end']]=int(h['prototype_id'].split('_')[-1])
    return selected,labels


def segmentation(z,starts,ends,s,parameters):
    n=len(z);delta=np.r_[0.,np.linalg.norm(np.diff(z,axis=0),axis=1)]
    centers=(starts+ends)//2
    edges=np.r_[starts[0],(centers[:-1]+centers[1:])//2,ends[-1]]
    multiscale=np.zeros(n)
    for i in range(1,n):
        available=[]
        for scale in s.scales:
            if i>=scale and i+scale<=n:available.append(float(np.linalg.norm(z[i-scale:i].mean(0)-z[i:i+scale].mean(0))))
        multiscale[i]=float(np.mean(available)) if available else 0.
    def points(score,threshold):
        selected=[]
        for i in sorted(np.flatnonzero(score>threshold),key=lambda j:(-score[j],j)):
            if i<s.min_segment_windows or n-i<s.min_segment_windows:continue
            if all(abs(i-j)>=s.min_segment_windows for j in selected):selected.append(int(i))
        return sorted(selected)
    methods={'simple_change':points(delta,parameters['delta_threshold']),
        'multiscale_change':points(multiscale,parameters['multiscale_threshold']),
        'fixed_windows':list(range(s.fixed_segment_windows,n,s.fixed_segment_windows))}
    prefix=np.vstack([np.zeros(z.shape[1]),np.cumsum(z,axis=0)]);squares=np.r_[0.,np.cumsum((z*z).sum(1))]
    cost=np.full(n+1,np.inf);cost[0]=-parameters['dp_penalty'];previous=np.full(n+1,-1,dtype=int)
    for end in range(s.min_segment_windows,n+1):
        for begin in range(max(0,end-s.max_segment_windows),end-s.min_segment_windows+1):
            if not np.isfinite(cost[begin]):continue
            length=end-begin;total=prefix[end]-prefix[begin];error=max(0.,squares[end]-squares[begin]-float(total@total)/length)
            value=cost[begin]+error+parameters['dp_penalty']
            if value<cost[end]:cost[end]=value;previous[end]=begin
    boundaries=[];at=n
    while at>0 and previous[at]>=0:
        at=int(previous[at])
        if at:boundaries.append(at)
    methods['consistent_dp']=sorted(boundaries) if previous[n]>=0 else []
    results=[]
    for name,points_ in methods.items():
        chunks=[0,*points_,n];segments=[];within=0.
        for begin,end in zip(chunks,chunks[1:]):
            part=z[begin:end];error=float(np.square(part-part.mean(0)).sum());within+=error
            segments.append({'start_ms':int(edges[begin]),'end_ms':int(edges[end]),'row_start':begin,'row_end':end,'dispersion':error/max(1,len(part)),
                'raw_feature_support_start_ms':int(starts[begin]),'raw_feature_support_end_ms':int(ends[end-1])})
        total=float(np.square(z-z.mean(0)).sum())
        results.append({'method':name,'boundaries_ms':[int(edges[i]) for i in points_],'segments':segments,'segment_count':len(segments),
            'explained_temporal_variance':max(0.,1-within/total) if total>1e-12 else None,'feature_responsive':name!='fixed_windows',
            'offline_future_context':True,'time_cells':'nonoverlapping midpoint cells of cached clip features; not frame-level action truth','real_boundary_f1':None})
    return results,delta,multiscale


def fit_segmentation(z,starts,ends,s):
    delta=np.linalg.norm(np.diff(z,axis=0),axis=1);q=float(np.quantile(delta,s.change_quantile)) if len(delta) else 0.
    params={'delta_threshold':q,'multiscale_threshold':q,'dp_penalty':max(q*q*s.dp_penalty_multiplier,1e-9)}
    _,_,scores=segmentation(z,starts,ends,s,params)
    params['multiscale_threshold']=float(np.quantile(scores[1:],s.change_quantile)) if len(scores)>1 else 0.
    return params


def boundary_agreement(first,second,tolerance):
    a=sorted(first);b=sorted(second);i=j=hits=0
    while i<len(a) and j<len(b):
        if a[i]<b[j]-tolerance:i+=1
        elif b[j]<a[i]-tolerance:j+=1
        else:hits+=1;i+=1;j+=1
    return 2*hits/(len(a)+len(b)) if len(a)+len(b) else None


def assignment_agreement(a,b):
    mask=(a>=0)|(b>=0)
    return float(np.mean(a[mask]==b[mask])) if mask.any() else None
