"""Generated numeric mechanisms, never evidence of industrial action truth."""
import copy,json
from types import SimpleNamespace
from pathlib import Path
import numpy as np
import pytest
from packages.contracts.unlabeled import UnlabeledSettings
from packages.config.errors import ContractError
from packages.config.loader import load_bundle,PROJECT_ROOT
from packages.unlabeled.data import Stream,time_roles,cross_source_admission
from packages.unlabeled.algorithms import descriptor,subsequences,exact_profile,discover,segmentation,fit_segmentation,assignment_agreement
from packages.unlabeled.forecast import pairs,fit_forecasts,score_forecasts
from packages.unlabeled.study import fit_stream
from packages.unlabeled.memory import pack_state,load_state,shadow_registry
from packages.knowledge.service import content_hash
from scripts.prepare_b2u import research_settings


def settings(**changes):
    raw=research_settings('private-test-manifest.json');raw.update(seeds=[11,23,37],**changes);return UnlabeledSettings.model_validate(raw)


def periodic(n=120):
    phase=np.arange(n)*np.pi/4;values=np.c_[np.sin(phase),np.cos(phase)]
    starts=np.arange(n)*1000;return Stream('generated_numeric_fixture',values,starts,starts+2000,np.ones(n),{}, {'fixture':True})


def test_old_config_snapshot_hash_does_not_acquire_an_empty_research_field():
    b=load_bundle(PROJECT_ROOT/'config/system.yaml');assert 'unlabeled' not in b.snapshot


@pytest.mark.parametrize('change',[{'production_updates':True},{'formal_research_access':True},{'fit_fraction':.7,'calibration_fraction':.25},{'motif_lengths':[2,2]}])
def test_research_config_forbids_production_test_access_and_invalid_budgets(change):
    with pytest.raises(ValueError):settings(**change)


def test_exact_profile_matches_brute_force_and_excludes_shared_original_frames():
    records=[{'start_ms':i*1000,'end_ms':i*1000+3000} for i in range(7)];x=np.array([[i%3,1] for i in range(7)],dtype=float)
    profile,nearest,d=exact_profile(x,records)
    for i in range(len(x)):
        allowed=[j for j in range(len(x)) if records[j]['start_ms']>=records[i]['end_ms'] or records[i]['start_ms']>=records[j]['end_ms']]
        assert profile[i]==pytest.approx(min(np.linalg.norm(x[i]-x[j]) for j in allowed))
        assert nearest[i] in allowed and np.isinf(d[i,i])
    assert np.isinf(d[0,2]) and d[0,3]==0 # sharing even one input frame is excluded


def test_constant_subsequences_cannot_become_a_stable_action_prototype():
    s=settings();z=np.zeros((40,2));starts=np.arange(40)*1000;records,v=subsequences(z,starts,starts+2000,np.ones(40),4,.15,.1,.25)
    assert not records and not len(v)
    p,profiles=discover(z,starts,starts+2000,np.ones(40),s,.1);assert p==[] and all(r['status']=='no_nonoverlapping_dynamic_pairs' for r in profiles)


def test_dynamic_generated_pattern_has_disjoint_repeats_and_anonymous_only():
    a=periodic();s=settings();p,_=discover(a.values[:60],a.starts[:60],a.ends[:60],a.activity[:60],s,.5)
    assert p
    for proto in p:
        events=[proto['records'][i] for i in proto['members']]
        assert len(events)>=3 and all(x['end_ms']<=y['start_ms'] for x,y in zip(events,events[1:]))
        assert proto['radius']>=2*s.noise_sigma


def test_segmentation_partitions_cells_without_overlapping_raw_clip_claims():
    a=periodic();s=settings();params=fit_segmentation(a.values[:50],a.starts[:50],a.ends[:50],s)
    methods,_,_=segmentation(a.values,a.starts,a.ends,s,params)
    assert {m['method'] for m in methods}=={'simple_change','multiscale_change','consistent_dp','fixed_windows'}
    for method in methods:
        spans=method['segments'];assert spans[0]['start_ms']==0 and spans[-1]['end_ms']==a.ends[-1]
        assert all(x['end_ms']==y['start_ms'] for x,y in zip(spans,spans[1:]))
        assert method['real_boundary_f1'] is None
    assert not next(m for m in methods if m['method']=='fixed_windows')['feature_responsive']


