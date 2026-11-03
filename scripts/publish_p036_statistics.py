"""Explicitly publish scalar statistics only; never copy numerical feature/weight packages."""
import argparse,csv,json
from pathlib import Path
import numpy as np
from packages.config.loader import PROJECT_ROOT
from packages.exploratory.experiment import save


def distribution(values):
    a=np.asarray(values,dtype=float)
    return {'median':float(np.median(a)),'min':float(a.min()),'max':float(a.max()),'std':float(a.std()),'seeds':len(a)}


def display_traces(points, limit=20):
    """Uniformly retain actual endpoints, round trace-only scalars; never metrics."""
    indices=np.unique(np.linspace(0,len(points)-1,min(limit,len(points)),dtype=int)) if points else []
    def compact(value):
        if isinstance(value,float):return round(value,8)
        if isinstance(value,list):return [compact(v) for v in value]
        if isinstance(value,dict):return {k:compact(v) for k,v in value.items()}
        return value
    return [compact(points[i]) for i in indices]


def export(inputs,output,ui_path):
    catalog=json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())['assets']
    expected={a['asset_id']:a for a in catalog if a['kind']=='video'}
    records={};snapshots=[]
    for directory in inputs:
        data=json.loads((directory/'results.json').read_text());snapshots.append(json.loads((directory/'run-config.json').read_text()))
        for record in data['assets']:
            key=record['asset_id']
            if key in records:raise ValueError('Duplicate source run; preserve retries separately, do not silently cherry-pick')
            if key not in expected:raise ValueError('Unallowlisted source')
            records[key]=record
    if set(records)!=set(expected):raise ValueError('Wait for all nine development sources, including blocked/failed records')
    common=set.intersection(*(set(s['code_files']) for s in snapshots))
    if any(set(s['code_files'])!=common for s in snapshots):
        raise ValueError('Numerical code snapshots have different dependency coverage')
    if any(len({s['code_files'][p] for s in snapshots})!=1 for p in common):
        raise ValueError('Numerical code changed between batches; do not merge results as a single version')
    if len({s['system_config_hash'] for s in snapshots})!=1:
        raise ValueError('Different runtime configurations: report separately rather than merging')
    published=[];summary=[];csv_rows=[]
    for key,record in sorted(records.items()):
        # Allowlist fields: no runtime URI, vectors, raw predictions or parameters.
        fields=('asset_id','source_sha256','rows','status','feature_space_id','cache_sha256','manifest_sha256','encoder_sha256','evidence_class','formal_research_status','independent_sessions_verified','confidence_intervals','p04_admitted','seeds','horizons_seconds','reason','exception_type','error_code')
        item={k:record[k] for k in fields if k in record};item['cohorts']={}
        for history,cohort in record.get('cohorts',{}).items():
            meta={k:v for k,v in cohort['metadata'].items() if k in ('status','counts','intervals_ms','cohort_hash','history_windows','row_overlap')}
            if meta['status']=='ready' and sorted(r['seed'] for r in cohort['runs'])!=sorted(record['seeds']):
                raise ValueError('Incomplete seed matrix; keep missing runs blocked/failed, never claim complete')
            item['cohorts'][history]={'metadata':meta,'runs':[]}
            for run in cohort['runs']:
                permitted=('seed','history','training','models','weights_sha256','weight_reload_exact','motion_threshold_train_only','projection_explained_variance','encoder_updated','selected_by_validation','splits')
                public_run={k:run[k] for k in permitted if k in run}
                public_run['splits']={name:{**split,'scalar_traces':display_traces(split['scalar_traces'])}
                                      for name,split in run['splits'].items()}
                item['cohorts'][history]['runs'].append(public_run)
                for split,result in run['splits'].items():
                    for model,m in result['metrics'].items():
                        for h,seconds in enumerate(record['horizons_seconds']):
                            csv_rows.append({'video':key,'history':history,'seed':run['seed'],'split':split,
                                'model':model,'horizon_seconds':seconds,'feature_mse':m['feature_mse'][h],
                                'normalized_feature_mse':m['normalized_feature_mse'][h],'cosine':m['feature_cosine'][h]})
            if cohort['runs']:
                names=list(cohort['runs'][0]['splits']['holdout']['metrics'])
                models={n:distribution([np.mean(r['splits']['holdout']['metrics'][n]['feature_mse']) for r in cohort['runs']]) for n in names}
                wins={}
                controls=('persistence','train_mean','ridge','initial_tcn','shuffled_tcn')
                for name in ('causal_tcn','delta_tcn','multiscale_delta_tcn'):
                    wins[name]=sum(all(np.mean(r['splits']['holdout']['metrics'][name]['feature_mse'])<np.mean(r['splits']['holdout']['metrics'][base]['feature_mse']) for base in controls) for r in cohort['runs'])
                summary.append({'video':key,'history':history,'counts':meta['counts'],
                    'holdout_seed_distributions':models,'descriptive_seeds_better_than_all_five_controls':wins,
                    'not_an_independent_generalization_pass_rate':True,
                    'validation_selected_models':{str(r['seed']):r['selected_by_validation'] for r in cohort['runs']}})
            else:summary.append({'video':key,'history':history,'status':'blocked','counts':meta['counts']})
        published.append(item)
    result={'stage':'P0.3.6','status':'completed' if all(r.get('status')!='failed' for r in records.values()) else 'completed_with_failures',
        'formal_status':'blocked','independent_sessions_verified':0,'p04_admitted':False,
        'evidence_class':'retrospective_development','display_only_actual_scalar_statistics':True,
        'trace_display':{'max_actual_points_per_split':20,'decimal_places':8,'smoothing':False,
                         'full_precision_metrics_and_losses':True,'private_reports_unchanged':True},
        'numeric_code_heads':sorted({s['git_head'] for s in snapshots}),'assets':published}
    output.mkdir(parents=True,exist_ok=True)
    save(output/'summary.json',{'stage':'P0.3.6','formal_status':'blocked','assets':summary,
        'random_seeds_are_not_independent_captures':True,'confidence_intervals':None})
    save(output/'execution-provenance.json',{'code_heads':result['numeric_code_heads'],
        'dirty_at_run_start':[s['git_dirty'] for s in snapshots],
        'common_numeric_files_identical':True,'code_files':{p:snapshots[0]['code_files'][p] for p in sorted(common)},
        'system_config_hashes':sorted({s['system_config_hash'] for s in snapshots}),
        'formal_database_accessed':False,'feature_or_weight_values_published':False})
    with (output/'per-horizon.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(csv_rows[0]) if csv_rows else ['status'])
        writer.writeheader();writer.writerows(csv_rows)
    ui_path.parent.mkdir(parents=True,exist_ok=True)
    ui_path.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,default=PROJECT_ROOT/'docs/delivery/p0.3.6/research')
    p.add_argument('--ui',type=Path,default=PROJECT_ROOT/'apps/web/src/data/robustness.json')
    args=p.parse_args();result=export(args.runs,args.output,args.ui)
    print(json.dumps({'status':result['status'],'sources':len(result['assets']),'formal_status':result['formal_status']}))


if __name__=='__main__':main()
