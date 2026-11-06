"""Prospective metadata admission and frozen-model A/B proxy validation.

Never fits a model, changes the old gate or activates a memory. Real data missing
means blocked. Protocol criteria are new hypotheses, not retroactive approvals.
"""
import argparse,json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from packages.config.loader import PROJECT_ROOT,load_bundle
from packages.knowledge.service import content_hash
from packages.local_motion.extraction import sha,write
BASELINE=PROJECT_ROOT/'docs/plans/p0.3.7-b2u1/baseline-seal.json'


def seal(draft):
    validate_body(draft)
    from packages.unlabeled.engine import code_identity
    body={'schema':'b2u1-sealed-development-protocol-v1','sealed_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'scope':'new independent development captures, NOT formal sealed tests','protocol':draft,'baseline_commit':'755aefd8e623b130f83282f6ad40d0fe50b21757',
        'frozen_algorithm_identity':code_identity(),'validator_sha256':sha(Path(__file__)),'baseline_seal_sha256':sha(BASELINE)}
    return {**body,'protocol_hash':content_hash(body)}


def validate_body(p):
    if p.get('schema')!='b2u1-prospective-development-routes-v1' or p.get('roles')!=['fit','calibration','confirmation']:raise ValueError('Unsupported protocol roles')
    if p.get('primary_feature')!='frame_difference' or p['route_b'].get('primary_predictor')!='ridge':raise ValueError('No post-hoc predictor/feature selection')
    for key in ['old_failed_candidates_promoted','production_activation','formal_test_access','p04_admitted']:
        if p.get(key) is not False:raise ValueError('Development protocol cannot activate models or access formal tests')
    if p.get('old_gate_must_remain_unchanged') is not True or p.get('reject_baseline_catalog_sources') is not True:raise ValueError('Historical protection required')
    if p.get('frozen_unlabeled_settings')!=json.loads(BASELINE.read_text())['legacy_math_settings']:raise ValueError('Old algorithm and composite thresholds must remain unchanged')
    if p['minimum_sessions']!=3 or p['minimum_capture_dates']<2 or p['minimum_duration_ms']<240000 or p['maximum_duration_ms']>480000:raise ValueError('Invalid fixed acquisition bounds')
    for key in ['coverage_min','coverage_max','noise_agreement_min','roi_agreement_min','coverage_excess_over_all_time_shuffles_min','coverage_excess_over_background_min']:
        if type(p['route_a'][key]) not in (int,float) or not 0<=p['route_a'][key]<=1:raise ValueError('Invalid recurrence threshold')
    if p['route_a']['coverage_min']>=p['route_a']['coverage_max'] or p['route_a']['future_prediction_required'] is not False:raise ValueError('Routes must remain separate')
    if p['route_b']['minimum_pairs_each_lag']<30 or any(not 0<p['route_b'][k]<1 for k in ['gain_vs_persistence_min','gain_vs_trainmean_min','history_ablation_gain_for_history_claim']):raise ValueError('Invalid forecasting threshold')
    return p


def load_protocol(path):
    r=json.loads(Path(path).read_text());body={k:v for k,v in r.items() if k!='protocol_hash'}
    if r.get('schema')!='b2u1-sealed-development-protocol-v1' or content_hash(body)!=r.get('protocol_hash'):raise ValueError('Protocol seal changed')
    validate_body(r['protocol'])
    if sha(Path(__file__))!=r['validator_sha256']:raise ValueError('Validator changed after protocol freeze; explicitly version a new protocol')
    if sha(BASELINE)!=r['baseline_seal_sha256']:raise ValueError('Baseline fingerprint changed')
    return r


def baseline_hashes():
    return set(json.loads(BASELINE.read_text())['observed_source_identity_digests'])


