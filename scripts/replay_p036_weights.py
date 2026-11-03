"""Reconstruct private full-vector predictions from frozen local weights/cache.

Only the nine unassigned development sources are accepted; no formal DB access.
The output is a NEW private NPZ, never a public Demo artifact.
"""
import argparse,json
from pathlib import Path
import numpy as np
from packages.config.loader import PROJECT_ROOT,load_bundle
from packages.exploratory.experiment import load_source,tensors
from packages.exploratory.robustness import make_cohort,predict
from packages.ingestion.pipeline import file_hash


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True);p.add_argument('--features',type=Path,required=True)
    p.add_argument('--asset',required=True);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--split',choices=['train','validation','holdout'],default='holdout')
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    for path in (args.features,args.run,args.output):
        if not path.resolve().is_relative_to(PROJECT_ROOT/'artifacts'):p.error('Only private artifacts paths allowed')
    bundle=load_bundle(args.config);opts=bundle.config.exploratory
    if opts is None or not opts.enabled:p.error('Explicit retrospective config required')
    catalog={a['asset_id']:a for a in json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())['assets'] if a['kind']=='video'}
    if args.asset not in catalog:p.error('Not an allowlisted development source')
    asset=catalog[args.asset];report=json.loads((args.run/'report.json').read_text())
    selection=json.loads((args.run/'selection-before-holdout.json').read_text())
    if file_hash(args.run/'weights.npz')!=report['weights_sha256'] or selection['weights_sha256']!=report['weights_sha256']:
        p.error('Weight identity differs from the pre-holdout selection record')
    manifest,sequences=load_source(args.features/args.asset,asset,bundle,bundle.config.encoder.weights_sha256)
    pools,windows,cohort=make_cohort(sequences,max(d['end_ms'] for d in manifest['domains']),
        bundle.config.learning,bundle.config.temporal,report['history'],opts.minimum_windows,opts.guard_ms)
    if cohort['cohort_hash']!=selection['cohort_hash']:p.error('Original cohort/recipe changed')
    with np.load(args.run/'weights.npz',allow_pickle=False) as data:
        projection={k.removeprefix('projection__'):data[k].copy() for k in data.files if k.startswith('projection__')}
        parameters={name:{k.split('__',1)[1]:data[k].copy() for k in data.files if k.startswith(name+'__')} for name in report['models']}
    x,y=tensors(pools[args.split],windows[args.split])
    predictions=predict(x,projection,parameters,report['models'],report['seed'],y.shape[1])
    for name,value in predictions.items():
        mse=np.mean((value-y)**2,axis=(0,2))
        np.testing.assert_allclose(mse,report['splits'][args.split]['metrics'][name]['feature_mse'],rtol=1e-10,atol=1e-12)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('xb') as output:np.savez_compressed(output,target=y,**predictions)
    print(json.dumps({'scope':'private retrospective reconstruction only','models':len(predictions),
        'windows':len(y),'dimension':y.shape[-1],'metrics_match_saved_report':True,'formal_admission':False}))


if __name__=='__main__':main()
