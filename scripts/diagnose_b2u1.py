"""Read-only diagnosis of a frozen B.2-U report; no fitting or activation.

Detailed candidate times stay private. Public output is an explicit aggregate.
"""
import argparse,hashlib,json,statistics
from pathlib import Path
from packages.knowledge.service import content_hash


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def union_ms(spans):
    merged=[]
    for a,b in sorted((s['start_ms'],s['end_ms']) for s in spans):
        if b<=a:raise ValueError('Invalid original support')
        if merged and a<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],b)
        else:merged.append([a,b])
    return sum(b-a for a,b in merged)


def distribution(values):
    valid=[v for v in values if v is not None]
    return {'count':len(valid),'null_count':len(values)-len(valid),'min':min(valid) if valid else None,
        'median':statistics.median(valid) if valid else None,'max':max(valid) if valid else None}


def forecasts(rows):
    result=[]
    for f in rows:
        if f['status']!='computed':result.append(f);continue
        methods=f['methods'];mean=methods['train_mean']['normalized_latent_mse'];proto=methods['prototype_continuation']['normalized_latent_mse'];best=min(v['normalized_latent_mse'] for v in methods.values())
        result.append({**f,'primary_predictors_with_lowest_mse':[k for k,v in methods.items() if abs(v['normalized_latent_mse']-best)<1e-12],
            'prototype_gain_vs_trainmean':1-proto/mean if mean>1e-12 else None,
            'shuffled_target_seeds_better_than_trainmean':[v['seed'] for v in f['shuffled_target_ridge_all_seeds'] if v['normalized_latent_mse']<mean]})
    return result


