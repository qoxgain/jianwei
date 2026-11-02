"""Collect completed per-source caches from isolated CPU batches, never raw media."""
import json
from pathlib import Path
import shutil
from packages.config.loader import PROJECT_ROOT
from packages.ingestion.pipeline import file_hash
from packages.exploratory.experiment import save


def collect():
    root=PROJECT_ROOT/'artifacts/exploratory'
    known={a['asset_id']:a for a in json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())['assets'] if a['kind']=='video'}
    records=[]
    for folder in (root/'extraction',root/'batch2',root/'batch3'):
        if not (folder/'acceptance.json').is_file():continue
        for item in json.loads((folder/'acceptance.json').read_text())['assets']:
            identifier=item['asset_id'];asset=known[identifier]
            if item['source_sha256']!=asset['sha256']:raise ValueError('Source differs')
            src=folder/identifier;dest=root/'collected'/identifier
            if not (src/'manifest.json').is_file():continue
            if src!=dest:
                dest.mkdir(parents=True,exist_ok=True)
                for name in ('manifest.json','vectors.npy','progress.json','exploration.json','job.json'):
                    a,b=src/name,dest/name
                    if b.exists() and file_hash(a)!=file_hash(b):raise ValueError('Refusing to mix/overwrite feature versions')
                    if not b.exists():shutil.copyfile(a,b)
            records.append({**item,'batch':folder.name})
    unique={r['asset_id']:r for r in records}
    for path in (root/'direct').glob('*/execution.json'):
        item=json.loads(path.read_text());identifier=item['asset_id'];src=path.parent;dest=root/'collected'/identifier
        if item['source_sha256']!=known[identifier]['sha256']:raise ValueError('Direct source differs')
        dest.mkdir(parents=True,exist_ok=True)
        for name in ('manifest.json','vectors.npy','progress.json','execution.json'):
            a,b=src/name,dest/name
            if b.exists() and file_hash(a)!=file_hash(b):raise ValueError('Refusing to overwrite a direct cache version')
            if not b.exists():shutil.copyfile(a,b)
        unique[identifier]={**item,'batch':'direct'}
    save(root/'extraction-inventory.json',{'assets':list(unique.values()),'pending':sorted(set(known)-set(unique))})
    return sorted(set(known)-set(unique))


if __name__=='__main__':print(collect())
