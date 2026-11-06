"""Mechanisms with generated fixtures/metadata, never field effect evidence."""
import copy,json
from pathlib import Path
import pytest
from packages.config.loader import PROJECT_ROOT
from packages.knowledge.service import content_hash
from scripts.diagnose_b2u1 import diagnose,union_ms,distribution
from scripts.validate_b2u1 import admission,independent_proxy_checks,validate_body,seal,load_protocol

DRAFT=PROJECT_ROOT/'docs/plans/p0.3.7-b2u1/routes-draft.json'

def report():
    r=json.loads((PROJECT_ROOT/'apps/web/src/data/unlabeledDemo.json').read_text());r['evidence_class']='development_exploration';return r

def protocol():return {'protocol':json.loads(DRAFT.read_text()),'protocol_hash':'f'*64,'sealed_at':'2024-01-01T00:00:00+00:00'}

def source_records():
    return [{'role':role,'source_sha256':str(i)*64,'root_source_sha256':str(i)*64,'kind':'field','split':'unassigned','relation':'original','parent_source_sha256':None,'independence_review':'verified','near_duplicate_review':'clear','review_authority':'generated unit fixture, no real capture',
        'capture_time_evidence':'verified_capture_log','view_stability_review':'verified_fixed','background_review':'verified_no_operation_intrusion','duration_ms':240000,
        'capture_session_id':role,'view_id':'fixture-view','camera_id':'fixture-camera','workstation_id':'fixture-station','captured_at':f'2024-01-0{i+1}T10:00:00+00:00'} for i,role in enumerate(['fit','calibration','confirmation'],1)]

def proxies(role):
    p=protocol()['protocol'];seeds=p['frozen_algorithm']['seeds']
    return {'protocol_hash':'f'*64,'fit_state_hash':'unit-frozen-state','model_freeze_receipt_hash':'unit-receipt','query_source_sha256':role,'role':role,'admission_ready':True,'evidence_class':'development_exploration','registered_seeds':seeds,
        'recurrence':{'coverage':.4,'noise_agreement':[.95]*5,'roi_agreement':[.9,.9],'time_shuffle_coverage':[.1]*5,'background_coverage':.1,'prototype_occurrences':{'anonymous_unit':4}},
        'forecast':[{'lag_windows':lag,'pairs':100,'methods':{'ridge':{'normalized_latent_mse':.5},'persistence':{'normalized_latent_mse':1.},'train_mean':{'normalized_latent_mse':.8},'prototype_continuation':{'normalized_latent_mse':.7}},
            'shuffled_target_ridge_all_seeds':[{'seed':s,'normalized_latent_mse':.9} for s in seeds],
            'history_controls':[{'control':'repeat_current_context_not_retrained','normalized_latent_mse':.8}]+[{'control':'shuffle_past_keep_current_and_target','seed':s,'normalized_latent_mse':.8} for s in seeds]} for lag in [2,4]]}


def test_diagnosis_is_read_only_and_gate_count_is_representation_level():
    r=report();before=copy.deepcopy(r);pub,private=diagnose(r)
    assert r==before and len(private['candidates'])==sum(len(m['prototypes']) for m in r['methods'])
    assert pub['gate_unit']=='representation, not individual prototype' and pub['old_registry_changed'] is False
    assert all(c['per_candidate_gate_evaluation_available'] is False for c in private['candidates'])


def test_public_diagnosis_has_no_industrial_candidate_values_or_timeline():
    pub,_=diagnose(report());text=json.dumps(pub)
    assert not any('"'+k+'"' in text for k in ['source','trace','occurrences','medoid','descriptor','fit_occurrences','prototype_id'])
    assert pub['real_f1'] is None


def test_interval_coverage_unions_overlaps_without_counting_duplicates():
    assert union_ms([{'start_ms':0,'end_ms':10},{'start_ms':5,'end_ms':15},{'start_ms':30,'end_ms':40}])==25
    assert distribution([None,None])=={'count':0,'null_count':2,'min':None,'median':None,'max':None}


def test_frozen_gate_tampering_is_rejected_not_reinterpreted():
    r=report();r['methods'][0]['shadow_gate']['passed']=True
    with pytest.raises(ValueError):diagnose(r)


def test_valid_metadata_fixture_is_not_a_real_capture_claim():
    r=admission(protocol(),source_records(),set());assert r['ready'] and not r['field_transfer_executed'] and not r['production_activated']


