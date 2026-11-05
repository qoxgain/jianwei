"""Generated metadata and synthetic executed predictions, never industrial truth."""
import copy,json
from pathlib import Path
import pytest
from packages.knowledge.service import content_hash
from packages.config.errors import ContractError
from packages.local_motion.annotation import compile_annotations,validate_source,union,subtract
from packages.local_motion.evaluation import evaluate,metrics,compare_annotators

T='2026-11-05T12:00:00Z'


def report():return json.loads((Path(__file__).resolve().parents[1]/'apps/web/src/data/localMotionDemo.json').read_text())


def source():
    r=report();s={'schema':'b11-annotation-source-v1','source':r['source'],'evidence_class':'synthetic_engineering',
        'frames':[{'index':i,'pts_ms':i*100,'end_ms':(i+1)*100} for i in range(120)],'annotation_protocol':'generated contract fixture, not original video frames','clock_mapping':'test'}
    s['descriptor_hash']=content_hash(s);return s


def document():
    s=source();return {'schema':'b11-annotation-v1','annotation_id':'generated-test','source_sha256':s['source']['sha256'],'descriptor_hash':s['descriptor_hash'],
        'annotator':'synthetic engineering test, not a human','mode':'blind','evidence_class':'synthetic_engineering','start_ms':0,'end_ms':12000,
        'created_at':T,'revision':0,'events':[],'status':'draft','coverage_complete':False,'human_attestation':False,'sealed_at':None,'sealed_hash':None}


def event(d,op,**values):
    e={'id':'e'+str(len(d['events'])),'at':T,'actor':d['annotator'],'op':op,'target_id':None,'pts_ms':None,'end_ms':None,'kind':None,'note':''}|values
    d['events'].append(e);d['revision']=len(d['events']);return e['id']


def seal(d):
    d['status']='sealed';d['coverage_complete']=True;d['human_attestation']=True;d['sealed_at']=T
    d['sealed_hash']=content_hash({k:v for k,v in d.items() if k!='sealed_hash'});return d


def complete():
    d=document();event(d,'interval_add',pts_ms=0,end_ms=12000,kind='reviewed')
    for c in report()['primary']['candidates']:event(d,'boundary_add',pts_ms=c['pts_ms'],kind='onset')
    return seal(d)


def run(d,r=None,**kw):return evaluate(source(),r or report(),d,report_file_sha256='a'*64,code_identity={'test':'synthetic'},**kw)


def test_incomplete_draft_never_creates_metrics_or_rewrites_candidates():
    r=report();before=copy.deepcopy(r);out=run(document(),r)
    assert r==before and out['status']=='blocked' and len(out['methods'])==14
    assert all(v['f1'] is None for v in out['methods'])


def test_boolean_complete_without_real_interval_coverage_is_blocked():
    d=seal(document());assert run(d)['status']=='blocked'
    assert compile_annotations(source(),d)['unreviewed']==[(0,12000)]


def test_missing_final_half_second_cannot_pass_full_coverage():
    d=document();event(d,'interval_add',pts_ms=0,end_ms=11500,kind='reviewed');seal(d)
    a=compile_annotations(source(),d);assert a['unreviewed']==[(11500,12000)] and not a['eligible_for_evaluation']


def test_full_synthetic_annotation_uses_frozen_primary_and_all_methods_same_scope():
    out=run(complete());assert out['status']=='evaluated_synthetic_engineering_only'
    primary=next(m for m in out['methods'] if m['method']=='local_combined_original')
    assert primary['f1']==1 and primary['boundary_error_mae_ms']==0
    assert all(m['evaluated_duration_ms']==12000 for m in out['methods'])
    assert [s['tolerance_ms'] for s in primary['tolerance_sensitivity']]==[250,1000]
    assert out['feature_support_end_ms']==12000 and not out['threshold_selection_performed']


@pytest.mark.parametrize('field,value',[('source_sha256','b'*64),('descriptor_hash','b'*64),('end_ms',11500)])
def test_cross_source_or_cropped_scope_rejected(field,value):
    d=document();d[field]=value
    with pytest.raises(ContractError):compile_annotations(source(),d)


def test_update_delete_and_review_preserve_the_operation_log():
    d=document();key=event(d,'boundary_add',pts_ms=1000,kind='onset');event(d,'boundary_update',target_id=key,pts_ms=1100,kind='offset')
    event(d,'boundary_review',target_id=key,note='test review only');other=event(d,'boundary_add',pts_ms=3000,kind='pause');event(d,'boundary_delete',target_id=other)
    a=compile_annotations(source(),d);assert len(d['events'])==5 and len(a['boundaries'])==1
    assert a['boundaries'][0]['pts_ms']==1100 and a['boundaries'][0]['reviewed_by']


def test_interval_edit_and_retract_updates_holes_without_deleting_audit():
    d=document();key=event(d,'interval_add',pts_ms=0,end_ms=12000,kind='reviewed');event(d,'interval_update',target_id=key,pts_ms=0,end_ms=11000,kind='reviewed')
    assert compile_annotations(source(),d)['unreviewed']==[(11000,12000)]
    event(d,'interval_delete',target_id=key);assert compile_annotations(source(),d)['unreviewed']==[(0,12000)]


def test_exposure_irreversibly_invalidates_a_blind_claim():
    d=document();event(d,'exposure',note='viewed algorithms')
    with pytest.raises(ContractError) as e:compile_annotations(source(),d)
    assert e.value.code=='ANNOTATION_EXPOSURE'
    d['mode']='assisted';assert compile_annotations(source(),d)['annotation_mode']=='assisted'