def admission(protocol,records,old_hashes):
    p=protocol['protocol'];issues=[];by_role={};sessions=set();roots=set();source_ids=set();views=set();cameras=set();stations=set();dates=set();times={}
    def issue(role,code):issues.append({'role':role,'code':code})
    if len(records)!=p['minimum_sessions']:issue('all','EXACTLY_THREE_INDEPENDENT_DEVELOPMENT_ROLES_REQUIRED')
    frozen=datetime.fromisoformat(protocol['sealed_at'])
    for r in records:
        role=r.get('role','unknown')
        if role not in p['roles'] or role in by_role:issue(role,'ROLE_UNKNOWN_OR_DUPLICATED')
        by_role[role]=r
        for name in ['source_sha256','root_source_sha256']:
            value=r.get(name)
            if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):issue(role,'SOURCE_IDENTITY_MISSING')
            if content_hash(value) in old_hashes:issue(role,'OBSERVED_BASELINE_SOURCE_CANNOT_BE_PROSPECTIVE_DATA')
        if r.get('source_sha256') is not None:
            if r['source_sha256'] in source_ids:issue(role,'DUPLICATE_SOURCE_BYTES')
            source_ids.add(r['source_sha256'])
        if r.get('kind')!='field' or r.get('split')!='unassigned':issue(role,'ONLY_NEW_DEVELOPMENT_FIELD_SOURCES_ALLOWED')
        if r.get('relation')!='original' or r.get('parent_source_sha256') is not None or r.get('source_sha256')!=r.get('root_source_sha256'):issue(role,'ONLY_VERIFIED_ORIGINAL_CAPTURE_NOT_CROPPED_OR_TRANSCODED_ALIASES')
        if r.get('independence_review')!='verified' or r.get('near_duplicate_review')!='clear' or not r.get('review_authority'):issue(role,'CAPTURE_LINEAGE_REVIEW_PENDING')
        if r.get('capture_time_evidence')!='verified_capture_log':issue(role,'CONTAINER_CREATION_HINT_IS_NOT_CAPTURE_EVIDENCE')
        if r.get('view_stability_review')!='verified_fixed' or r.get('background_review')!='verified_no_operation_intrusion':issue(role,'VIEW_OR_BACKGROUND_CONTROL_NOT_VERIFIED')
        duration=r.get('duration_ms')
        if type(duration) is not int or not p['minimum_duration_ms']<=duration<=p['maximum_duration_ms']:issue(role,'INSUFFICIENT_OR_UNBOUNDED_CAPTURE_DURATION')
        for name,seen in [('capture_session_id',sessions),('root_source_sha256',roots)]:
            value=r.get(name)
            if not value or value in seen:issue(role,'INDEPENDENT_ROOT_AND_SESSION_NOT_ESTABLISHED')
            seen.add(value)
        for name,seen in [('view_id',views),('camera_id',cameras),('workstation_id',stations)]:
            value=r.get(name)
            if not value:issue(role,'FIXED_VIEW_IDENTITY_MISSING')
            seen.add(value)
        try:
            t=datetime.fromisoformat(r['captured_at'].replace('Z','+00:00'))
            if t.tzinfo is None:raise ValueError('timezone required')
            if t<=frozen:issue(role,'CAPTURE_MUST_FOLLOW_NEW_PROTOCOL_FREEZE')
            if t>datetime.now(ZoneInfo('UTC')):issue(role,'CAPTURE_TIME_IS_IN_THE_FUTURE')
            times[role]=t;dates.add(t.date().isoformat())
        except (KeyError,TypeError,ValueError,AttributeError):issue(role,'VERIFIED_ZONED_CAPTURE_TIME_MISSING')
    if set(by_role)!=set(p['roles']):issue('all','MISSING_DEVELOPMENT_ROLE')
    if any(len(s)!=1 for s in [views,cameras,stations]):issue('all','CROSS_VIEW_MATCHING_NOT_PREREGISTERED')
    if len(dates)<p['minimum_capture_dates']:issue('all','TWO_CAPTURE_DATES_REQUIRED')
    if set(times)==set(p['roles']) and not times['fit']<times['calibration']<times['confirmation']:issue('all','CAUSAL_CAPTURE_ORDER_INVALID')
    return {'schema':'b2u1-prospective-admission-v1','ready':not issues,'issues':issues,'protocol_hash':protocol['protocol_hash'],
        'field_transfer_executed':False,'action_labels_required':False,'formal_test_access':False,'production_activated':False}


