"""Explicit aggregate whitelist. Never publish industrial trajectories/prototypes."""
import argparse,json
from pathlib import Path
from packages.local_motion.extraction import write


def aggregate(report):
    if report['schema']!='b2u-unlabeled-study-v1':raise ValueError('Unsupported study')
    methods=[]
    for m in report['methods']:
        item={'method':m['method'],'status':m['status']}
        if m['status']=='computed':
            item.update(candidate_prototypes=len(m['prototypes']),active_prototypes=m['shadow_gate']['active_prototypes'],
                occurrence_support=[p['support_count'] for p in m['prototypes']],
                fit_windows=m['fit']['windows'],fit_strength=m['fit_strength_proxy'],worst_null_strength=m['worst_null_strength_proxy'],
                null_excess=m['null_excess_proxy'],null_controls=m['fit_null_controls_all_seeds'],
                gate_checks=m['shadow_gate']['checks'],gate_failures=m['shadow_gate']['failure_reasons'],
                gate_frozen_before_replay=m['shadow_gate']['frozen_before_replay'],elapsed_seconds=m['elapsed_seconds'],
                calibration_noise=m['calibration_noise_all_seeds'],
                roles={role:{'windows':e['scope']['windows'],'matches':len(e['matches']),'coverage':e['matched_window_coverage'],
                    'largest_prototype_share':e['largest_prototype_share'],'effective_prototype_count':e['effective_prototype_count'],
                    'segments':[{k:v for k,v in a.items() if k in {'method','segment_count','explained_temporal_variance','feature_responsive','offline_future_context'}} for a in e['segmentation']],
                    'forecasts':e['forecasts']} for role,e in m['evaluations'].items()},
                controls=m['controls_all_results'])
        methods.append(item)
    return {'schema':'b2u-public-aggregate-v1','evidence_class':report['evidence_class'],'run_id':report['run_id'],
        'code_commit':report['code_identity']['git_head'],'original_sources':1,'methods':methods,'unavailable_methods':report['unavailable_methods'],
        'elapsed_seconds':report['elapsed_seconds'],'real_precision':None,'real_recall':None,'real_f1':None,
        'independent_field_transfer':'blocked: capture session, view, source lineage and acquisition order are unverified',
        'human_action_labels_required':False,'production_activated':False,'p04_admitted':False,
        'limits':report['limits'],'industrial_trajectories_or_prototype_values_included':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--real',type=Path,required=True);p.add_argument('--generated',type=Path,required=True);p.add_argument('--query',type=Path,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parents[1];real=json.loads(a.real.read_text());generated=json.loads(a.generated.read_text());query=json.loads(a.query.read_text())
    if real['evidence_class']!='development_exploration' or generated['evidence_class']!='synthetic_engineering' or query['evidence_class']!='synthetic_engineering':raise ValueError('Evidence classes must be explicit')
    write(root/'apps/web/src/data/unlabeledStatistics.json',aggregate(real))
    # Only executed synthetic scalar traces/PTS, NEVER model arrays or registry entries.
    write(root/'apps/web/src/data/unlabeledDemo.json',generated)
    write(root/'apps/web/src/data/unlabeledQueryDemo.json',query)
    print('Exported aggregate industrial results and separately executed synthetic reports; no industrial traces')


if __name__=='__main__':main()