def test_chronological_roles_and_prediction_targets_never_share_support():
    a=periodic();s=settings();roles=time_roles(a,s)
    assert a.ends[roles['fit'][-1]]<=a.starts[roles['calibration'][0]]
    assert a.ends[roles['calibration'][-1]]<=a.starts[roles['replay'][0]]
    x,y,indices=pairs(a.values,a.starts,a.ends,6,1);assert len(x)==0 # overlapping 2s clips are not a forecast
    x,y,indices=pairs(a.values,a.starts,a.ends,6,2);assert len(x)>0
    assert all(a.starts[i+2]>=a.ends[i] for i in indices)


def test_poisoning_replay_cannot_change_fitted_projection_prototypes_or_gate():
    a=periodic();s=settings();report,model=fit_stream(a,s);altered=copy.deepcopy(a)
    roles=time_roles(a,s);altered.values[roles['replay']]=np.random.default_rng(999).normal(100,20,altered.values[roles['replay']].shape)
    other,new_model=fit_stream(altered,s)
    assert model['fit_state_hash']==new_model['fit_state_hash'] and model['gate_state_hash']==new_model['gate_state_hash']
    assert report['shadow_gate']==other['shadow_gate']
    assert all(np.array_equal(model['projection'][k],new_model['projection'][k]) for k in model['projection'])
    assert report['real_f1'] is None and report['production_activated'] is False
    assert report['evaluations']['replay']['forecasts']!=other['evaluations']['replay']['forecasts']


def test_all_unknown_assignments_have_no_fabricated_perfect_consistency():
    assert assignment_agreement(np.array([-1,-1]),np.array([-1,-1])) is None
    assert assignment_agreement(np.array([0,-1]),np.array([-1,-1]))==0


def evidence(sha,root,session,date,**changes):
    return {'source_sha256':sha,'root_source_sha256':root,'kind':'generated','independence_review':'verified','near_duplicate_review':'clear',
        'capture_session_id':session,'view_id':'same_generated_view','captured_at':date,**changes}


def cross_settings(**change):
    rows=[evidence('a'*64,'a'*64,'one','2026-01-01T00:00:00Z'),evidence('b'*64,'b'*64,'two','2026-01-02T00:00:00Z',**change)]
    return settings(source_evidence=rows)


@pytest.mark.parametrize('change,code',[({'independence_review':'pending'},'UNLABELED_INDEPENDENCE_UNKNOWN'),
    ({'root_source_sha256':'a'*64},'UNLABELED_SAME_ROOT'),({'capture_session_id':'one'},'UNLABELED_SAME_ROOT'),
    ({'captured_at':'2025-01-01T00:00:00Z'},'UNLABELED_SOURCE_ORDER'),({'view_id':'different'},'UNLABELED_SPACE_CHANGED')])
def test_different_files_or_reencoded_children_are_not_independent_evidence(change,code):
    with pytest.raises(ContractError) as err:cross_source_admission({'sha256':'a'*64},{'sha256':'b'*64},cross_settings(**change),{'v':1},{'v':1})
    assert err.value.code==code


def test_known_generated_source_transfer_does_not_count_as_field_generalization():
    r=cross_source_admission({'sha256':'a'*64},{'sha256':'b'*64},cross_settings(),{'v':1},{'v':1});assert r['kind']=='generated' and not r['query_updates_model']
    with pytest.raises(ContractError):cross_source_admission({'sha256':'a'*64},{'sha256':'b'*64},cross_settings(),{'v':2},{'v':1})


def test_numeric_memory_roundtrip_is_pickle_free_and_tampering_is_rejected(tmp_path):
    original={'forecast':{2:{'weights':np.array([[1.,2.]])}},'prototype':[np.arange(4)],'none':None}
    pack_state(tmp_path,original);loaded=load_state(tmp_path)
    assert np.array_equal(loaded['forecast'][2]['weights'],original['forecast'][2]['weights'])
    (tmp_path/'model-private.npz').write_bytes(b'changed')
    with pytest.raises(ContractError):load_state(tmp_path)


