"""Frozen B.1 predictions, common human scope, no threshold selection."""
import numpy as np
from packages.config.errors import ContractError
from .annotation import compile_annotations
from .analysis import suppress
from .review import match_boundaries


def frozen_methods(report):
    s=report['settings'];primary=report['primary']
    if s['boundary_tolerance_ms']!=500:raise ContractError('EVALUATION_PROTOCOL','Primary tolerance must remain 500 ms')
    arm=next(a for a in report['arms'] if a['id']==primary['arm'])
    chosen=[g for g in arm['grid'] if g['threshold']==primary['threshold'] and g['smoothing_ms']==primary['smoothing_ms']]
    single={g['series']:g['candidates'] for g in chosen}
    result={key:list(single[key]) for key in ['global:r3d','hands:r3d','interaction:r3d','hands:frame_difference','interaction:frame_difference','hands:block_motion','interaction:block_motion','background:r3d','background:frame_difference','background:block_motion']}
    for label,methods in [('local_r3d',['r3d']),('local_frame_difference',['frame_difference']),('local_block_motion',['block_motion'])]:
        pool=[c for method in methods for roi in ['hands','interaction'] for c in single[roi+':'+method]]
        result[label]=suppress(pool,s['refractory_ms'],s['candidate_budget'])[0]
    result['local_combined_original']=list(primary['candidates'])
    return result


def segment(pts,intervals,source_end):
    return next((i for i,(start,end) in enumerate(intervals) if start<=pts<end or pts==end==source_end),None)


def metrics(predictions,truth,annotation,tolerance,end):
    valid=annotation['valid_intervals'];predicted=[c['pts_ms'] for c in predictions];actual=[c['pts_ms'] for c in truth]
    combined={'matches':[],'unmatched_predictions_ms':[],'unmatched_truth_ms':[]};ignored_predictions=[];ignored_truth=[]
    for i,interval in enumerate(valid):
        p=[t for t in predicted if segment(t,valid,end)==i];a=[t for t in actual if segment(t,valid,end)==i]
        m=match_boundaries(p,a,tolerance)
        for key in combined:combined[key].extend(m[key])
    ignored_predictions=[t for t in predicted if segment(t,valid,end) is None];ignored_truth=[t for t in actual if segment(t,valid,end) is None]
    tp=len(combined['matches']);fp=len(combined['unmatched_predictions_ms']);fn=len(combined['unmatched_truth_ms']);duration=sum(b-a for a,b in valid)
    errors=np.array([m['error_ms'] for m in combined['matches']],dtype=float)
    contexts={}
    for name in ['lighting','no_action','background_invalid']:
        intervals=[v for v in annotation['intervals'] if v['kind']==name]
        contexts[name+'_unmatched_predictions']=sum(any(v['start_ms']<=t<v['end_ms'] for v in intervals) for t in combined['unmatched_predictions_ms'])
    duplicates=sum(any(abs(t-a)<=tolerance and segment(t,valid,end)==segment(a,valid,end) for a in actual if segment(a,valid,end) is not None) for t in combined['unmatched_predictions_ms'])
    return {**combined,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,
        'f1':2*tp/(2*tp+fp+fn) if tp+fn else None,'true_positive':tp,'false_positive':fp,'false_negative':fn,
        'false_positives_per_minute':fp/(duration/60000) if duration else None,'evaluated_duration_ms':duration,
        'excluded_predictions':len(ignored_predictions),'excluded_truth':len(ignored_truth),'tolerance_ms':tolerance,
        'boundary_error_mae_ms':float(abs(errors).mean()) if len(errors) else None,'boundary_error_p95_ms':float(np.quantile(abs(errors),.95)) if len(errors) else None,
        'boundary_error_bias_ms':float(errors.mean()) if len(errors) else None,'extra_predictions_near_already_matched_truth':duplicates,
        'interference_context':contexts,'matching_rule':'historical ordered one-to-one maximum-cardinality, not minimum-error assignment',
        'semantic_type_accuracy':None,'semantic_type_reason':'algorithm candidates have no observed action semantics'}


