"""Publish executed generated-data A.4 statistics only, including every failure."""
import argparse,hashlib,json,re,subprocess,zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from scripts.check_public_research_artifacts import inspect_file

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/delivery/p0.3.7-a4';DEMO=ROOT/'docs/demo/p0.3.7-a4'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def browser_safe(value):
    if isinstance(value,dict):return {k:browser_safe(v) for k,v in value.items()}
    if isinstance(value,list):return [browser_safe(v) for v in value]
    if isinstance(value,int) and abs(value)>2**53-1:return str(value)
    return value


def verify():
    archive=DEMO/'jianwei-p0.3.7-a4-demo.zip';inspect_file(archive)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None;manifest=json.loads(z.read('manifest.json'))
        assert set(z.namelist())==set(manifest['files'])|{'manifest.json'}
        for name,record in manifest['files'].items():assert hashlib.sha256(z.read(name)).hexdigest()==record['sha256']
        assert z.read('index.html')==(DEMO/'index.html').read_bytes()
        assert z.read('evidence.json')==(OUT/'verification.json').read_bytes()
    assert sha(archive)==json.loads((DEMO/'manifest.json').read_text())['archive_sha256']
    return {'passed':True,'archive_sha256':sha(archive),'original_corrupt_copy':'blocked_missing_original',
        'original_corruption_root_cause':'root_cause_not_established','fault_state_strategy':'pending_independent_review','stage_b_started':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,default=ROOT/'artifacts/p037-a4')
    p.add_argument('--backend-junit',type=Path);p.add_argument('--backend-log',type=Path)
    p.add_argument('--verify-only',action='store_true');a=p.parse_args()
    if a.verify_only:print(json.dumps(verify()));return
    e=a.evidence.resolve();backend_junit=a.backend_junit or e/'backend-junit.xml';backend_log=a.backend_log or e/'backend-full.log'
    tree=E.parse(backend_junit)
    totals={k:sum(int(s.get(k,'0')) for s in tree.findall('.//testsuite')) for k in ['tests','failures','errors','skipped']}
    assert totals['tests']>=767 and not totals['failures'] and not totals['errors']
    tests=[{'module':t.get('classname'),'name':t.get('name'),'status':'skipped' if t.find('skipped') is not None else 'passed'}
        for t in tree.findall('.//testcase') if t.get('classname','').startswith('tests.test_p037_a4_')]
    assert len(tests)>=57 and all(t['status']=='passed' for t in tests)
    performance=json.loads((e/'performance-release/results.json').read_text());assert len(performance['runs'])==performance['summary']['runs']==144
    initial=json.loads((e/'performance-main/results.json').read_text());assert len(initial['runs'])==initial['summary']['runs']==144
    intermediate=json.loads((e/'performance-final/results.json').read_text());assert intermediate['summary']['runs']==144
    assert sum(r['status']!='passed' for r in performance['runs'])==performance['summary']['failed']
    for r in performance['runs']:
        if r['spec']['quarantined']:assert r['committed']==0
        elif r['spec']['variant']=='a4_fault_state':
            assert r['integrity_policy_at_write_end']['monitor_successes']>=1
    front=re.search(r'Tests\s+(\d+) passed',(e/'frontend.log').read_text());reference=re.search(r'Ran (\d+) tests',(e/'reference.log').read_text())
    assert front and reference and '\nOK\n' in (e/'reference.log').read_text()
    for f in ['build.log','build-demo.log']:assert 'built in ' in (e/f).read_text()
    records={name:json.loads((e/path).read_text()) for name,path in [('protocol','browser-protocol-release/protocol-browser.json'),
        ('media','browser-media-release/results.json')]};assert all(r['passed'] for r in records.values())
    sources=['infra/database.py','infra/sqlite_isolation.py','infra/sqlite_admission.py','infra/sqlite_guard.py',
        'infra/sqlite_backup.py','infra/sqlite_backup_artifacts.py','infra/sqlite_health.py','infra/sqlite_connection.py','packages/contracts/database.py',
        'packages/temporal_jobs/service.py','scripts/benchmark_sqlite_a4.py','scripts/investigate_sqlite_workload.py']
    payload=browser_safe({'stage':'P0.3.7-A.4','baseline':'fb69646336eb6346ea3cec498a08856a80a34e60',
        'tested_source_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'source_sha256':{s:sha(ROOT/s) for s in sources},'backend':totals,'counterexample_tests':tests,
        'frontend_passed':int(front[1]),'reference_passed':int(reference[1]),'builds':['production','demo'],
        'records':records,'baseline_counterexample':json.loads((e/'baseline/results.json').read_text()),
        'performance':performance,'initial_performance':initial,'intermediate_performance':intermediate,
        'last_page_before':json.loads((e/'last-page-admission-review/results.json').read_text()),
        'last_page_after':json.loads((e/'last-page-admission-fixed/results.json').read_text()),
        'prior_ci_workload_failure':json.loads((e/'ci-foundation/workload/results.json').read_text()),
        'workload_budget_repetition':json.loads((e/'workload-ci-budget-fix/results.json').read_text()),
        'post_performance_failure_checks':json.loads((e/'performance-failure-checks.json').read_text()),
        'original_corrupt_copy':'blocked_missing_original','independent_original_available':False,
        'original_corruption_root_cause':'root_cause_not_established','fault_state_architecture_status':'pending_independent_review',
        'large_integer_encoding':'decimal_string_for_browser_precision','formal_test_data_consumed':False,
        'cloud_file_protocol_preview':'blocked_by_browser_administrator_policy; not_bypassed',
        'supported_demo_preview':'loopback_HTTP_verified',
        'prior_a2_media_cache_case':'not_rerun; limitation_unchanged','stage_b_started':False,'p04_admitted':False,'independent_acceptance':'pending'})
    save(OUT/'verification.json',payload);save(OUT/'performance-results.json',performance)
    save(OUT/'performance-initial-results.json',initial)
    save(OUT/'performance-intermediate-results.json',intermediate)
    save(OUT/'performance-failure-checks.json',payload['post_performance_failure_checks'])
    log_acquisition={}
    for name in ['backend-junit.xml','backend-full.log','frontend.log','reference.log','build.log','build-demo.log',
        'isolation.log','admission-first.log','admission-regression.log','admission-final.log','admission-release.log',
        'policy.log','policy-release.log','monitor-release.log','baseline.log','performance-main.log','performance-final.log',
        'backend-schema-first.log','schema-migration-release.log','failure-seal-first.log','failure-seal-final.log',
        'performance-release.log','workload-budget-regression.log','workload-ci-budget-fix.log']:
        source=backend_junit if name=='backend-junit.xml' else backend_log if name=='backend-full.log' else e/name
        original=source.read_bytes();target=OUT/'tests'/name;target.parent.mkdir(exist_ok=True)
        if name.endswith('.log'):
            plain=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',original.decode())
            target.write_text('\n'.join(line.rstrip() for line in plain.splitlines()).rstrip()+'\n')
        else:target.write_bytes(original)
        log_acquisition[name]={'original_sha256':hashlib.sha256(original).hexdigest(),'published_sha256':sha(target),
            'normalization':'ANSI_and_trailing_whitespace_only' if name.endswith('.log') else 'none'}
    save(OUT/'tests/log-acquisition.json',log_acquisition)
    for name,r in records.items():save(OUT/'records'/(name+'.json'),r)
    for name in ['desktop.png','mobile.png']:
        target=OUT/'browser'/name;target.parent.mkdir(exist_ok=True);target.write_bytes((e/'browser-media-release'/name).read_bytes())
    html=(ROOT/'demo/stage-a4-evidence.html').read_text().replace('__DATA__',json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c'))
    files={'index.html':html.encode(),'evidence.json':(OUT/'verification.json').read_bytes(),
        '使用说明.txt':('解压后使用本地HTTP预览：在解压目录运行 python -m http.server 18064 --bind 127.0.0.1，打开 http://127.0.0.1:18064/index.html。\n'
            '云环境浏览器管理员策略禁止file://，未修改或绕过；实际通过桌面/手机回环HTTP交互验收。\n'
            '展示真实生成数据工程证据及全部性能失败；不请求远程API，不含数据库/视频/特征/权重。默认full，轻量策略待审，原损坏副本未取得，根因未确认，B阶段未启动。').encode()}
    manifest={'stage':'P0.3.7-A.4','private_data_included':False,
        'files':{n:{'sha256':hashlib.sha256(v).hexdigest(),'bytes':len(v)} for n,v in files.items()}}
    files['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode();DEMO.mkdir(parents=True,exist_ok=True)
    (DEMO/'index.html').write_bytes(files['index.html']);archive=DEMO/'jianwei-p0.3.7-a4-demo.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in sorted(files.items()):
            item=zipfile.ZipInfo(name,(2026,11,5,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED;z.writestr(item,data)
    save(DEMO/'manifest.json',{'archive_sha256':sha(archive),'files':manifest['files']});print(json.dumps(verify()))


if __name__=='__main__':main()
