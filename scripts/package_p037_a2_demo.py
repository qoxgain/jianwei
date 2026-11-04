"""Publish sanitized A.2 engineering evidence, with exact-media verification explicitly pending."""
import argparse,hashlib,json,re,subprocess,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from scripts.check_public_research_artifacts import inspect_file
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/delivery/p0.3.7-a2';DEMO=ROOT/'docs/demo/p0.3.7-a2'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def verify():
    path=DEMO/'jianwei-p0.3.7-a2-demo.zip';inspect_file(path)
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None;m=json.loads(z.read('manifest.json'));assert set(z.namelist())==set(m['files'])|{'manifest.json'}
        for name,item in m['files'].items():assert hashlib.sha256(z.read(name)).hexdigest()==item['sha256']
        assert z.read('index.html')==(DEMO/'index.html').read_bytes()
        assert z.read('evidence.json')==(OUT/'verification.json').read_bytes()
    assert sha(path)==json.loads((DEMO/'manifest.json').read_text())['archive_sha256']
    return {'passed':True,'archive_sha256':sha(path),'exact_media_cache_case':'not_rerun','independent_acceptance':'pending'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,default=ROOT/'artifacts/p037-a2');p.add_argument('--verify-only',action='store_true');a=p.parse_args()
    if a.verify_only:print(json.dumps(verify()));return
    e=a.evidence.resolve();tree=ET.parse(e/'backend-junit.xml');suites=tree.findall('.//testsuite')
    totals={k:sum(int(s.get(k,'0')) for s in suites) for k in ['tests','failures','errors','skipped']};assert not totals['failures'] and not totals['errors']
    cases=[{'module':t.get('classname'),'name':t.get('name'),'status':'skipped' if t.find('skipped') is not None else 'passed'}
        for t in tree.findall('.//testcase') if t.get('classname','').startswith('tests.test_p037_a2_')]
    assert len(cases)==44 and all(t['status']=='passed' for t in cases)
    records={name:json.loads((e/file).read_text()) for name,file in [('optimizer','optimizer-release/results.json'),
        ('static','static-regression/results.json'),('four_scenes','browser/research/results.json'),
        ('sampling','browser/sampling/results.json'),('protocol','browser/protocol/protocol-browser.json'),('live','browser/live/results.json')]}
    assert all(x['passed'] for x in records.values())
    front=re.search(r'Tests\s+(\d+) passed',(e/'frontend-release.log').read_text());reference=re.search(r'Ran (\d+) tests',(e/'reference-final.log').read_text());assert front and reference
    assert '\nOK\n' in (e/'reference-final.log').read_text()
    for file in ['build-release.log','demo-build-release.log']:assert 'built in ' in (e/file).read_text()
    sources=['infra/database.py','infra/sqlite_backup.py','infra/sqlite_connection.py','infra/sqlite_guard.py','infra/file_gate.py',
        'packages/contracts/database.py','packages/config/loader.py','packages/ingestion/recipe.py',
        'apps/web/reviewedAssets.ts','apps/web/optimizerIntegrity.ts','apps/web/vite.config.ts','apps/web/browser-files.json',
        'scripts/refresh_web_manifest.py','tests/test_p037_a2_backup.py','tests/test_p037_a2_leases.py',
        'tests/test_p037_a2_tool_versions.py','tests/check_p037_a2_optimizer.py']
    payload={'stage':'P0.3.7-A.2','baseline':'5f150e7daf982c3cb0c9066691f25e7b34961bc8',
        'tested_source_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'backend':totals,'frontend_passed':int(front[1]),'reference_passed':int(reference[1]),'builds':['production','demo'],
        'counterexample_tests':cases,'records':records,'source_sha256':{f:sha(ROOT/f) for f in sources},
        'baseline_backup':{mode:json.loads((e/'baseline-backup'/mode/'result.json').read_text()) for mode in ['DELETE','WAL']},
        'baseline_boundaries':json.loads((e/'baseline-boundaries/results.json').read_text()),
        'baseline_optimizer_text':json.loads((e/'baseline-optimizer-text.json').read_text()),
        'exact_media_cache_case':'not_rerun_after_user_content_notice','content_restriction_origin':'not_confirmed_by_tool_rejection',
        'independent_acceptance':'pending','stage_b_started':False,'p04_admitted':False,
        'sqlite_historical_corruption_root_cause':'not_confirmed',
        'conditions':{'A':'partial_verification; exact_media_cache_case_not_rerun','B':'supported_paths_tested',
            'C':'task_owned_revocable_leases_and_two_worker_regression_passed','D':'tool_records_and_v5_identity_tested',
            'E':'executed_regressions_passed; field_and_independent_review_pending'},
        'limits':['single-host Linux with responsive SQLite and reliable local filesystem',
            'trusted source/lock/install/bundler/process memory; mutable optimizer cache is untrusted',
            'backup progress callbacks must be trusted, short and avoid database transactions',
            'external restores/writers, admin changes, power loss and cross-node coordination not verified']}
    save(OUT/'verification.json',payload)
    for name,x in records.items():save(OUT/'records'/(name+'.json'),x)
    for file in ['backend-junit.xml','backend-final.log','frontend-release.log','reference-final.log','build-release.log',
        'demo-build-release.log','R02-backup.log','R03-tools.log','R01-leases-final.log','frontend-final.log']:
        path=OUT/'tests'/file;path.parent.mkdir(exist_ok=True);path.write_bytes((e/file).read_bytes())
    for name in ['desktop.png','mobile.png']:
        path=OUT/'browser'/name;path.parent.mkdir(exist_ok=True);path.write_bytes((e/'browser/live'/name).read_bytes())
    html=(ROOT/'demo/stage-a2-evidence.html').read_text().replace('__DATA__',json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c'))
    files={'index.html':html.encode(),'evidence.json':(OUT/'verification.json').read_bytes(),
        'backend-junit.xml':(e/'backend-junit.xml').read_bytes(),'使用说明.txt':'打开index.html查看真实工程记录；不连接服务器。原媒体/Base64缓存专项未重跑，独立验收待完成，B阶段未启动。'.encode()}
    m={'stage':'P0.3.7-A.2','private_data_included':False,'files':{n:{'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)} for n,b in files.items()}}
    files['manifest.json']=(json.dumps(m,ensure_ascii=False,indent=2)+'\n').encode();DEMO.mkdir(parents=True,exist_ok=True);(DEMO/'index.html').write_bytes(files['index.html'])
    target=DEMO/'jianwei-p0.3.7-a2-demo.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for name,b in sorted(files.items()):
            item=zipfile.ZipInfo(name,(2026,11,4,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED;z.writestr(item,b)
    save(DEMO/'manifest.json',{'archive_sha256':sha(target),'files':m['files']});print(json.dumps(verify()))


if __name__=='__main__':main()
