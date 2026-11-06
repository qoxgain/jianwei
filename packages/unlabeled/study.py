"""Fit/calibration/replay zero-label research with immutable shadow proposals."""
import time
import numpy as np
from packages.temporal.models import fit_projection,project
from packages.knowledge.service import content_hash
from .data import time_roles
from .algorithms import discover,match,fit_segmentation,segmentation,assignment_agreement,boundary_agreement
from .forecast import fit_forecasts,score_forecasts


def transform(values,p):return project(values,p)/p['latent_scale']


def pattern_strength(prototypes):return max((p['strength'] for p in prototypes),default=0.)


def describe_prototypes(prototypes,fit_indices,stream):
    descriptions=[]
    for p in prototypes:
        occurrences=[p['records'][i] for i in p['members']];medoid=p['records'][p['medoid']]
        descriptions.append({'id':p['id'],'semantic_label':None,'pseudo_label':None,'status':'anonymous_candidate',
            'length_windows':p['length'],'support_count':p['support_count'],'radius':p['radius'],'separation_proxy':p['separation'],
            'strength_proxy':p['strength'],'duration_ms':medoid['end_ms']-medoid['start_ms'],
            'medoid':{'start_ms':medoid['start_ms'],'end_ms':medoid['end_ms']},
            'occurrences':[{'start_ms':r['start_ms'],'end_ms':r['end_ms']} for r in occurrences],
            'following_prototype_counts':p.get('following_prototype_counts',{}),
            'continuation_support':p.get('next_delta_support',{}),
            'cycle_interpretation':'disjoint original support occurrences, not verified business cycles or independent sources'})
    return descriptions


