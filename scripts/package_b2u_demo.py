"""Ship actual synthetic reports + industrial aggregate; no private numeric state."""
import argparse,hashlib,json,zipfile
from pathlib import Path
from scripts.check_public_research_artifacts import inspect_file
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/demo/p0.3.7-b2u-r1';ARCHIVE=OUT/'jianwei-p0.3.7-b2u-demo.zip'
sha=lambda b:hashlib.sha256(b).hexdigest()


def verify():
    historical=ROOT/'docs/demo/p0.3.7-b2u/jianwei-p0.3.7-b2u-demo.zip'
    assert sha(historical.read_bytes())=='75f216897e11d371c75d0e24c509c600c63871979e8c332f9cb131d3245239bf'
    inspect_file(historical)
    inspect_file(ARCHIVE);outer=json.loads((OUT/'manifest.json').read_text());assert sha(ARCHIVE.read_bytes())==outer['archive_sha256']
    with zipfile.ZipFile(ARCHIVE) as z:
        assert z.testzip() is None;m=json.loads(z.read('manifest.json'));assert set(z.namelist())==set(m['files'])|{'manifest.json'}
        for n,v in m['files'].items():assert sha(z.read(n))==v['sha256']
        assert z.read('index.html')==(OUT/'index.html').read_bytes()
        for name,digest in m['reviewed_source_sha256'].items():assert sha((ROOT/name).read_bytes())==digest
        report=json.loads(z.read('synthetic-study.json'));query=json.loads(z.read('synthetic-query.json'));aggregate=json.loads(z.read('industrial-aggregate.json'))
        assert report['evidence_class']==query['evidence_class']=='synthetic_engineering'
        assert aggregate['evidence_class']=='development_exploration' and aggregate['industrial_trajectories_or_prototype_values_included'] is False
        assert len(aggregate['methods'])==11 and all(r['real_f1'] is None for r in [report,query,aggregate])
        assert query['parent_run_id']==report['run_id'] and not query['query_used_for_parent_fit']
        assert not m['industrial_media_included'] and not m['private_state_included'] and not m['p04_admitted']
    return {'passed':True,'stage':'P0.3.7-B.2-U','archive_sha256':outer['archive_sha256'],'html_sha256':sha((OUT/'index.html').read_bytes()),'industrial_traces_included':False,'production_updated':False,'p04_admitted':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--verify-only',action='store_true');a=p.parse_args()
    if a.verify_only:print(json.dumps(verify()));return
    if OUT.exists():raise FileExistsError('Preserve prior delivery; new output version required')
    reviewed=json.loads((ROOT/'apps/web/browser-files.json').read_text())['files']
    for path,value in reviewed.items():assert sha((ROOT/path).read_bytes())==value
    files={'index.html':(ROOT/'apps/web/dist-demo/index.html').read_bytes(),'static/brand-mark.svg':(ROOT/'apps/web/dist-demo/static/brand-mark.svg').read_bytes(),
        'synthetic-study.json':(ROOT/'apps/web/src/data/unlabeledDemo.json').read_bytes(),
        'synthetic-query.json':(ROOT/'apps/web/src/data/unlabeledQueryDemo.json').read_bytes(),
        'industrial-aggregate.json':(ROOT/'apps/web/src/data/unlabeledStatistics.json').read_bytes(),
        'generate_b2u_practice.py':(ROOT/'demo/generate_b2u_practice.py').read_bytes(),
        '使用与复验.md':(ROOT/'docs/delivery/p0.3.7-b2u/REPRODUCE.md').read_bytes(),
        'ENCODER-CI.md':(ROOT/'docs/delivery/p0.3.7-b2u/ENCODER-CI.md').read_bytes(),
        'RESULTS.md':(ROOT/'docs/delivery/p0.3.7-b2u/RESULTS.md').read_bytes()}
    manifest={'stage':'P0.3.7-B.2-U','industrial_media_included':False,'private_state_included':False,'p04_admitted':False,
        'reviewed_source_sha256':reviewed,'files':{n:{'sha256':sha(v),'bytes':len(v)} for n,v in files.items()}}
    files['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode();OUT.mkdir(parents=True);(OUT/'index.html').write_bytes(files['index.html'])
    with zipfile.ZipFile(ARCHIVE,'w',zipfile.ZIP_DEFLATED) as z:
        for n,v in sorted(files.items()):item=zipfile.ZipInfo(n,(2026,11,6,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED;z.writestr(item,v)
    (OUT/'manifest.json').write_text(json.dumps({'archive_sha256':sha(ARCHIVE.read_bytes()),'files':manifest['files']},indent=2)+'\n');print(json.dumps(verify()))


if __name__=='__main__':main()
