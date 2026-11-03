"""Publish actual Stage-A test evidence and a media-free offline review Demo."""
import argparse,hashlib,json,re,subprocess,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from packages.config.loader import PROJECT_ROOT
from scripts.check_public_research_artifacts import inspect_file


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def verify_archive(target):
    inspect_file(target)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        manifest=json.loads(archive.read('manifest.json'))
        assert set(archive.namelist())==set(manifest['files'])|{'manifest.json'}
        for name,item in manifest['files'].items():
            raw=archive.read(name)
            assert len(raw)==item['bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256']
        assert archive.read('index.html')==(target.parent/'index.html').read_bytes()
    return {'passed':True,'archive_sha256':sha(target)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence',type=Path,default=PROJECT_ROOT/'artifacts/p037-a')
    p.add_argument('--verify-only',action='store_true')
    args=p.parse_args();e=args.evidence.resolve();out=PROJECT_ROOT/'docs/delivery/p0.3.7-a';demo=PROJECT_ROOT/'docs/demo/p0.3.7-a'
    if args.verify_only:
        print(json.dumps(verify_archive(demo/'jianwei-p0.3.7-a-demo.zip')));return
    xml=ET.parse(e/'backend-junit.xml');suites=xml.findall('.//testsuite')
    totals={k:sum(int(s.get(k,'0')) for s in suites) for k in ('tests','failures','errors','skipped')}
    if totals['failures'] or totals['errors']:p.error('Do not package failed suites')
    cases=[{'name':t.get('name'),'module':t.get('classname'),'seconds':float(t.get('time','0')),
        'status':'skipped' if t.find('skipped') is not None else 'passed'} for t in xml.findall('.//testcase')]
    selected=[t for t in cases if t['module'].startswith('tests.test_p037_')]
    if len(selected)!=22 or any(t['status']!='passed' for t in selected):p.error('Incomplete Stage-A counterexample matrix')
    browser=json.loads((e/'browser-release/results.json').read_text())
    if not browser['passed'] or browser['stage_b_started'] or browser['template_sha256']!=sha(PROJECT_ROOT/'demo/stage-a-live.html'):
        p.error('Live browser evidence does not match current template')
    regressions=[]
    for name,path in [('four_scenes','research/results.json'),('sampling','sampling/results.json'),('protocol','protocol/protocol-browser.json'),('p036','p036/browser-results.json')]:
        data=json.loads((e/'regression'/path).read_text())
        if not data['passed']:p.error('Regression failed: '+name)
        regressions.append({'name':name,'passed':True,'sha256':sha(e/'regression'/path),'scope':'isolated mechanism fixtures / historical development display only'})
    source_files=['infra/database.py','infra/sqlite_guard.py','infra/file_gate.py','packages/media/access.py',
        'packages/ingestion/recipe.py','packages/ingest_jobs/inspection.py','packages/ingest_jobs/service.py',
        'packages/sop/service.py','apps/api/main.py','apps/api/sop.py','apps/web/vite.config.ts',
        'scripts/serve_p037_a_demo.py','demo/stage-a-live.html','tests/browser_p037_a.py',
        'tests/test_p037_media_access.py','tests/test_p037_transaction_gate.py','tests/test_p037_tool_fingerprint.py']
    front=re.search(r'Tests\s+(\d+) passed',(e/'frontend-release.log').read_text())
    reference=re.search(r'Ran (\d+) tests',(e/'reference.log').read_text())
    if front is None or reference is None or '\nOK\n' not in (e/'reference.log').read_text():p.error('Missing successful frontend/reference evidence')
    for log in ('build-release.log','demo-build-release.log'):
        if 'built in ' not in (e/log).read_text():p.error('Missing successful build: '+log)
    payload={'stage':'P0.3.7-A','scope':'generated engineering fixtures, not real learning or formal sealed data',
        'baseline':'6a3b3c74887f44c495aea6a9c14351f8994227f9',
        'code_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'user_acceptance':'pending','stage_b_started':False,'p04_admitted':False,'formal_research_status':'blocked',
        'baseline_counterexamples':json.loads((e/'baseline-counterexamples.json').read_text()),
        'backend':totals,'frontend_passed':int(front[1]),'reference_passed':int(reference[1]),'builds':['production','demo'],
        'demo_html_sha256':sha(PROJECT_ROOT/'apps/web/dist-demo/index.html'),
        'counterexample_tests':selected,'browser':browser,'regressions':regressions,
        'source_sha256':{f:sha(PROJECT_ROOT/f) for f in source_files},
        'sqlite_historical_corruption_root_cause':'not_established',
        'limits':['single-host supported local file locks','external unguarded writers and administrator changes not covered',
            'quarantine durability depends on functioning filesystem','shared-library hot replacement is not an immutable deployment proof']}
    save(out/'verification.json',payload)
    save(out/'baseline-counterexamples.json',payload['baseline_counterexamples'])
    (out/'backend-junit.xml').write_bytes((e/'backend-junit.xml').read_bytes())
    for name in ('results.json','desktop.png','mobile.png'):
        dest=out/'browser'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((e/'browser-release'/name).read_bytes())
    for name in ('full-backend.log','frontend-release.log','reference.log','build-release.log','demo-build-release.log',
                 'baseline-counterexamples.log','R01-complete.log','R02-native-setters.log','R03-tests.log'):
        dest=out/'tests'/name;dest.parent.mkdir(exist_ok=True);dest.write_bytes((e/name).read_bytes())
    raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>见微 A阶段交付</title><style>body{max-width:980px;margin:auto;padding:24px;background:#132024;color:#e4eeed;font:16px/1.7 system-ui}h1{font-size:26px}section{border:1px solid #4b6567;border-radius:10px;padding:18px;margin:20px 0}.scroll{overflow:auto}table{border-collapse:collapse;width:100%}td,th{padding:9px;text-align:left;border-bottom:1px solid #435a5c}td{overflow-wrap:anywhere}select,button{padding:10px;background:#244744;color:#fff;border:1px solid #79b0a8;border-radius:6px}pre{white-space:pre-wrap;overflow-wrap:anywhere}.notice{color:#e9bf7d}</style><h1>见微 P0.3.7-A 工程交付</h1><p class="notice">等待用户测试确认 · 阶段B–E未开始 · 正式P0.4仍blocked</p><p>离线查看已经执行的工程测试记录；这里不检查当前服务器，不运行训练或真实现场评测。</p><section><h2>实际验证</h2><p id="summary"></p><p>浏览器：封存GET/HEAD/Range/preview拒绝，正常生成视频播放与定位通过；Vite私有素材及raw/url/import变体拒绝。所有素材均为生成测试图。</p><button id="export">下载完整执行证据</button></section><section><h2>三项缺陷与反例</h2><p>R01：基线探索422／Range206／preview200；修复后封存媒体统一拒绝。R02：基线故障后提交1行；修复后受管理事务拒绝提交并持久隔离。R03：基线同mtime内容替换返回旧SHA；修复后重新哈希并使配方失效。</p><label for="filter">专项</label><select id="filter" aria-label="专项"><option value="all">全部22项新增反例</option><option value="media">R01 媒体</option><option value="transaction">R02 事务</option><option value="tool">R03 工具</option></select><div class="scroll"><table><thead><tr><th>实际测试</th><th>结果</th></tr></thead><tbody id="rows"></tbody></table></div></section><section><h2>运行自己的隔离验收</h2><p>在项目根目录安装requirements-encoder.txt、requirements-browser.txt和FFmpeg。输出必须为新的artifacts目录；不使用客户数据库或真实封存资料。</p><pre>.venv/bin/python -m scripts.serve_p037_a_demo --root artifacts/p037-a-my-check --port 18737</pre><p>然后打开本机 http://127.0.0.1:18737/stage-a-check，点击“运行实际媒体检查”，可导出实际HTTP结果。</p><pre>.venv/bin/python -m pytest tests/test_p037_media_access.py tests/test_p037_transaction_gate.py tests/test_p037_tool_fingerprint.py -q</pre><p>SQLite原始损坏根因仍未确认；单机文件锁不等于跨节点或任意外部写者保护。历史配方不改写，新配方为v3。</p></section><script>const evidence=__DATA__;document.querySelector('#summary').textContent=`本地后端 ${evidence.backend.tests} 项，前端 ${evidence.frontend_passed} 项，参考 ${evidence.reference_passed} 项通过，生产和Demo构建通过。`;
function show(){const key=document.querySelector('#filter').value;const rows=document.querySelector('#rows');rows.textContent='';for(const t of evidence.counterexample_tests.filter(t=>key==='all'||t.module.includes(key))){const tr=document.createElement('tr');for(const text of [t.name,t.status]){const td=document.createElement('td');td.textContent=text;tr.append(td)}rows.append(tr)}}document.querySelector('#filter').onchange=show;show();
document.querySelector('#export').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(evidence,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='p037-a-verification.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};</script></html>'''.replace('__DATA__',raw)
    files={'index.html':html.encode(),'evidence.json':(out/'verification.json').read_bytes(),
        'backend-junit.xml':(out/'backend-junit.xml').read_bytes(),
        '使用说明.txt':'解压打开index.html查看实际证据；不会自动连接服务器。交互媒体检查请按页面命令启动隔离生成素材服务。A阶段等待用户验证，后续未开始。'.encode()}
    manifest={'stage':'P0.3.7-A','media_features_weights_databases_included':False,
        'files':{name:{'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for name,b in files.items()}}
    files['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode();demo.mkdir(parents=True,exist_ok=True)
    (demo/'index.html').write_bytes(files['index.html'])
    target=demo/'jianwei-p0.3.7-a-demo.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,b in sorted(files.items()):
            item=zipfile.ZipInfo(name,(2026,11,3,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED;z.writestr(item,b)
    inspect_file(target)
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None and z.read('index.html')==(demo/'index.html').read_bytes()
        for name,item in manifest['files'].items():assert hashlib.sha256(z.read(name)).hexdigest()==item['sha256']
    save(demo/'manifest.json',{'archive_sha256':sha(target),'archive_bytes':target.stat().st_size,**manifest})
    print(json.dumps({'tests':totals,'counterexamples':len(selected),'archive_bytes':target.stat().st_size,'archive_sha256':sha(target)}))


if __name__=='__main__':main()