def freeze_model(protocol,records,parent,output,config):
    from packages.unlabeled.memory import load_state
    fit=next(r for r in records if r['role']=='fit')
    check=admission(protocol,[fit],baseline_hashes())
    if any(i['role']=='fit' for i in check['issues']):raise ValueError('New fit-source admission not ready')
    parent=Path(parent);report=json.loads((parent/'report-private.json').read_text())
    if report['source']['sha256']!=fit['source_sha256'] or report['evidence_class']!='development_exploration' or report['formal_test_data_consumed'] is not False:raise ValueError('New fit source does not match parent')
    for key in ['source_sha256','dependencies']:
        if report['code_identity'][key]!=protocol['frozen_algorithm_identity'][key]:raise ValueError('Parent algorithm/dependencies are not preregistered')
    actual={k:v for k,v in report['settings'].items() if k not in ['feature_manifest','source_evidence']}
    if actual!=protocol['protocol']['frozen_unlabeled_settings']:raise ValueError('Parent settings changed')
    bundle=load_bundle(config)
    if bundle.config_hash!=report['config_hash']:raise ValueError('Fit runtime configuration does not match frozen parent')
    state=load_state(parent,bundle.config.ingestion.max_expanded_bytes)
    if len(state['branches'])!=1:raise ValueError('One frozen fit group required')
    body={'schema':'b2u1-fit-freeze-receipt-v1','protocol_hash':protocol['protocol_hash'],'fit_source_sha256':fit['source_sha256'],
        'frozen_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'files_sha256':{n:sha(parent/n) for n in ['report-private.json','model-private.npz','model-state-private.json','registry-private.json']},
        'primary_fit_state_hash':state['models'][protocol['protocol']['primary_feature']]['fit_state_hash'],'production_activated':False}
    r={**body,'receipt_hash':content_hash(body)};write(output/'model-freeze-private.json',r);return r


