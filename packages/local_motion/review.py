"""Manual boundary decisions are a separate, source/run-bound append-only log."""
from typing import Literal
from pydantic import Field,model_validator
from packages.contracts.models import Strict
from packages.config.errors import ContractError
from packages.knowledge.service import content_hash


class ReviewEvent(Strict):
    id:str=Field(min_length=1,max_length=100)
    target_id:str|None=None
    action:Literal['confirm','adjust','delete','add']
    pts_ms:int|None=Field(default=None,ge=0)
    kind:Literal['onset','offset','pause','switch','unspecified']='unspecified'
    note:str=Field(default='',max_length=1000)


class ReviewDocument(Strict):
    schema_version:Literal['b1-boundary-review-v1']=Field(default='b1-boundary-review-v1',alias='schema')
    run_id:str
    source_sha256:str
    recipe_hash:str
    annotator:str=Field(min_length=1,max_length=100)
    start_ms:int=Field(ge=0)
    end_ms:int=Field(gt=0)
    coverage_complete:bool=False
    mode:Literal['candidate_assisted','independent']='candidate_assisted'
    events:list[ReviewEvent]=Field(default_factory=list,max_length=10000)


def compile_review(report,document):
    d=ReviewDocument.model_validate(document)
    if (d.run_id,d.source_sha256,d.recipe_hash)!=(report['run_id'],report['source']['sha256'],report['recipe_hash']):raise ContractError('MOTION_REVIEW_MISMATCH','人工边界与来源/配方/实验版本不匹配')
    s=report['settings']
    if not s['start_ms']<=d.start_ms<d.end_ms<=s['end_ms']:raise ContractError('MOTION_REVIEW_RANGE','人工覆盖范围超出实验')
    predicted={c['id']:c for c in report['primary']['candidates']};decisions={};manual={};seen=set()
    for event in d.events:
        if event.id in seen:raise ContractError('MOTION_REVIEW_DUPLICATE','复核事件ID不能重复')
        seen.add(event.id)
        if event.action=='add':
            if event.pts_ms is None:raise ContractError('MOTION_REVIEW_TIME','新增边界必须有PTS')
            manual['manual:'+event.id]={'id':'manual:'+event.id,'pts_ms':event.pts_ms,'kind':event.kind}
        elif event.target_id in manual:
            if event.action=='delete':manual.pop(event.target_id)
            else:raise ContractError('MOTION_REVIEW_TARGET','手工边界修订应删除并新增，保留记录')
        else:
            if event.target_id not in predicted or d.mode=='independent':raise ContractError('MOTION_REVIEW_TARGET','未知候选或独立标注中引用算法候选')
            if event.action=='delete':decisions[event.target_id]=None
            else:
                point=predicted[event.target_id]['pts_ms'] if event.action=='confirm' else event.pts_ms
                if point is None:raise ContractError('MOTION_REVIEW_TIME','修订边界必须有PTS')
                decisions[event.target_id]={'id':event.target_id,'pts_ms':point,'kind':event.kind}
    truth=[v for v in decisions.values() if v is not None]+list(manual.values())
    if any(not d.start_ms<=v['pts_ms']<=d.end_ms for v in truth):raise ContractError('MOTION_REVIEW_TIME','边界PTS超出人工审核覆盖范围')
    evaluation={'status':'blocked','reason':'NO_EXHAUSTIVE_HUMAN_BOUNDARIES','precision':None,'recall':None,'f1':None,'tolerance_ms':s['boundary_tolerance_ms']}
    in_scope=[c for c in predicted.values() if d.start_ms<=c['pts_ms']<=d.end_ms]
    if d.coverage_complete:
        if d.mode=='candidate_assisted' and any(c['id'] not in decisions for c in in_scope):evaluation['reason']='UNREVIEWED_CANDIDATES_IN_DECLARED_COMPLETE_SCOPE'
        else:evaluation=match_boundaries([c['pts_ms'] for c in in_scope],[v['pts_ms'] for v in truth],s['boundary_tolerance_ms'])
    return {'schema':'b1-reviewed-boundaries-v1','run_id':d.run_id,'source_sha256':d.source_sha256,'recipe_hash':d.recipe_hash,
        'manual_document':d.model_dump(mode='json',by_alias=True),'manual_hash':content_hash(d.model_dump(mode='json',by_alias=True)),'manual_boundaries':sorted(truth,key=lambda v:v['pts_ms']),
        'algorithm_predictions_unchanged':True,'evaluation':{**evaluation,'annotation_mode':d.mode,'independent_generalization':False}}


def match_boundaries(predicted,truth,tolerance):
    # Ordered one-to-one maximum-cardinality tolerance matching, no reuse of a
    # truth event; absent/empty truth cannot yield a fabricated perfect F1.
    p=sorted(predicted);t=sorted(truth);i=j=0;matches=[];false_positive=[];false_negative=[]
    while i<len(p) and j<len(t):
        if p[i]<t[j]-tolerance:false_positive.append(p[i]);i+=1
        elif t[j]<p[i]-tolerance:false_negative.append(t[j]);j+=1
        else:matches.append({'prediction_ms':p[i],'truth_ms':t[j],'error_ms':p[i]-t[j]});i+=1;j+=1
    false_positive.extend(p[i:]);false_negative.extend(t[j:]);tp=len(matches)
    precision=tp/len(p) if p else None;recall=tp/len(t) if t else None
    return {'status':'evaluated_development_only' if t else 'blocked','reason':None if t else 'EMPTY_HUMAN_BOUNDARIES',
        'precision':precision,'recall':recall,'f1':2*tp/(len(p)+len(t)) if t else None,'tolerance_ms':tolerance,
        'true_positive':tp,'false_positive':len(false_positive),'false_negative':len(false_negative),'matches':matches,
        'unmatched_predictions_ms':false_positive,'unmatched_truth_ms':false_negative,'matching_scope':'type_agnostic_boundary_time_only'}