def diagnose(report):
    if report.get('schema')!='b2u-unlabeled-study-v1' or report.get('evidence_class')!='development_exploration':raise ValueError('Frozen real-development report required')
    if any(report.get(k) is not None for k in ['real_precision','real_recall','real_f1']):raise ValueError('No human truth: real metrics must remain null')
    if report.get('production_activated') is not False or report.get('formal_test_data_consumed') is not False:raise ValueError('Not an offline development result')
    methods=[];candidates=[];gate_counts={};gate_candidates={}
    for m in report['methods']:
        if m['status']!='computed':raise ValueError('Expected complete frozen methods, not silent omissions')
        checks=m['shadow_gate']['checks'];failures=[k for k,ok in checks.items() if not ok]
        if m['shadow_gate']['passed']!=all(checks.values()) or set(failures)!=set(m['shadow_gate']['failure_reasons']):raise ValueError('Frozen gate receipt is inconsistent')
        for name in failures:gate_counts[name]=gate_counts.get(name,0)+1;gate_candidates[name]=gate_candidates.get(name,0)+len(m['prototypes'])
        fit_span=m['fit']['end_ms']-m['fit']['start_ms'];all_fit=[]
        for p in m['prototypes']:
            spans=p['occurrences'];all_fit.extend(spans)
            if len(spans)!=p['support_count'] or union_ms(spans)!=sum(s['end_ms']-s['start_ms'] for s in spans):raise ValueError('Prototype support count or original-frame exclusion is inconsistent')
            records={role:[h for h in v['matches'] if h['prototype_id']==p['id']] for role,v in m['evaluations'].items()}
            candidates.append({'method':m['method'],'prototype_id':p['id'],'support_count':len(spans),'fit_support_time_ms':union_ms(spans),'fit_time_fraction':union_ms(spans)/fit_span,
                'duration_ms':p['duration_ms'],'fit_occurrences':spans,'calibration_matches':records['calibration'],'replay_matches':records['replay'],
                'inherited_representation_failures':failures,'per_candidate_gate_evaluation_available':False,'real_action_identity':None,'activated':False})
        role_stats={}
        for role,e in m['evaluations'].items():
            span=e['scope']['end_ms']-e['scope']['start_ms'];coverage=e['matched_window_coverage'];share=e['largest_prototype_share']
            role_stats[role]={'windows':e['scope']['windows'],'matched_occurrences':len(e['matches']),'matched_window_fraction':coverage,'matched_support_time_fraction':union_ms(e['matches'])/span,
                'largest_share_of_matched_windows':share,'largest_share_of_all_windows':share*coverage if share is not None else None,'effective_prototype_count':e['effective_prototype_count'],
                'forecasts':forecasts(e['forecasts'])}
        nulls=m['fit_null_controls_all_seeds'];control_groups={key:[r for r in m['controls_all_results'] if r['control']==key] for key in sorted({r['control'] for r in m['controls_all_results']})}
        methods.append({'method':m['method'],'background_control':m['method'].startswith('background'),
            'candidate_count':len(m['prototypes']),'support_count_distribution':distribution([p['support_count'] for p in m['prototypes']]),
            'duration_distribution_ms':distribution([p['duration_ms'] for p in m['prototypes']]),'fit_union_fraction':union_ms(all_fit)/fit_span,
            'at_registered_candidate_cap':len(m['prototypes'])==report['settings']['max_prototypes'],
            'fit_strength':m['fit_strength_proxy'],'null_strength_all_seeds':nulls,'excess_over_time_shuffle':m['fit_strength_proxy']-max(v['strength_proxy'] for v in nulls if v['control']=='time_shuffle'),
            'excess_over_block_shuffle':m['fit_strength_proxy']-max(v['strength_proxy'] for v in nulls if v['control']=='block_shuffle'),
            'old_null_margin':report['settings']['min_gate_null_margin'],'calibration_noise':distribution([v['assignment_agreement'] for v in m['calibration_noise_all_seeds']]),
            'replay_controls':{key:{'assignment_agreement':distribution([v['assignment_agreement'] for v in rows]),'rows':rows} for key,rows in control_groups.items()},
            'roi_shift_status':'measured_cached_lightweight' if any(k.startswith('roi_') for k in control_groups) else 'not_extracted_or_not_applicable',
            'roles':role_stats,'old_gate_checks':checks,'old_gate_failures':failures,'old_activated_count':m['shadow_gate']['active_prototypes'],
            'per_candidate_gate_evaluation_available':False})
    public={'schema':'b2u1-frozen-diagnosis-aggregate-v1','baseline_run_id':report['run_id'],'baseline_code_commit':report['code_identity']['git_head'],
        'original_development_sources':1,'candidate_count':len(candidates),'methods':methods,'gate_failure_method_counts':gate_counts,'gate_failure_candidate_exposures':gate_candidates,
        'failure_counts_are_nonexclusive':True,'gate_unit':'representation, not individual prototype','frozen_settings_hash':content_hash(report['settings']),
        'no_fit_or_threshold_selection':True,'old_registry_changed':False,'old_failed_candidates_promoted':False,'real_precision':None,'real_recall':None,'real_f1':None,
        'interpretation_limits':['Proxy recurrence is not confirmed action identity.','Candidate shares among matched windows differ from total-time dominance.',
            'Block shuffles retain short within-block motifs; old composite rejection is not a proof that no recurrence exists.',
            'Prediction MSE tests a different hypothesis from motif reuse; removing prediction alone still leaves old null failures.',
            'Latent MSE is only comparable within one frozen representation.','Sparse/overlapping windows are not independent sessions.']}
    return public,{'schema':'b2u1-private-candidate-diagnosis-v1','baseline_run_id':report['run_id'],'candidates':candidates,'real_f1':None,'old_registry_changed':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',type=Path,required=True);p.add_argument('--seal',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parents[1];out=a.output.resolve()
    if not out.is_relative_to(root/'artifacts'):raise ValueError('Private new artifacts output required')
    seal=json.loads(a.seal.read_text());before=sha(a.report)
    if before!=seal['report_sha256']:raise ValueError('Frozen report differs from baseline seal')
    r=json.loads(a.report.read_text());public,private=diagnose(r)
    if r['run_id']!=seal['run_id'] or private['baseline_run_id']!=seal['run_id'] or len(private['candidates'])!=seal['candidate_count']:raise ValueError('Frozen baseline identity mismatch')
    out.mkdir(parents=True,exist_ok=False);write(out/'diagnosis-aggregate.json',public);write(out/'candidates-private.json',private)
    if sha(a.report)!=before:raise RuntimeError('Frozen result modified during diagnosis')
    write(out/'receipt.json',{'baseline_report_sha256':before,'input_unchanged':True,'methods':len(public['methods']),'candidates':len(private['candidates']),'old_registry_changed':False})
    print(json.dumps({'methods':len(public['methods']),'candidates':len(private['candidates']),'input_unchanged':True}))


if __name__=='__main__':main()
