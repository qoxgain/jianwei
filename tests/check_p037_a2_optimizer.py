"""Defensive Vite integrity check with an inert text marker; no media payload is used."""
import argparse,hashlib,json,os,signal,socket,subprocess,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import URLError,HTTPError
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1];WEB=ROOT/'apps/web'


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--chromium',default='/usr/bin/chromium');p.add_argument('--expect-vulnerable',action='store_true');args=p.parse_args()
 if args.expect_vulnerable:assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()=='5f150e7daf982c3cb0c9066691f25e7b34961bc8'
 out=args.output.resolve();assert out.is_relative_to(ROOT/'artifacts');out.mkdir(parents=True,exist_ok=False)
 marker=b'generated-unverified-cache-content';payload=b'export default \"'+marker+b'\";\n'
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 log=(out/'vite.log').open('w');server=subprocess.Popen(['npm','run','dev','--','--mode','demo','--port',str(port)],cwd=WEB,stdout=log,stderr=log,start_new_session=True)
 base=f'http://127.0.0.1:{port}';cache=WEB/'node_modules/.vite/deps';saved={};results=[];errors=[]
 def get(path,method='GET',headers=None):
  try:
   with urlopen(Request(base+path,method=method,headers=headers or {}),timeout=15) as r:return r.status,r.read(),dict(r.headers)
  except HTTPError as e:return e.code,e.read(),dict(e.headers)
 try:
  for _ in range(300):
   if server.poll() is not None:raise RuntimeError('Vite exited; see '+str(out/'vite.log'))
   try:
    with urlopen(base,timeout=1):break
   except URLError:time.sleep(.1)
  with sync_playwright() as pw:
   browser=pw.chromium.launch(executable_path=args.chromium,args=['--no-sandbox']);page=browser.new_page();frames=[]
   page.on('websocket',lambda socket:socket.on('framereceived',lambda value:frames.append(value)))
   def connected():return sum(isinstance(v,str) and '"type":"connected"' in v for v in frames)
   def wait_connected(previous):
    for _ in range(300):
     if connected()>previous:return
     page.wait_for_timeout(20)
    raise AssertionError('Real Vite HMR websocket did not connect')
   def approve(path,raw):
    temp=path.with_suffix('.review.tmp');temp.write_bytes(raw);os.replace(temp,path)
   page.on('pageerror',lambda e:errors.append(str(e)));page.goto(base);expect(page.get_by_role('button',name='知识工作台',exact=True)).to_be_visible(timeout=30000)
   metadata=cache/'_metadata.json';snapshot=metadata.read_bytes();info=json.loads(snapshot);member=cache/info['optimized']['react']['file'];original=member.read_bytes();saved[member]=original
   before={p:hashlib.sha256((WEB/p).read_bytes()).hexdigest() for p in ['package-lock.json','browser-files.json']}
   for method,query,headers in [('GET','',{}),('GET','',{'Range':'bytes=0-9999999'}),('GET','?raw',{}),('GET','?url',{}),('GET','?import',{}),('HEAD','',{})]:
    # Replace even when ordinary GET has previously populated Vite's transform cache.
    member.write_bytes(payload);assert member.stat().st_nlink==1 and not member.is_symlink()
    status,body,_=get('/node_modules/.vite/deps/'+member.name+query,method,headers)
    leaked=marker in body
    results.append({'method':method,'query':query,'range':bool(headers),'status':status,'unverified_content_returned':leaked})
   assert metadata.read_bytes()==snapshot
   assert all(hashlib.sha256((WEB/p).read_bytes()).hexdigest()==v for p,v in before.items())
   if args.expect_vulnerable:assert any(r['unverified_content_returned'] for r in results)
   else:
    assert not any(r['unverified_content_returned'] for r in results)
    assert all(r['status'] in (403,404,416) for r in results)
   member.write_bytes(original)
   page.reload();expect(page.get_by_role('button',name='知识工作台',exact=True)).to_be_visible(timeout=30000)
   assert not errors
   # Initial legitimate optimized modules and debugging maps must remain usable.
   status,body,_=get('/node_modules/.vite/deps/'+member.name);assert status==200
   restored_response_contains_marker=marker in body
   if not args.expect_vulnerable:assert not restored_response_contains_marker
   if not args.expect_vulnerable:
    map_path=next(cache.glob('*.js.map'))
    status,body,_=get('/node_modules/.vite/deps/'+map_path.name)
    assert status==200 and json.loads(body)['version']==3
    status,_,headers=get('/node_modules/.vite/deps/'+map_path.name.removesuffix('.map'))
    assert status==200 and headers.get('SourceMap')==map_path.name
    for query in ['?raw','?url','?v=abc&raw','?url&import','?unknown=1']:
     status,body,_=get('/node_modules/.vite/deps/'+member.name+query)
     assert status in (403,404) and marker not in body
    # A mutable metadata entry alone is not a publication authority.
    extra=cache/'unreviewed-metadata-entry.js'
    try:
     extra.write_bytes(payload);changed=json.loads(snapshot);changed['chunks']['unreviewed-entry']={'file':extra.name}
     metadata.write_text(json.dumps(changed));status,body,_=get('/node_modules/.vite/deps/'+extra.name)
     assert status==403 and marker not in body
    finally:extra.unlink(missing_ok=True);metadata.write_bytes(snapshot)
    # Reviewed developer changes still travel through Vite's real HMR channel.
    css=WEB/'src/styles.css';manifest=WEB/'browser-files.json';css_before=css.read_bytes();manifest_before=manifest.read_bytes()
    saved[css]=css_before;saved[manifest]=manifest_before
    wait_connected(0);connections=connected()
    css.write_bytes(css_before+b'\n:root { --a2-reviewed-hmr: verified; }\n')
    approval=json.loads(manifest_before);approval['files']['apps/web/src/styles.css']=hashlib.sha256(css.read_bytes()).hexdigest()
    approve(manifest,(json.dumps(approval,ensure_ascii=False,indent=2)+'\n').encode())
    page.wait_for_function('getComputedStyle(document.documentElement).getPropertyValue("--a2-reviewed-hmr").trim()==="verified"',timeout=30000)
    wait_connected(connections)
    css.write_bytes(css_before);approve(manifest,manifest_before)
    page.wait_for_function('getComputedStyle(document.documentElement).getPropertyValue("--a2-reviewed-hmr").trim()===""',timeout=30000)
    for mode in ['production','demo']:
     with (out/('build-'+mode+'.log')).open('w') as buildlog:
      subprocess.run(['node','node_modules/vite/bin/vite.js','build','--mode',mode,'--outDir',str(out/mode)],cwd=WEB,stdout=buildlog,stderr=subprocess.STDOUT,check=True)
     assert not any(marker in p.read_bytes() for p in (out/mode).rglob('*') if p.is_file())
    assert not any(marker in p.read_bytes() for p in cache.rglob('*') if p.is_file())
   result={'passed':True,'expected_vulnerable_baseline':args.expect_vulnerable,'scope':'inert text marker only; original media/Base64 case not rerun',
    'browser':browser.version,'member_regular_independent':True,'metadata_lock_manifest_unchanged':True,'requests':results,
    'normal_react_before_after':True,'restored_response_contains_marker':restored_response_contains_marker,
    'reviewed_hmr_and_debug_map':not args.expect_vulnerable,
    'metadata_alone_cannot_authorize':not args.expect_vulnerable,'page_errors':errors,'stage_b_started':False}
   (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result));browser.close()
 finally:
  for path,raw in saved.items():path.write_bytes(raw)
  os.killpg(server.pid,signal.SIGTERM);server.wait(10);log.close()


if __name__=='__main__':main()
