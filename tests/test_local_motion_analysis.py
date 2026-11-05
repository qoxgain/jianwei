"""No truth means no F1; predictions and manual decisions never share storage."""
import copy,json
from pathlib import Path
import numpy as np
import pytest
from packages.local_motion.analysis import normalize,smoothed,candidates,suppress,analyze
from packages.local_motion.review import compile_review,match_boundaries
from packages.config.errors import ContractError


def report():
    return {'run_id':'r','recipe_hash':'f','source':{'sha256':'a'*64},'settings':{'start_ms':0,'end_ms':10000,'boundary_tolerance_ms':500},
        'primary':{'candidates':[{'id':'one','pts_ms':1000},{'id':'two','pts_ms':4000}]}}


def document():
    return {'schema':'b1-boundary-review-v1','run_id':'r','recipe_hash':'f','source_sha256':'a'*64,'annotator':'generated engineering test',
        'start_ms':0,'end_ms':10000,'coverage_complete':False,'events':[]}


def test_one_to_one_fixed_tolerance_and_empty_truth_never_perfect():
    result=match_boundaries([1000,1100,4000],[1050,8000],500)
    assert result['true_positive']==1 and result['false_positive']==2 and result['false_negative']==1
    assert result['f1']==pytest.approx(.4)
    assert match_boundaries([],[],500)['f1'] is None
    assert match_boundaries([],[1000],500)['f1']==0


def test_confirm_adjust_delete_add_are_separate_and_no_recall_from_partial_review():
    r=report();original=copy.deepcopy(r);d=document();d['events']=[{'id':'e1','target_id':'one','action':'confirm'},
        {'id':'e2','target_id':'one','action':'adjust','pts_ms':1200},{'id':'e3','target_id':'two','action':'delete'},
        {'id':'e4','action':'add','pts_ms':8000,'kind':'onset'}]
    result=compile_review(r,d)
    assert r==original and [v['pts_ms'] for v in result['manual_boundaries']]==[1200,8000]
    assert result['evaluation']['f1'] is None
    d['coverage_complete']=True;result=compile_review(r,d)
    assert result['evaluation']['f1']==.5 and result['evaluation']['annotation_mode']=='candidate_assisted'
    assert not result['evaluation']['independent_generalization']


@pytest.mark.parametrize('key,value',[('run_id','wrong'),('recipe_hash','wrong'),('source_sha256','b'*64)])
def test_review_wrong_source_or_version_is_rejected(key,value):
    d=document();d[key]=value
    with pytest.raises(ContractError) as e:compile_review(report(),d)
    assert e.value.code=='MOTION_REVIEW_MISMATCH'


def test_complete_scope_requires_all_candidate_decisions_and_independent_no_candidate_ids():
    d=document();d['coverage_complete']=True;d['events']=[{'id':'x','target_id':'one','action':'confirm'}]
    assert compile_review(report(),d)['evaluation']['reason']=='UNREVIEWED_CANDIDATES_IN_DECLARED_COMPLETE_SCOPE'
    d['mode']='independent'
    with pytest.raises(ContractError):compile_review(report(),d)
    d['events']=[{'id':'manual','action':'add','pts_ms':1000}]
    assert compile_review(report(),d)['evaluation']['recall']==1


def test_duplicate_events_and_out_of_range_adjustment_rejected():
    d=document();d['events']=[{'id':'x','action':'add','pts_ms':1000}]*2
    with pytest.raises(ContractError):compile_review(report(),d)
    d['events']=[{'id':'x','target_id':'one','action':'adjust','pts_ms':10001}]
    with pytest.raises(ContractError):compile_review(report(),d)


def test_prefix_fit_does_not_use_later_responses_or_fill_invalid_gaps():
    points=[{'pts_ms':i*1000,'raw':v} for i,v in enumerate([1,2,3,None,100,200])]
    fit=normalize(points,2000);assert fit['median']==2 and fit['count']==3
    assert smoothed(points,2000)[3] is None
    assert smoothed(points,2000)[2]<10 # no smoothing across the invalid gap to 100


def test_candidate_budget_is_a_cap_not_a_quota_and_timestamps_remain_observed():
    points=[{'pts_ms':i*1000,'start_ms':i*1000-500,'end_ms':i*1000+500,'normalized':v} for i,v in enumerate([0,0,4,0,0,6,0])]
    series={'id':'hands:r3d','method':'r3d','roi':'hands','role':'hands','points':points}
    cs=candidates(series,2.5,0,500);selected,stats=suppress(cs,500,50)
    assert [c['pts_ms'] for c in selected]==[2000,5000] and stats['budget_omitted']==0
    assert all(c['semantic_action'] is None for c in cs)


def test_unequal_feature_hash_is_rejected_before_vector_read(tmp_path):
    (tmp_path/'features.npz').write_bytes(b'not approved')
    (tmp_path/'manifest.json').write_text(json.dumps({'settings':{},'features_sha256':'a'*64}))
    with pytest.raises(ContractError) as e:analyze(tmp_path)
    assert e.value.code=='MOTION_FEATURE_CHANGED'