@pytest.mark.parametrize('field,value,code',[
    ('split','test','ONLY_NEW_DEVELOPMENT_FIELD_SOURCES_ALLOWED'),('kind','generated','ONLY_NEW_DEVELOPMENT_FIELD_SOURCES_ALLOWED'),
    ('capture_time_evidence','container_hint','CONTAINER_CREATION_HINT_IS_NOT_CAPTURE_EVIDENCE'),('captured_at',None,'VERIFIED_ZONED_CAPTURE_TIME_MISSING'),
    ('captured_at','2024-01-01T00:00:00Z','CAPTURE_MUST_FOLLOW_NEW_PROTOCOL_FREEZE'),('captured_at','2999-01-01T00:00:00Z','CAPTURE_TIME_IS_IN_THE_FUTURE'),
    ('near_duplicate_review','pending','CAPTURE_LINEAGE_REVIEW_PENDING'),('duration_ms',20440,'INSUFFICIENT_OR_UNBOUNDED_CAPTURE_DURATION'),
    ('view_stability_review','unknown','VIEW_OR_BACKGROUND_CONTROL_NOT_VERIFIED')])
def test_source_admission_fails_closed_without_action_labels(field,value,code):
    records=source_records();records[1][field]=value;r=admission(protocol(),records,set())
    assert not r['ready'] and code in [i['code'] for i in r['issues']]


def test_old_source_new_name_different_sha_and_single_session_do_not_make_independence():
    r=source_records();known={content_hash(r[0]['source_sha256'])};a=admission(protocol(),r,known)
    assert not a['ready'] and any(i['code']=='OBSERVED_BASELINE_SOURCE_CANNOT_BE_PROSPECTIVE_DATA' for i in a['issues'])
    r=source_records();r[1]['root_source_sha256']=r[0]['root_source_sha256'];assert not admission(protocol(),r,set())['ready']
    r=source_records();r[1]['source_sha256']=r[0]['source_sha256'];assert not admission(protocol(),r,set())['ready']
    r=source_records();r[1]['capture_session_id']=r[0]['capture_session_id'];assert not admission(protocol(),r,set())['ready']


def test_protocol_rejects_posthoc_legacy_threshold_changes_and_production():
    p=json.loads(DRAFT.read_text());p['frozen_unlabeled_settings']['min_gate_null_margin']=0
    with pytest.raises(ValueError):validate_body(p)
    p=json.loads(DRAFT.read_text());p['production_activation']=True
    with pytest.raises(ValueError):validate_body(p)


def test_protocol_seal_rejects_mutation_and_is_not_formal_admission(tmp_path):
    p=seal(json.loads(DRAFT.read_text()));file=tmp_path/'seal.json';file.write_text(json.dumps(p));assert load_protocol(file)['protocol_hash']==p['protocol_hash']
    p['protocol']['route_a']['coverage_min']=0;file.write_text(json.dumps(p))
    with pytest.raises(ValueError):load_protocol(file)


def test_different_research_questions_do_not_reactivate_old_memories():
    a,b=proxies('calibration'),proxies('confirmation');p=protocol()['protocol']
    good=independent_proxy_checks(a,b,p);assert good['route_a_proxy_supported'] and good['route_b_proxy_supported']
    assert not good['old_gate_changed'] and not good['historical_failed_candidates_promoted'] and good['real_f1'] is None
    a['forecast'][0]['methods']['ridge']['normalized_latent_mse']=1.1
    separate=independent_proxy_checks(a,b,p);assert separate['route_a_proxy_supported'] and not separate['route_b_proxy_supported']
    a,b=proxies('calibration'),proxies('confirmation');a['recurrence']['noise_agreement'][0]=0
    separate=independent_proxy_checks(a,b,p);assert not separate['route_a_proxy_supported'] and separate['route_b_proxy_supported']


def test_missing_controls_or_reused_confirmation_source_never_pass():
    a,b=proxies('calibration'),proxies('confirmation');a['recurrence']['noise_agreement']=[]
    with pytest.raises(ValueError):independent_proxy_checks(a,b,protocol()['protocol'])
    a=proxies('calibration');b['query_source_sha256']=a['query_source_sha256']
    with pytest.raises(ValueError):independent_proxy_checks(a,b,protocol()['protocol'])


def test_one_anonymous_type_is_not_automatically_total_time_collapse():
    r=report();m=r['methods'][0];m['evaluations']['replay']['largest_prototype_share']=1.;m['evaluations']['replay']['matched_window_coverage']=.1
    pub,_=diagnose(r);e=pub['methods'][0]['roles']['replay'];assert e['largest_share_of_matched_windows']==1 and e['largest_share_of_all_windows']==pytest.approx(.1)