def test_shadow_memory_is_additive_and_never_overwrites_production_or_rejected_candidates():
    base={'source':{'sha256':'a'*64},'run_id':'run1','methods':[{'method':'fixture','status':'computed','fit':{'state_hash':'state'},'shadow_gate':{'passed':False},'prototypes':[{'id':'p0'}]}]}
    empty=shadow_registry(base);assert not empty['entries']
    base['methods'][0]['shadow_gate']['passed']=True;one=shadow_registry(base);copy_one=copy.deepcopy(one)
    base['source']['sha256']='b'*64;base['run_id']='run2';two=shadow_registry(base,one)
    assert one==copy_one and two['entries'][:len(one['entries'])]==one['entries'] and two['parent_version']==one['version']
    assert not two['production_activated'] and two['scope']=='experimental_only'
    with pytest.raises(ContractError):shadow_registry(base,{**one,'scope':'production'})


def test_constant_stream_reports_degeneracy_not_a_perfect_prediction():
    a=periodic();a.values[:]=1;r,m=fit_stream(a,settings());assert m is None and r['status']=='degenerate_constant_input' and r['real_f1'] is None


def test_research_gate_has_an_executable_positive_mechanism_without_human_truth():
    r,_=fit_stream(periodic(),settings());assert r['shadow_gate']['passed'] and r['shadow_gate']['active_prototypes']>=1
    assert all(v['methods']['prototype_continuation']['gain_vs_persistence']>0 for v in r['evaluations']['calibration']['forecasts'])
    assert r['real_precision'] is None and r['real_recall'] is None and r['real_f1'] is None


def test_background_repetition_is_retained_as_a_negative_but_never_promoted():
    a=periodic();a.background=True;r,_=fit_stream(a,settings())
    assert r['prototypes'] and not r['shadow_gate']['passed'] and not r['shadow_gate']['checks']['not_background_control']


def test_memory_metadata_and_resource_budget_are_checked(tmp_path):
    with pytest.raises(ContractError):pack_state(tmp_path,{'x':np.ones(100)},max_bytes=1)
    pack_state(tmp_path,{'x':np.ones(2)})
    f=tmp_path/'model-state-private.json';r=json.loads(f.read_text());r['payload']['items'][0][0]='different_key';f.write_text(json.dumps(r))
    with pytest.raises(ContractError):load_state(tmp_path)


def test_cache_values_are_not_opened_when_formal_source_guard_rejects(monkeypatch,tmp_path):
    from packages.unlabeled import data
    b=SimpleNamespace(config=SimpleNamespace(unlabeled=settings()))
    def forbidden(*args,**kw):raise AssertionError('Feature values must not be read')
    def sealed(*args):raise ContractError('MOTION_SEALED_FORBIDDEN','generated guard test')
    monkeypatch.setattr(data,'admission',sealed);monkeypatch.setattr(np,'load',forbidden)
    with pytest.raises(ContractError) as e:data.load_features(b)
    assert e.value.code=='MOTION_SEALED_FORBIDDEN'


def test_too_short_video_after_support_purging_is_not_filled_or_split_into_fake_sources():
    with pytest.raises(ContractError) as e:time_roles(periodic(16),settings())
    assert e.value.code=='UNLABELED_TOO_SHORT'


def test_discovered_support_is_inside_the_final_representative_radius():
    s=settings();z=np.random.default_rng(3).normal(size=(80,3));starts=np.arange(80)*1000;activity=np.ones(80)
    prototypes,_=discover(z,starts,starts+2000,activity,s,0.)
    assert prototypes,'exercise actual nontrivial groups'
    for p in prototypes:
        assert p['medoid'] in p['members']
        for i in p['members']:
            r=p['records'][i];v=descriptor(z[r['row_start']:r['row_end']],s.context_weight)
            assert np.linalg.norm(v-p['descriptor'])<=p['radius']+1e-12
