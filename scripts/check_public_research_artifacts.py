"""Review changed public artifacts against the explicit P0.3.6 publication boundary."""
import argparse,json,subprocess,zipfile
from pathlib import Path

FORBIDDEN={'.npy','.npz','.onnx','.pth','.pt','.sqlite','.db','.mp4','.mov','.avi','.pdf'}
SAFE_LONG_ARRAYS={'epoch_losses','train_latent_mse','train_raw_feature_mse','validation_raw_feature_mse'}


def inspect_json(value,key=''):
    if isinstance(value,dict):
        for k,v in value.items():inspect_json(v,k)
    elif isinstance(value,list):
        if len(value)>=64 and all(type(v) in (int,float) for v in value) and key not in SAFE_LONG_ARRAYS:
            raise ValueError('Unexpected high-dimensional numeric array; keep private and review scalar export')
        for v in value:inspect_json(v,key)


def inspect_file(path):
    if path.suffix.lower() in FORBIDDEN:raise ValueError('Private numerical/source artifact forbidden: '+path.name)
    if path.suffix=='.json':inspect_json(json.loads(path.read_text()))
    if path.suffix=='.zip':
        with zipfile.ZipFile(path) as z:
            for member in z.namelist():
                if Path(member).suffix.lower() in FORBIDDEN:raise ValueError('Private archive entry: '+member)
                if member.endswith('.json'):inspect_json(json.loads(z.read(member)))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',default='0bf8fbab9b986940eb8d24a92a7435db06f03ea3');args=p.parse_args()
    paths=subprocess.check_output(['git','diff','--name-only','--diff-filter=AM',args.base],text=True).splitlines()
    paths+=subprocess.check_output(['git','ls-files','--others','--exclude-standard'],text=True).splitlines()
    for path in sorted(set(paths)):
        file=Path(path)
        if file.is_file():inspect_file(file)
    print(f'Reviewed {len(set(paths))} changed paths: no new raw numerical/source packages; manual review still required')


if __name__=='__main__':main()
