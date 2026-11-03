"""Package executed A.1 engineering evidence; never include media or private numerical data."""
import argparse,hashlib,json,re,subprocess,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from scripts.check_public_research_artifacts import inspect_file

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/delivery/p0.3.7-a1'
DEMO=ROOT/'docs/demo/p0.3.7-a1'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def verify():
    path=DEMO/'jianwei-p0.3.7-a1-demo.zip';inspect_file(path)
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        m=json.loads(z.read('manifest.json'))
        assert set(z.namelist())==set(m['files'])|{'manifest.json'}
        for name,item in m['files'].items():
            b=z.read(name);assert len(b)==item['bytes'] and hashlib.sha256(b).hexdigest()==item['sha256']
        assert z.read('index.html')==(DEMO/'index.html').read_bytes()
        assert z.read('evidence.json')==(OUT/'verification.json').read_bytes()
    assert sha(path)==json.loads((DEMO/'manifest.json').read_text())['archive_sha256']
    return {'passed':True,'archive_sha256':sha(path),'private_media_features_weights_databases':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence',type=Path,default=ROOT/'artifacts/p037-a1')
    p.add_argument('--verify-only',action='store_true');args=p.parse_args()
    if args.verify_only:print(json.dumps(verify()));return
    e=args.evidence.resolve();tree=ET.parse(e/'backend-junit.xml')
    suites=tree.findall('.//testsuite')
    totals={k:sum(int(s.get(k,'0')) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert not totals['failures'] and not totals['errors']
    cases=[{'module':t.get('classname'),'name':t.get('name'),
            'status':'skipped' if t.find('skipped') is not None else 'passed'}
           for t in tree.findall('.//testcase') if t.get('classname','').startswith('tests.test_p037_a1_')]
    assert len(cases)==38 and all(t['status']=='passed' for t in cases)
    browser=json.loads((e/'browser-live/results.json').read_text())
    static=json.loads((e/'static-release-final/results.json').read_text())
    assert browser['passed'] and not browser['stage_b_started'] and static['passed']
    assert browser['template_sha256']==sha(ROOT/'demo/stage-a1-live.html')
    regressions=[]
    for name,file in [('four_scenes','research/results.json'),('sampling','sampling/results.json'),
                      ('protocol','protocol/protocol-browser.json'),('p036','p036/browser-results.json'),
                      ('stage_a','stage-a/results.json'),('original_development_video','video/browser.json')]:
        path=e/'regression'/file;data=json.loads(path.read_text());assert data['passed'],name
        regressions.append({'name':name,'passed':True,'evidence_sha256':sha(path)})
    front=re.search(r'Tests\s+(\d+) passed',(e/'frontend-release.log').read_text())
    reference=re.search(r'Ran (\d+) tests',(e/'reference-final.log').read_text())
    assert front and reference and '\nOK\n' in (e/'reference-final.log').read_text()
    for file in ['build-release.log','demo-build-release.log']:assert 'built in ' in (e/file).read_text()
    source=['infra/sqlite_guard.py','infra/sqlite_connection.py','infra/file_gate.py','infra/database.py',
        'packages/media/access.py','packages/ingestion/recipe.py','packages/contracts/media.py',
        'packages/ingest_jobs/service.py','packages/ingest_jobs/inspection.py','apps/api/static.py',
        'apps/api/main.py','apps/api/sop.py','apps/web/vite.config.ts','apps/web/reviewedAssets.ts',
        'apps/web/browser-files.json','demo/stage-a1-live.html','scripts/serve_p037_a1_demo.py',
        'tests/browser_p037_a1.py','tests/check_p037_a1_static.py']
    payload={'stage':'P0.3.7-A.1','baseline':'b029f783689de2bf94f0a8e1c5dd743516ddf5af',
        'tested_code_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'scope':'generated engineering fixtures; historical development playback regression only; no formal sealed data',
        'independent_acceptance':'pending','stage_b_started':False,'p04_admitted':False,
        'formal_research_status':'blocked','backend':totals,'frontend_passed':int(front[1]),
        'reference_passed':int(reference[1]),'builds':['production','demo'],
        'baseline_counterexamples':json.loads((e/'baseline/backend-counterexamples.json').read_text()),
        'baseline_static':json.loads((e/'baseline-vite/counterexamples.json').read_text()),
        'counterexample_tests':cases,'live_browser':browser,'static_publication':static,'regressions':regressions,
        'source_sha256':{file:sha(ROOT/file) for file in source},
        'conditions':{k:'implemented_and_locally_tested; independent_review_pending' for k in 'ABCDE'},
        'sqlite_historical_corruption_root_cause':'not_confirmed',
        'limits':['single-host local reliable file locks, not cross-node coordination',
            'supported APIs only; administrator changes, private Python reflection and unguarded external SQLite writers excluded',
            'reviewed Git manifest, locked dependencies and trusted build installation are publication authority',
            'arbitrary external restore to another database identity requires offline consumption-ledger reconciliation',
            'power-loss durability and customer-network slow clients not field verified']}
    save(OUT/'verification.json',payload)
    for name,path in [('baseline-backend.json',e/'baseline/backend-counterexamples.json'),
                      ('baseline-static.json',e/'baseline-vite/counterexamples.json'),
                      ('backend-junit.xml',e/'backend-junit.xml')]:
        (OUT/name).write_bytes(path.read_bytes())
    for name in ['backend-final.log','frontend-release.log','reference-final.log','build-release.log',
                 'demo-build-release.log','R012-final.log','R021-tests.log','R022-tests.log','R03-tests.log']:
        dest=OUT/'tests'/name;dest.parent.mkdir(exist_ok=True);dest.write_bytes((e/name).read_bytes())
    for name in ['results.json','desktop.png','mobile.png']:
        dest=OUT/'browser'/name;dest.parent.mkdir(exist_ok=True);dest.write_bytes((e/'browser-live'/name).read_bytes())
    # Only scalar/status evidence and media-free screenshots are published.
    html=(ROOT/'demo/stage-a1-evidence.html').read_text().replace('__DATA__',json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c'))
    files={'index.html':html.encode(),'evidence.json':(OUT/'verification.json').read_bytes(),
           'backend-junit.xml':(OUT/'backend-junit.xml').read_bytes(),
           '使用说明.txt':'解压打开index.html查看实际执行证据；离线页面不请求API。实际五状态复验请按页面命令启动隔离服务。A.1待独立复验，不得进入B。'.encode()}
    m={'stage':'P0.3.7-A.1','private_media_features_weights_databases':False,
       'files':{name:{'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for name,b in files.items()}}
    files['manifest.json']=(json.dumps(m,ensure_ascii=False,indent=2)+'\n').encode();DEMO.mkdir(parents=True,exist_ok=True)
    (DEMO/'index.html').write_bytes(files['index.html'])
    target=DEMO/'jianwei-p0.3.7-a1-demo.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,b in sorted(files.items()):
            item=zipfile.ZipInfo(name,(2026,11,3,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED;z.writestr(item,b)
    save(DEMO/'manifest.json',{'archive_sha256':sha(target),'files':m['files']})
    print(json.dumps(verify()))


if __name__=='__main__':main()
