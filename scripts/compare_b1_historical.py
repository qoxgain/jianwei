"""Read-only nominal-window comparison, retaining distinct recipe identities."""
import argparse,json,hashlib
from pathlib import Path
import numpy as np


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def compare(old_directory,new_directory):
    old=json.loads((old_directory/'manifest.json').read_text());new=json.loads((new_directory/'manifest.json').read_text())
    if old['source_hash']!=new['source']['sha256'] or old['split']!='unassigned':raise ValueError('Same development source required')
    old_path=old_directory/'vectors.npy';new_path=new_directory/'features.npz'
    if sha(old_path)!=old['feature_cache']['sha256'] or sha(new_path)!=new['features_sha256']:raise ValueError('Read-only cache hash mismatch')
    before=(sha(old_path),sha(new_path));x=np.load(old_path,allow_pickle=False)
    with np.load(new_path,allow_pickle=False) as arrays:y=arrays['features'].copy()
    lookup={(r['start_ms'],r['end_ms']):r['row'] for r in old['rows'] if r['eligible']}
    primary=new['settings'];rows=[r for r in new['rows'] if r['role']=='global' and r['window_ms']==primary['primary_window_ms'] and r['fps']==primary['primary_fps']]
    pairs=[(lookup[(r['start_ms'],r['end_ms'])],r['index']) for r in rows if (r['start_ms'],r['end_ms']) in lookup and r['status']=='valid']
    if not pairs:raise ValueError('No comparable windows')
    delta=np.asarray([x[i].astype(float)-y[j].astype(float) for i,j in pairs]);assert before==(sha(old_path),sha(new_path))
    return {'old_feature_sha256':before[0],'old_rows':len(x),'new_global_primary_rows':len(rows),
        'matching_nominal_windows':len(pairs),'max_abs_difference':float(abs(delta).max()),'mean_squared_difference':float(np.mean(delta**2)),
        'old_features_preserved':True,'recipe_identity_preserved':'distinct recipes; matching values do not relabel caches',
        'interpretation':'same source nominal window control, not independent sources or boundary accuracy'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--old',type=Path,required=True);p.add_argument('--new',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    r=compare(a.old,a.new)
    with a.output.open('x') as stream:stream.write(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r))


if __name__=='__main__':main()
