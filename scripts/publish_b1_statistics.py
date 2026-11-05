"""Explicit aggregate-only publication boundary for private B.1 executions.

Never copies curves, candidates, annotations, frames or embedding values. The
private report remains the source of truth; this is not a general redactor.
"""
import argparse,json,hashlib
from pathlib import Path
from packages.local_motion.analysis import suppress
from packages.local_motion.review import match_boundaries
from scripts.check_public_research_artifacts import inspect_json


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def summarize(report,background_status='unverified_over_full_video'):
    if report['schema']!='b1-local-motion-report-v1':raise ValueError('Unsupported executed report')
    if report['formal_research_passed'] or report['b2_started']:raise ValueError('B.1 cannot publish formal admission')
    arms=[]
    for arm in report['arms']:
        arms.append({k:arm[k] for k in ['id','window_ms','fps','operation_background','offset_sensitivity']} | {
            'series_statistics':[{k:s[k] for k in ['id','roi','role','method','stats']} for s in arm['series']],
            'grid':[{k:g[k] for k in ['series','threshold','smoothing_ms','count']} for g in arm['grid']]})
    primary=report['primary'];arm=next(a for a in report['arms'] if a['id']==primary['arm']);s=report['settings']
    grid=[g for g in arm['grid'] if g['threshold']==primary['threshold'] and g['smoothing_ms']==primary['smoothing_ms']]
    local_ids={v['id'] for v in arm['series'] if v['role'] in {'hands','interaction'}}
    pools={}
    for label,accept in [('r3d_only',lambda c:c['series'].endswith(':r3d')),('motion_only',lambda c:not c['series'].endswith(':r3d'))]:
        pools[label]=suppress([c for g in grid if g['series'] in local_ids for c in g['candidates'] if accept(c)],s['refractory_ms'],s['candidate_budget'])[0]
    # Matching counts describe agreement BETWEEN algorithms, never truth F1.
    agreement=match_boundaries([c['pts_ms'] for c in pools['r3d_only']],[c['pts_ms'] for c in pools['motion_only']],s['boundary_tolerance_ms'])
    result={'schema':'b1-public-aggregate-v1','evidence_class':report['evidence_class'],
        'run_id':report['run_id'],'recipe_hash':report['recipe_hash'],'features_sha256':report['features_sha256'],
        'source':{k:report['source'][k] for k in ['asset_id','sha256','bytes','width','height','first_pts_ms']},
        'encoder':report['encoder'],'model_sha256':report['model_sha256'],'settings':s,
        'feature_rows':report['feature_rows'],'elapsed_extraction_seconds':report['elapsed_extraction_seconds'],
        'quality':report['quality'],'primary':{k:primary[k] for k in ['arm','threshold','smoothing_ms','count','selection','budget_is_not_a_quota']},
        'arms':arms,'parameter_configurations':sum(len(a['grid']) for a in arms),
        'modality_agreement':{'r3d_only_candidates':len(pools['r3d_only']),'motion_only_candidates':len(pools['motion_only']),
            'matched_between_algorithms':agreement['true_positive'],'tolerance_ms':s['boundary_tolerance_ms'],
            'interpretation':'algorithm agreement, not precision/recall/F1 or evidence of better boundaries'},
        'boundary_evaluation':{'status':'blocked','reason':'NO_EXHAUSTIVE_HUMAN_BOUNDARIES','precision':None,'recall':None,'f1':None},
        'human_industrial_boundaries':0,'background_control_status':background_status,
        'industrial_curves_included':False,'raw_embeddings_included':False,'formal_research_passed':False,'b2_started':False}
    inspect_json(result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--background-contamination-observed',action='store_true',help='Requires visual inspection, not inferred from curve amplitude')
    a=p.parse_args();r=json.loads(a.report.read_text());out=summarize(r,'contamination_observed_not_pure_negative_control' if a.background_contamination_observed else 'unverified_over_full_video');out['private_report_sha256']=digest(a.report)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    if a.output.exists():raise FileExistsError('Do not overwrite a published execution')
    a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'output':str(a.output),'candidate_count':out['primary']['count'],'raw_embeddings_included':False}))


if __name__=='__main__':main()
