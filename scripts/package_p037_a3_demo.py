"""Export executed A.3 engineering results only; never package database/numerical files."""
import argparse,hashlib,json,re,subprocess,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from scripts.check_public_research_artifacts import inspect_file

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/delivery/p0.3.7-a3';DEMO=ROOT/'docs/demo/p0.3.7-a3'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def verify():
    archive=DEMO/'jianwei-p0.3.7-a3-demo.zip';inspect_file(archive)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None;manifest=json.loads(z.read('manifest.json'))
        assert set(z.namelist())==set(manifest['files'])|{'manifest.json'}
        for name,record in manifest['files'].items():assert hashlib.sha256(z.read(name)).hexdigest()==record['sha256']
        assert z.read('index.html')==(DEMO/'index.html').read_bytes()
        assert z.read('evidence.json')==(OUT/'verification.json').read_bytes()
    assert sha(archive)==json.loads((DEMO/'manifest.json').read_text())['archive_sha256']
    return {'passed':True,'archive_sha256':sha(archive),'independent_corrupt_copy':'blocked_not_available',
        'original_root_cause':'root_cause_not_established','independent_acceptance':'pending','stage_b_started':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,default=ROOT/'artifacts/p037-a3')
    p.add_argument('--verify-only',action='store_true');a=p.parse_args()
    if a.verify_only:print(json.dumps(verify()));return
    e=a.evidence.resolve();tree=ET.parse(e/'backend-junit.xml')
    totals={key:sum(int(s.get(key,'0')) for s in tree.findall('.//testsuite')) for key in ['tests','failures','errors','skipped']}
    assert totals=={'tests':710,'failures':0,'errors':0,'skipped':0}
    cases=[{'module':t.get('classname'),'name':t.get('name'),'status':'skipped' if t.find('skipped') is not None else 'passed'}
        for t in tree.findall('.//testcase') if t.get('classname','').startswith('tests.test_p037_a3_')]
    assert len(cases)==28 and all(t['status']=='passed' for t in cases)
    workload={name:json.loads((e/f'workload-{folder}-22/results.json').read_text()) for name,folder in
        [('baseline','baseline'),('a3_initial','a3'),('a3','a3-final')]}
    for report in workload.values():assert report['summary']=={'runs':22,'passed':22,'failed':0,'structural_damage_observed':0}
    records={name:json.loads((e/path).read_text()) for name,path in [('protocol','browser-protocol/protocol-browser.json'),
        ('media','browser-media/results.json'),('forensic_before','forensic-lock-before/results.json'),
        ('forensic_after','forensic-lock-after/results.json'),('baseline_integrity','baseline-structure/results.json')]}
    assert records['protocol']['passed'] and records['media']['passed']
    assert records['baseline_integrity']['persistent_marker_exists'] is False
    assert records['baseline_integrity']['independent_rows_after_commit']==1
    assert records['forensic_before']['after']['result']=='acquired'
    assert records['forensic_after']['after']['result']=='SQLITE_BUSY'
    frontend=re.search(r'Tests\s+(\d+) passed',(e/'frontend.log').read_text());reference=re.search(r'Ran (\d+) tests',(e/'reference.log').read_text())
    assert frontend and reference and '\nOK\n' in (e/'reference.log').read_text()
    for f in ['build.log','build-demo.log']:assert 'built in ' in (e/f).read_text()
    sources=['infra/database.py','infra/sqlite_health.py','infra/sqlite_guard.py','infra/sqlite_connection.py','infra/sqlite_backup.py',
        'infra/file_gate.py','scripts/inspect_sqlite_copy.py','scripts/check_sqlite_forensic_lock.py','scripts/investigate_sqlite_workload.py']
    payload={'stage':'P0.3.7-A.3','baseline':'642984426e8faa251a776cedee47d48c8529d918',
        'tested_source_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'source_sha256':{f:sha(ROOT/f) for f in sources},'backend':totals,'frontend_passed':int(frontend[1]),
        'reference_passed':int(reference[1]),'builds':['production','demo'],'counterexample_tests':cases,'workload':workload,'records':records,
        'independent_evidence_available':False,'independent_corrupt_copy':'blocked_not_available',
        'original_incident_root_cause':'root_cause_not_established','generated_fixture_is_independent_original':False,
        'all_persistent_channels_failure':'stop_service_offline_required; restart_durability_not_claimed',
        'prior_a2_media_cache_case':'not_rerun; limitation_unchanged','independent_acceptance':'pending',
        'pilot':json.loads((e/'workload-pilot/results.json').read_text()),
        'stage_b_started':False,'p04_admitted':False,'formal_test_data_consumed':False}
    save(OUT/'verification.json',payload);save(OUT/'workload-results.json',workload)
    for name,data in records.items():save(OUT/'records'/(name+'.json'),data)
    # All logs below contain generated fixtures and source code only. Never glob
    # runtime directories: no DB, partial, NPZ, production record or model is public.
    log_acquisition={}
    for name in ['backend-junit.xml','backend-full.log','frontend.log','reference.log','build.log','build-demo.log',
        'initial-regression.log','integrity-new.log','integrity-expanded.log','integrity-final-protocol.log',
        'backup-regression.log','forensic-final.log','baseline.log','baseline-structure.log',
        'factory-before.log','factory-after.log','backend-before-factory.log']:
        target=OUT/'tests'/name;target.parent.mkdir(exist_ok=True);original=(e/name).read_bytes()
        if name.endswith('.log'):
            plain=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',original.decode())
            target.write_text('\n'.join(line.rstrip() for line in plain.splitlines()).rstrip()+'\n')
        else:target.write_bytes(original)
        log_acquisition[name]={'original_sha256':hashlib.sha256(original).hexdigest(),
            'published_sha256':sha(target),'normalization':'ANSI_and_trailing_whitespace_only' if name.endswith('.log') else 'none'}
    save(OUT/'tests/log-acquisition.json',log_acquisition)
    for name in ['desktop.png','mobile.png']:
        target=OUT/'browser'/name;target.parent.mkdir(exist_ok=True);target.write_bytes((e/'browser-media'/name).read_bytes())
    html=(ROOT/'demo/stage-a3-evidence.html').read_text().replace('__DATA__',json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c'))
    files={'index.html':html.encode(),'evidence.json':(OUT/'verification.json').read_bytes(),
        '使用说明.txt':'打开index.html查看真实生成数据工程记录。原损坏副本未取得，原2/11根因未确认；等待独立验收，B阶段未启动。本包不含数据库或数值数据。'.encode()}
    manifest={'stage':'P0.3.7-A.3','private_data_included':False,
        'files':{n:{'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)} for n,b in files.items()}}
    files['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    DEMO.mkdir(parents=True,exist_ok=True);(DEMO/'index.html').write_bytes(files['index.html'])
    archive=DEMO/'jianwei-p0.3.7-a3-demo.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in sorted(files.items()):
            item=zipfile.ZipInfo(name,(2026,11,4,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED;z.writestr(item,data)
    save(DEMO/'manifest.json',{'archive_sha256':sha(archive),'files':manifest['files']});print(json.dumps(verify()))


if __name__=='__main__':main()