def test_pending_sources_block_before_any_query_feature_values(monkeypatch,tmp_path):
    import scripts.validate_b2u1 as v
    from packages.unlabeled import engine
    def forbidden(*args,**kwargs):raise AssertionError('No new source feature read before admission')
    monkeypatch.setattr(engine,'query',forbidden);monkeypatch.setattr(v.np,'load',forbidden)
    records=source_records();records[0]['capture_session_id']=None
    r=v.run_query(protocol(),records,'not-read.yaml',tmp_path/'not-read-parent',tmp_path,'confirmation',{})
    assert r['ready'] is False and (tmp_path/'admission.json').exists()


def test_bad_roles_and_derivative_sources_cannot_bypass_admission():
    r=source_records();r[1]['role']='arbitrary';assert not admission(protocol(),r,set())['ready']
    r=source_records();r[1]['relation']='derived';r[1]['parent_source_sha256']='b'*64
    assert not admission(protocol(),r,set())['ready']


def test_query_proxy_math_and_frozen_receipt_with_generated_numeric_stream(monkeypatch,tmp_path):
    """Actual matching/Ridge/state codec; simulated I/O, NOT field transfer evidence."""
    import numpy as np
    from types import SimpleNamespace
    import scripts.validate_b2u1 as v
    from packages.unlabeled import engine,data
    from packages.unlabeled.study import fit_stream,transform
    from packages.unlabeled.forecast import score_forecasts
    from packages.unlabeled.memory import pack_state
    from packages.contracts.unlabeled import UnlabeledSettings
    from tests.test_b2u_algorithms import periodic
    from packages.local_motion.extraction import write,sha
    p=protocol();p['frozen_algorithm_identity']=engine.code_identity();rows=source_records()
    settings=UnlabeledSettings.model_validate({**p['protocol']['frozen_unlabeled_settings'],'feature_manifest':'unused-unit-manifest.json'})
    stream=periodic();stream.name='frame_difference';stream.variants={
        'roi_left':{'values':stream.values.copy(),'activity':stream.activity.copy()},
        'roi_right':{'values':stream.values.copy(),'activity':stream.activity.copy()},
        'background_mix':{'values':.7*stream.values,'activity':.7*stream.activity}}
    _,model=fit_stream(stream,settings)
    parent=tmp_path/'parent';parent.mkdir()
    state={'branches':[{'fixture':'generated numeric only'}],'models':{'frame_difference':model}}
    pack_state(parent,state)
    write(parent/'report-private.json',{'source':{'sha256':rows[0]['source_sha256']}})
    write(parent/'registry-private.json',{'fixture':'generated only'})
    body={'protocol_hash':p['protocol_hash'],'fit_source_sha256':rows[0]['source_sha256'],'frozen_at':'2024-01-02T12:00:00+00:00',
        'files_sha256':{n:sha(parent/n) for n in ['report-private.json','registry-private.json','model-private.npz','model-state-private.json']},
        'primary_fit_state_hash':model['fit_state_hash']}
    receipt={**body,'receipt_hash':content_hash(body)}
    bundle=SimpleNamespace(config=SimpleNamespace(unlabeled=settings,local_motion=SimpleNamespace(source_sha256=rows[1]['source_sha256']),ingestion=SimpleNamespace(max_expanded_bytes=10000000)))
    monkeypatch.setattr(v,'load_bundle',lambda *args:bundle)
    monkeypatch.setattr(data,'load_features',lambda *args:({},[stream],[]))
    calls=[]
    def query(*args,**kw):
        calls.append(kw);assert kw=={'update':False}
        z=transform(stream.values,model['projection'])
        return {'methods':[{'method':'frame_difference','forecasts':score_forecasts(z,stream.starts,stream.ends,settings,model['forecasts'],stream.activity)}]}
    monkeypatch.setattr(engine,'query',query)
    out=tmp_path/'query';out.mkdir();r=v.run_query(p,rows,'unused.yaml',parent,out,'calibration',receipt)
    assert r['status']=='executed_new_independent_development_proxies' and calls==[{'update':False}]
    result=json.loads((out/'session-proxies-private.json').read_text())
    assert len(result['recurrence']['block_shuffle_coverage_descriptive_only'])==5
    assert len(result['recurrence']['noise_agreement'])==5 and result['recurrence']['background_coverage']==0
    assert all(len(f['history_controls'])==6 for f in result['forecast'])
    assert not result['production_activated'] and all(sha(parent/n)==value for n,value in body['files_sha256'].items())
    (parent/'registry-private.json').write_text('{}')
    with pytest.raises(ValueError,match='Parent changed after model freeze'):
        v.run_query(p,rows,'unused.yaml',parent,tmp_path/'rejected','calibration',receipt)
    assert len(calls)==1