def fit_stream(stream,s):
    started=time.perf_counter();roles=time_roles(stream,s);fit=roles['fit'];cal=roles['calibration'];replay=roles['replay']
    if not np.any(stream.values[fit].std(0)>1e-6):
        return {'method':stream.name,'status':'degenerate_constant_input','real_precision':None,'real_recall':None,'real_f1':None},None
    p,explained=fit_projection(stream.values[fit],s.projection_dim);fit_z=project(stream.values[fit],p)
    p['latent_scale']=np.maximum(fit_z.std(0),1e-6);z=transform(stream.values,p);fit_z=z[fit]
    activity_floor=float(np.quantile(stream.activity[fit],s.activity_quantile))
    starts,ends=stream.starts[fit],stream.ends[fit];activity=stream.activity[fit]
    prototypes,profiles=discover(fit_z,starts,ends,activity,s,activity_floor)
    occurrences=sorted([(p['records'][i]['start_ms'],p['records'][i]['end_ms'],p['id']) for p in prototypes for i in p['members']])
    for proto in prototypes:
        transitions={}
        for member in proto['members']:
            end=proto['records'][member]['end_ms'];following=next((v for v in occurrences if v[0]>=end),None)
            if following:transitions[following[2]]=transitions.get(following[2],0)+1
        proto['following_prototype_counts']=transitions
    segment_params=fit_segmentation(fit_z,starts,ends,s);forecasts=fit_forecasts(fit_z,starts,ends,s,prototypes,activity_floor)
    nulls=[]
    for seed in s.seeds:
        rng=np.random.default_rng(seed)
        for label in ['time_shuffle','block_shuffle']:
            if label=='time_shuffle':order=rng.permutation(len(fit))
            else:
                blocks=[np.arange(i,min(i+s.fixed_segment_windows,len(fit))) for i in range(0,len(fit),s.fixed_segment_windows)]
                order=np.concatenate([blocks[i] for i in rng.permutation(len(blocks))])
            negative,_=discover(fit_z[order],starts,ends,activity[order],s,activity_floor)
            nulls.append({'control':label,'seed':seed,'strength_proxy':pattern_strength(negative),'prototype_count':len(negative)})
    strength=pattern_strength(prototypes);worst_null=max((n['strength_proxy'] for n in nulls),default=0.)
    cal_hits,cal_labels=match(z[cal],stream.starts[cal],stream.ends[cal],stream.activity[cal],prototypes,s,activity_floor)
    stability=[]
    for seed in s.seeds:
        altered=z[cal]+np.random.default_rng(seed).normal(0,s.noise_sigma,z[cal].shape)
        _,labels=match(altered,stream.starts[cal],stream.ends[cal],stream.activity[cal],prototypes,s,activity_floor)
        stability.append({'seed':seed,'assignment_agreement':assignment_agreement(cal_labels,labels)})
    stable_values=[r['assignment_agreement'] for r in stability if r['assignment_agreement'] is not None]
    coverage=float(np.mean(cal_labels>=0));cal_forecast=score_forecasts(z[cal],stream.starts[cal],stream.ends[cal],s,forecasts,stream.activity[cal])
    prediction_gains=[r['methods']['prototype_continuation']['gain_vs_persistence'] for r in cal_forecast if r['status']=='computed']
    gates={'has_repeated_dynamic_candidates':bool(prototypes),'has_calibration_occurrence':bool(cal_hits),
        'noise_consistent':bool(stable_values) and min(stable_values)>=s.min_gate_stability,
        'exceeds_all_time_and_block_shuffle_searches':strength-worst_null>s.min_gate_null_margin,
        'not_near_total_coverage_collapse':0<coverage<s.max_gate_coverage,
        'prediction_exceeds_persistence_at_all_registered_lags':len(prediction_gains)==len(s.forecast_lags) and all(v is not None and v>s.min_gate_prediction_gain for v in prediction_gains),
        'not_background_control':not stream.background}
    gate_passed=all(gates.values());active_ids=[p['id'] for p in prototypes] if gate_passed else []
    model={'projection':p,'prototypes':prototypes,'segment_params':segment_params,'forecasts':forecasts,'activity_floor':activity_floor,
        'fit_indices':fit,'cal_indices':cal,'active_ids':active_ids,'contract':stream.contract,'name':stream.name,
        'fit_state_hash':content_hash({'fit_values':stream.values[fit].tolist(),'settings':s.model_dump(mode='json'),'method':stream.name}),
        'gate_state_hash':content_hash({'fit_values':stream.values[fit].tolist(),'cal_values':stream.values[cal].tolist(),'gates':gates,'method':stream.name})}
    # Above this point NO replay values have participated in a fit/gate decision.
    evaluations={};control_rows=[]
    for role,indices in [('calibration',cal),('replay',replay)]:
        hits,labels=match(z[indices],stream.starts[indices],stream.ends[indices],stream.activity[indices],prototypes,s,activity_floor)
        segments,delta,multi=segmentation(z[indices],stream.starts[indices],stream.ends[indices],s,segment_params)
        known=labels[labels>=0];shares=np.bincount(known)/len(known) if len(known) else np.array([])
        evaluations[role]={'matches':hits,'matched_window_coverage':float(np.mean(labels>=0)),
            'experimental_active_matches':sum(h['prototype_id'] in active_ids for h in hits),'largest_prototype_share':float(shares.max()) if len(shares) else None,
            'effective_prototype_count':float(np.exp(-np.sum(shares[shares>0]*np.log(shares[shares>0])))) if len(shares) else None,
            'segmentation':segments,'forecasts':cal_forecast if role=='calibration' else score_forecasts(z[indices],stream.starts[indices],stream.ends[indices],s,forecasts,stream.activity[indices]),
            'trace':[{'pts_ms':int(stream.ends[i]),'change':float(d),'multiscale_change':float(m),'prototype_id':None if label<0 else f'anonymous_{label:03d}'} for i,d,m,label in zip(indices,delta,multi,labels)],
            'scope':{'start_ms':int(stream.starts[indices[0]]),'end_ms':int(stream.ends[indices[-1]]),'windows':len(indices)}}
    indices=replay;base=evaluations['replay'];_,base_labels=match(z[indices],stream.starts[indices],stream.ends[indices],stream.activity[indices],prototypes,s,activity_floor)
    for seed in s.seeds:
        rng=np.random.default_rng(seed)
        order=rng.permutation(len(indices))
        for label,values,act in [('noise',z[indices]+rng.normal(0,s.noise_sigma,z[indices].shape),stream.activity[indices]),('time_shuffle',z[indices][order],stream.activity[indices][order])]:
            hits,labels=match(values,stream.starts[indices],stream.ends[indices],act,prototypes,s,activity_floor)
            segments,_,_=segmentation(values,stream.starts[indices],stream.ends[indices],s,segment_params)
            control_rows.append({'control':label,'seed':seed,'coverage':float(np.mean(labels>=0)),'matches':len(hits),
                'assignment_agreement':assignment_agreement(base_labels,labels),
                'boundary_agreements':[{'method':a['method'],'agreement':boundary_agreement(a['boundaries_ms'],b['boundaries_ms'],s.boundary_tolerance_ms)} for a,b in zip(base['segmentation'],segments)]})
    for label,variant in stream.variants.items():
        altered=transform(variant['values'][indices],p);hits,labels=match(altered,stream.starts[indices],stream.ends[indices],variant['activity'][indices],prototypes,s,activity_floor)
        control_rows.append({'control':label,'seed':None,'coverage':float(np.mean(labels>=0)),'matches':len(hits),'assignment_agreement':assignment_agreement(base_labels,labels),
            'interpretation':'declared cached-feature stress test; not a new independently filmed input or relabeled feature recipe'})
    # Drop first replay point, preserving its actual PTS, to test time-origin stability.
    if len(indices)>3:
        trimmed=indices[1:];_,labels=match(z[trimmed],stream.starts[trimmed],stream.ends[trimmed],stream.activity[trimmed],prototypes,s,activity_floor)
        control_rows.append({'control':'time_origin_trim_one_window','assignment_agreement':assignment_agreement(base_labels[1:],labels),'coverage':float(np.mean(labels>=0)),'seed':None})
    fit_description={'start_ms':int(starts[0]),'end_ms':int(ends[-1]),'windows':len(fit),'projection_dimension':fit_z.shape[1],
        'explained_variance_ratio':explained,'activity_floor':activity_floor,'state_hash':model['fit_state_hash']}
    report={'method':stream.name,'status':'computed','feature_contract':stream.contract,'fit':fit_description,
        'prototypes':describe_prototypes(prototypes,fit,stream),'matrix_profiles':profiles,'fit_null_controls_all_seeds':nulls,
        'fit_strength_proxy':strength,'worst_null_strength_proxy':worst_null,'null_excess_proxy':strength-worst_null,
        'calibration_noise_all_seeds':stability,'shadow_gate':{'checks':gates,'passed':gate_passed,'active_prototypes':len(active_ids),
            'state_hash':model['gate_state_hash'],'frozen_before_replay':True,'failure_reasons':[name for name,ok in gates.items() if not ok]},
        'evaluations':evaluations,'controls_all_results':control_rows,'elapsed_seconds':time.perf_counter()-started,
        'semantics':'anonymous recurring feature patterns, not observed action classes','real_precision':None,'real_recall':None,'real_f1':None,
        'roi_deep_shift_control':'not_extracted_in_B1; only cached lightweight offsets can be evaluated','production_activated':False}
    return report,model