def test_seal_hash_rejects_modification_and_revision_requires_every_event():
    d=complete();d['events'][0]['note']='modified after freeze'
    with pytest.raises(ContractError) as e:compile_annotations(source(),d)
    assert e.value.code=='ANNOTATION_SEAL'
    d=document();d['revision']=1
    with pytest.raises(ContractError):compile_annotations(source(),d)


def test_lighting_no_action_and_background_invalid_are_not_removed_to_improve_metrics():
    d=complete();d['status']='draft'
    for kind in ['lighting','no_action','background_invalid']:event(d,'interval_add',pts_ms=0,end_ms=12000,kind=kind)
    seal(d);out=run(d)
    assert all(m['evaluated_duration_ms']==12000 for m in out['methods'])
    for m in out['methods']:
        assert m['interference_context']['lighting_unmatched_predictions']==m['false_positive']


def test_mask_is_common_and_tolerance_cannot_match_across_unknown_intervals():
    a={'valid_intervals':[(0,1000),(1050,2000)],'intervals':[]}
    m=metrics([{'pts_ms':900}],[{'pts_ms':1100}],a,500,2000)
    assert m['true_positive']==0 and m['false_positive']==1 and m['false_negative']==1
    d=complete();d['status']='draft';event(d,'interval_add',pts_ms=2000,end_ms=2500,kind='occluded');seal(d)
    out=run(d);assert all(m['evaluated_duration_ms']==11500 for m in out['methods'])


def test_false_alarm_rate_duplicate_near_truth_and_timing_error_are_explicit():
    a={'valid_intervals':[(0,10000)],'intervals':[]}
    m=metrics([{'pts_ms':1000},{'pts_ms':1100},{'pts_ms':9000}],[{'pts_ms':1050}],a,500,10000)
    assert m['false_positive']==2 and m['false_positives_per_minute']==12
    assert m['extra_predictions_near_already_matched_truth']==1 and m['boundary_error_mae_ms']==50
    assert m['semantic_type_accuracy'] is None


def test_second_independent_rater_disagreement_is_not_merged_into_truth():
    a=complete();b=copy.deepcopy(a);b['annotation_id']='second';b['annotator']='second generated rater'
    b['events'][1]['kind']='offset';seal(b)
    before=copy.deepcopy(a);out=compare_annotators(source(),a,b)
    assert a==before and out['type_disagreements'] and out['requires_human_resolution']
    with pytest.raises(ContractError):compare_annotators(source(),a,a)


def test_wrong_descriptor_identity_and_pts_gaps_are_rejected():
    s=source();s['source']['bytes']+=1
    with pytest.raises(ContractError):validate_source(s)
    s=source();s['frames'][5]['pts_ms']+=1;s['descriptor_hash']=content_hash({k:v for k,v in s.items() if k!='descriptor_hash'})
    with pytest.raises(ContractError):validate_source(s)


def test_development_report_cannot_be_swapped_for_recomputed_or_tuned_predictions():
    s=source();s['evidence_class']='development_exploration';s['descriptor_hash']=content_hash({k:v for k,v in s.items() if k!='descriptor_hash'})
    r=report();r['evidence_class']='development_exploration';d=document();d.update(evidence_class=s['evidence_class'],descriptor_hash=s['descriptor_hash'])
    with pytest.raises(ContractError) as e:evaluate(s,r,d,report_file_sha256='a'*64,code_identity={})
    assert e.value.code=='EVALUATION_FROZEN_REPORT'


def test_empty_exhaustive_truth_is_not_a_fabricated_perfect_f1():
    d=document();event(d,'interval_add',pts_ms=0,end_ms=12000,kind='reviewed');seal(d)
    out=run(d);assert all(v['f1'] is None and v['recall'] is None for v in out['methods'])


def test_interval_union_is_exact_and_does_not_discard_unreviewed_tail():
    assert union([(100,200),(0,150),(300,400)])==[(0,200),(300,400)]
    assert subtract(0,500,[(0,200),(300,400)])==[(200,300),(400,500)]


def test_coincident_typed_boundaries_require_human_resolution_not_automatic_truth_merge():
    d=complete();d['status']='draft';pts=d['events'][1]['pts_ms'];event(d,'boundary_add',pts_ms=pts,kind='offset');seal(d)
    out=run(d);assert out['status']=='blocked' and len(out['annotation']['boundaries'])==len(d['events'])-1
    assert out['annotation']['reason']=='COINCIDENT_HUMAN_BOUNDARIES_REQUIRE_ADJUDICATION'


def test_explicit_frame_duration_cannot_bridge_a_real_pts_gap():
    from scripts.prepare_b11_annotation import frames_from_probe
    raw=[{'best_effort_timestamp_time':str(t),'duration_time':'.04'} for t in [0,.04,.12]]
    with pytest.raises(ValueError,match='gap/overlap'):frames_from_probe(raw)


def test_variable_frame_durations_and_rounding_are_not_replaced_by_average_fps():
    from scripts.prepare_b11_annotation import frames_from_probe
    raw=[{'best_effort_timestamp_time':'0','duration_time':'.04'},
        {'best_effort_timestamp_time':'.04','duration_time':'.06'},{'best_effort_timestamp_time':'.1','duration_time':'.05'}]
    assert [r['end_ms'] for r in frames_from_probe(raw)]==[40,100,150]


def test_missing_frame_duration_is_blocked_instead_of_guessed():
    from scripts.prepare_b11_annotation import frames_from_probe
    with pytest.raises(ValueError,match='duration required'):frames_from_probe([{'best_effort_timestamp_time':'0'}])