def independent_proxy_checks(a,b,p):
    """Assess complete new independent results; absence remains blocked, never 0."""
    if a['protocol_hash']!=b['protocol_hash'] or a['fit_state_hash']!=b['fit_state_hash'] or a['model_freeze_receipt_hash']!=b['model_freeze_receipt_hash'] or a['query_source_sha256']==b['query_source_sha256']:raise ValueError('Mismatched frozen independent results')
    if a['role']!='calibration' or b['role']!='confirmation':raise ValueError('Evaluation roles mismatched')
    if not a['admission_ready'] or not b['admission_ready'] or a['evidence_class']!='development_exploration' or b['evidence_class']!='development_exploration':raise ValueError('Real independent development admission required')
    if a['registered_seeds']!=p['frozen_algorithm']['seeds'] or b['registered_seeds']!=p['frozen_algorithm']['seeds']:raise ValueError('Registered seed set changed')
    ra=p['route_a'];rb=p['route_b'];results=[]
    for r in [a,b]:
        s=r['recurrence'];f=r['forecast'];cov=s['coverage'];noise=s['noise_agreement'];roi=s['roi_agreement']
        if len(noise)!=len(p['frozen_algorithm']['seeds']) or len(s['time_shuffle_coverage'])!=len(noise) or len(roi)!=2:raise ValueError('Incomplete registered controls')
        checks_a={'coverage_not_unknown_or_total':ra['coverage_min']<=cov<=ra['coverage_max'],
            'noise_stable':all(v is not None and v>=ra['noise_agreement_min'] for v in noise),
            'roi_stable':all(v is not None and v>=ra['roi_agreement_min'] for v in roi),
            'above_time_shuffle':cov-max(s['time_shuffle_coverage'])>=ra['coverage_excess_over_all_time_shuffles_min'],
            'above_verified_background':cov-s['background_coverage']>=ra['coverage_excess_over_background_min']}
        checks_b={};history={}
        if {v['lag_windows'] for v in f}!=set(p['frozen_algorithm']['forecast_lags']):raise ValueError('Missing forecast horizon')
        for v in f:
            lag=v['lag_windows'];m=v['methods'];e=m['ridge']['normalized_latent_mse'];pers=m['persistence']['normalized_latent_mse'];mean=m['train_mean']['normalized_latent_mse']
            if [x['seed'] for x in v['shuffled_target_ridge_all_seeds']]!=p['frozen_algorithm']['seeds']:raise ValueError('Shuffled-target seed controls incomplete or duplicated')
            checks_b[str(lag)]={'enough_pairs':v['pairs']>=rb['minimum_pairs_each_lag'],'beats_persistence':pers>0 and 1-e/pers>=rb['gain_vs_persistence_min'],
                'beats_trainmean':mean>0 and 1-e/mean>=rb['gain_vs_trainmean_min'],'beats_all_target_shuffles':len(v['shuffled_target_ridge_all_seeds'])==len(noise) and all(e<x['normalized_latent_mse'] for x in v['shuffled_target_ridge_all_seeds'])}
            altered=v['history_controls'];errors=[x['normalized_latent_mse'] for x in altered]
            if len(altered)!=len(noise)+1 or [x.get('seed') for x in altered[1:]]!=p['frozen_algorithm']['seeds'] or altered[0]['control']!='repeat_current_context_not_retrained':raise ValueError('Historical context controls incomplete')
            history[str(lag)]=len(errors)==len(noise)+1 and all(x>0 and 1-e/x>=rb['history_ablation_gain_for_history_claim'] for x in errors)
        results.append({'role':r['role'],'route_a_checks':checks_a,'route_b_checks':checks_b,'history_use_subclaim':history})
    recurrent=[{k for k,v in r['recurrence']['prototype_occurrences'].items() if v>=ra['minimum_occurrences_per_session']} for r in [a,b]]
    common=bool(recurrent[0]&recurrent[1]);pass_a=common and all(all(r['route_a_checks'].values()) for r in results)
    pass_b=all(all(all(c.values()) for c in r['route_b_checks'].values()) for r in results)
    return {'schema':'b2u1-independent-proxy-assessment-v1','route_a_proxy_supported':pass_a,'route_b_proxy_supported':pass_b,'same_prototype_recurrence':common,'results':results,
        'old_gate_changed':False,'historical_failed_candidates_promoted':False,'production_activated':False,'real_precision':None,'real_recall':None,'real_f1':None,'p04_admitted':False,
        'interpretation':'Prospective proxy feasibility only. No semantic action truth, no p-values, no production memory activation.'}