def compare_annotators(source,first,second):
    a=compile_annotations(source,first);b=compile_annotations(source,second)
    if a['annotation_id']==b['annotation_id'] or a['annotator']==b['annotator']:raise ContractError('ANNOTATION_SECOND_RATER','Second independent document and annotator required')
    if not a['eligible_for_evaluation'] or not b['eligible_for_evaluation']:return {'status':'blocked','reason':'BOTH_RATERS_REQUIRE_COMPLETE_SEALED_COVERAGE'}
    if a['annotation_mode']!='blind' or b['annotation_mode']!='blind':return {'status':'blocked','reason':'SECOND_RATER_NOT_INDEPENDENT_BLIND'}
    # Rater agreement uses a common mask, never substitutes consensus truth.
    from .annotation import union,subtract
    common={**a,'valid_intervals':subtract(first['start_ms'],first['end_ms'],union(a['excluded']+b['excluded']))}
    result=metrics([{'pts_ms':x['pts_ms']} for x in b['boundaries']],a['boundaries'],common,500,first['end_ms'])
    by_a={v['pts_ms']:v['kind'] for v in a['boundaries']};by_b={v['pts_ms']:v['kind'] for v in b['boundaries']}
    disagreements=[{**m,'first_kind':by_a[m['truth_ms']],'second_kind':by_b[m['prediction_ms']]} for m in result['matches'] if by_a[m['truth_ms']]!=by_b[m['prediction_ms']]]
    return {'status':'rater_agreement_only_not_consensus_truth','first_hash':a['annotation_hash'],'second_hash':b['annotation_hash'],
        'agreement':result,'type_disagreements':disagreements,'requires_human_resolution':True}


def evaluate(source,report,document,*,report_file_sha256,code_identity,baseline=None,second=None):
    s=source['source']
    if report['source']['sha256']!=s['sha256'] or report['evidence_class']!=source['evidence_class'] or report.get('formal_research_passed') or report.get('b2_started'):raise ContractError('EVALUATION_SOURCE','Only matching B.1 development/synthetic sources allowed')
    if report['evidence_class']=='development_exploration':
        if not baseline or (report_file_sha256,report['run_id'],report['recipe_hash'])!=(baseline['private_report_sha256'],baseline['run_id'],baseline['recipe_hash']):raise ContractError('EVALUATION_FROZEN_REPORT','须使用原B.1报告原字节；禁止重跑分析后替换36候选或调主阈值')
    a=compile_annotations(source,document);methods=frozen_methods(report);eligible=a['eligible_for_evaluation']
    results=[]
    for name,predictions in methods.items():
        result={'method':name,'predictions_total':len(predictions),'background_control':'contaminated_or_unverified' if name.startswith('background:') else 'not_background',
            'historical_extraction_cost':None,'cost_reason':'B.1 did not retain per-module timers; see separate bounded profiling, not retroactive estimates'}
        if eligible:
            result.update(metrics(predictions,a['boundaries'],a,500,s['last_pts_end_ms']))
            result['tolerance_sensitivity']=[metrics(predictions,a['boundaries'],a,t,s['last_pts_end_ms']) for t in [250,1000]]
        else:result.update(precision=None,recall=None,f1=None,reason=a['reason'])
        results.append(result)
    return {'schema':'b11-frozen-boundary-evaluation-v1','status':'evaluated_retrospective_development_only' if eligible else 'blocked',
        'evidence_class':source['evidence_class'],'report_file_sha256':report_file_sha256,'candidate_run_id':report['run_id'],'recipe_hash':report['recipe_hash'],
        'source_sha256':s['sha256'],'descriptor_hash':source['descriptor_hash'],'code_identity':code_identity,
        'annotation':a,'methods':results,'primary_tolerance_ms':500,'threshold_selection_performed':False,
        'original_candidates_unchanged':True,'original_primary_candidates':report['primary']['count'],
        'feature_support_end_ms':max(p['end_ms'] for arm in report['arms'] if arm['id']==report['primary']['arm'] for series in arm['series'] for p in series['points']),
        'source_end_ms':s['last_pts_end_ms'],'full_source_scope_includes_unpredicted_tail':True,
        'common_scope_rule':'all methods use full reviewed source and identical uncertain/occluded exclusions; lighting/no_action/background_invalid not selectively removed',
        'second_rater':compare_annotators(source,document,second) if second else {'status':'blocked','reason':'NO_SECOND_INDEPENDENT_HUMAN'},
        'independent_generalization':False,'b2_started':False,'p04_admitted':False,'b2_recommendation':'requires_human_review_and_user_decision'}