def run_query(protocol,records,config,parent,output,role,receipt):
    from packages.unlabeled.engine import query,code_identity
    from packages.unlabeled.memory import load_state
    from packages.unlabeled.data import load_features
    from packages.unlabeled.study import transform
    from packages.unlabeled.algorithms import match,assignment_agreement
    from packages.unlabeled.forecast import pairs
    from packages.temporal.models import ridge_predict
    admitted=admission(protocol,records,baseline_hashes())
    if not admitted['ready']:write(output/'admission.json',admitted);return admitted
    bundle=load_bundle(config);record=next(r for r in records if r['role']==role);fit=next(r for r in records if r['role']=='fit');settings=bundle.config.unlabeled
    if role not in ['calibration','confirmation']:raise ValueError('Query role must be a new held-out session')
    for k,v in protocol['protocol']['frozen_unlabeled_settings'].items():
        if getattr(settings,k)!=v:raise ValueError('Frozen research settings changed')
    if protocol['frozen_algorithm_identity']['source_sha256']!=code_identity()['source_sha256']:raise ValueError('Frozen algorithm changed')
    parent_report=json.loads((Path(parent)/'report-private.json').read_text())
    if parent_report['source']['sha256']!=fit['source_sha256'] or bundle.config.local_motion.source_sha256!=record['source_sha256']:raise ValueError('Source role/model binding mismatch')
    body={k:v for k,v in receipt.items() if k!='receipt_hash'}
    if content_hash(body)!=receipt.get('receipt_hash') or receipt['protocol_hash']!=protocol['protocol_hash'] or receipt['fit_source_sha256']!=fit['source_sha256']:raise ValueError('Fit receipt identity changed')
    for name,value in receipt['files_sha256'].items():
        if name not in ['report-private.json','model-private.npz','model-state-private.json','registry-private.json'] or sha(Path(parent)/name)!=value:raise ValueError('Parent changed after model freeze')
    if len(receipt['files_sha256'])!=4:raise ValueError('Incomplete fit receipt')
    if not datetime.fromisoformat(protocol['sealed_at'])<=datetime.fromisoformat(receipt['frozen_at'])<min(datetime.fromisoformat(r['captured_at'].replace('Z','+00:00')) for r in records if r['role']!='fit'):raise ValueError('Parent must be frozen before calibration and confirmation capture')
    result=query(parent,bundle,output/'core-query',update=False)
    manifest,streams,_=load_features(bundle);state=load_state(parent,bundle.config.ingestion.max_expanded_bytes)
    if len(state['branches'])!=1:raise ValueError('Prospective protocol requires one frozen fit group')
    name=protocol['protocol']['primary_feature'];stream=next(s for s in streams if s.name==name);model=state['models'][name]
    z=transform(stream.values,model['projection']);hits,labels=match(z,stream.starts,stream.ends,stream.activity,model['prototypes'],settings,model['activity_floor'])
    noise=[];shuffle=[];block_shuffle=[]
    for seed in settings.seeds:
        rng=np.random.default_rng(seed);_,alt=match(z+rng.normal(0,settings.noise_sigma,z.shape),stream.starts,stream.ends,stream.activity,model['prototypes'],settings,model['activity_floor']);noise.append(assignment_agreement(labels,alt))
        order=rng.permutation(len(z));_,alt=match(z[order],stream.starts,stream.ends,stream.activity[order],model['prototypes'],settings,model['activity_floor']);shuffle.append(float(np.mean(alt>=0)))
        blocks=[np.arange(i,min(i+settings.fixed_segment_windows,len(z))) for i in range(0,len(z),settings.fixed_segment_windows)]
        order=np.concatenate([blocks[i] for i in rng.permutation(len(blocks))])
        _,alt=match(z[order],stream.starts,stream.ends,stream.activity[order],model['prototypes'],settings,model['activity_floor']);block_shuffle.append(float(np.mean(alt>=0)))
    roi=[]
    for side in ['left','right']:
        variant=stream.variants.get('roi_'+side)
        if variant is None:raise ValueError('Registered lightweight ROI shifts were not extracted')
        _,alt=match(transform(variant['values'],model['projection']),stream.starts,stream.ends,variant['activity'],model['prototypes'],settings,model['activity_floor']);roi.append(assignment_agreement(labels,alt))
    mixed=stream.variants['background_mix'];background=(mixed['values']-(1-settings.background_mix)*stream.values)/settings.background_mix
    activity=(mixed['activity']-(1-settings.background_mix)*stream.activity)/settings.background_mix
    _,bg=match(transform(background,model['projection']),stream.starts,stream.ends,activity,model['prototypes'],settings,model['activity_floor'])
    forecast=next(m['forecasts'] for m in result['methods'] if m['method']==name)
    for f in forecast:
        if f['status']!='computed':raise ValueError('Missing valid support-disjoint forecast pairs')
        lag=f['lag_windows'];x,y,_=pairs(z,stream.starts,stream.ends,settings.forecast_history,lag);controls=[]
        no_history=np.repeat(x[:,-1:],x.shape[1],axis=1);controls.append({'control':'repeat_current_context_not_retrained','normalized_latent_mse':float(np.mean((ridge_predict(no_history,model['forecasts'][lag]['ridge'])-y)**2))})
        for seed in settings.seeds:
            altered=x.copy();order=np.random.default_rng(seed).permutation(x.shape[1]-1);altered[:,:-1]=altered[:,order]
            controls.append({'control':'shuffle_past_keep_current_and_target','seed':seed,'normalized_latent_mse':float(np.mean((ridge_predict(altered,model['forecasts'][lag]['ridge'])-y)**2))})
        f['history_controls']=controls
    assessment_input={'schema':'b2u1-private-session-proxies-v1','protocol_hash':protocol['protocol_hash'],'role':role,'admission_ready':True,'query_source_sha256':record['source_sha256'],
        'fit_state_hash':model['fit_state_hash'],'model_freeze_receipt_hash':receipt['receipt_hash'],'registered_seeds':settings.seeds,'evidence_class':'development_exploration','recurrence':{'coverage':float(np.mean(labels>=0)),'noise_agreement':noise,'roi_agreement':roi,'time_shuffle_coverage':shuffle,'block_shuffle_coverage_descriptive_only':block_shuffle,'background_coverage':float(np.mean(bg>=0)),
            'prototype_occurrences':dict(Counter(h['prototype_id'] for h in hits)),'pure_background_reconstructed_from_cached_scalars':True},'forecast':forecast,
        'production_activated':False,'historical_failed_candidates_promoted':False}
    write(output/'session-proxies-private.json',assessment_input);return {'status':'executed_new_independent_development_proxies','protocol_hash':protocol['protocol_hash'],'production_activated':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['seal','admit','freeze-model','query','assess']);p.add_argument('--protocol',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--sources',type=Path);p.add_argument('--config',type=Path);p.add_argument('--parent',type=Path);p.add_argument('--model-receipt',type=Path);p.add_argument('--role',choices=['calibration','confirmation']);p.add_argument('--calibration-result',type=Path);p.add_argument('--confirmation-result',type=Path);a=p.parse_args()
    if a.mode=='seal':
        if a.output.exists():raise FileExistsError('Never overwrite a frozen protocol')
        write(a.output,seal(json.loads(a.protocol.read_text())));print('Frozen new-development protocol; no model or P0.4 admission');return
    protocol=load_protocol(a.protocol);out=a.output.resolve()
    if not out.is_relative_to(PROJECT_ROOT/'artifacts'):raise ValueError('Private new output required')
    out.mkdir(parents=True,exist_ok=False)
    if a.mode in ['admit','query','freeze-model']:
        if not a.sources:p.error('--sources required')
        records=json.loads(a.sources.read_text())['sources']
        if a.mode=='admit':r=admission(protocol,records,baseline_hashes());write(out/'admission.json',r)
        elif a.mode=='freeze-model':
            if not a.parent or not a.config:p.error('freeze-model requires --parent --config')
            r=freeze_model(protocol,records,a.parent,out,a.config)
        else:
            if not all([a.config,a.parent,a.role,a.model_receipt]):p.error('query requires --config --parent --role --model-receipt')
            r=run_query(protocol,records,a.config,a.parent,out,a.role,json.loads(a.model_receipt.read_text()))
        print(json.dumps(r));
        if r.get('ready') is False:raise SystemExit(2)
    else:
        if not all([a.calibration_result,a.confirmation_result,a.sources,a.model_receipt]):p.error('assessment requires two independent result files, sources and model receipt')
        records=json.loads(a.sources.read_text())['sources'];ready=admission(protocol,records,baseline_hashes())
        if not ready['ready']:write(out/'admission.json',ready);print(json.dumps(ready));raise SystemExit(2)
        a_result=json.loads(a.calibration_result.read_text());b_result=json.loads(a.confirmation_result.read_text())
        receipt=json.loads(a.model_receipt.read_text());body={k:v for k,v in receipt.items() if k!='receipt_hash'}
        if content_hash(body)!=receipt.get('receipt_hash') or receipt['protocol_hash']!=protocol['protocol_hash']:raise ValueError('Assessment fit receipt changed')
        for result in [a_result,b_result]:
            if result['query_source_sha256']!=next(r['source_sha256'] for r in records if r['role']==result['role']):raise ValueError('Query result does not match admitted source role')
            if result['model_freeze_receipt_hash']!=receipt['receipt_hash'] or result['fit_state_hash']!=receipt['primary_fit_state_hash']:raise ValueError('Query result does not match the frozen fit model')
        if a_result['protocol_hash']!=protocol['protocol_hash']:raise ValueError('Result protocol mismatch')
        r=independent_proxy_checks(a_result,b_result,protocol['protocol']);write(out/'assessment-private.json',r);print(json.dumps({'route_a_proxy_supported':r['route_a_proxy_supported'],'route_b_proxy_supported':r['route_b_proxy_supported'],'production_activated':False}))


if __name__=='__main__':main()
