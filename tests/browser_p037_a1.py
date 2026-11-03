"""Actual two-token, five-state HTTP browser review; no industrial/sealed field data."""
import argparse,hashlib,json,socket,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]


def main():
 p=argparse.ArgumentParser();p.add_argument('--chromium',default='/usr/bin/chromium');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 out=args.output.resolve();assert out.is_relative_to(ROOT/'artifacts');out.mkdir(parents=True,exist_ok=False)
 work=Path(tempfile.mkdtemp(prefix='a1-',dir=out));log=(work/'api.log').open('w')
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 base=f'http://127.0.0.1:{port}';server=subprocess.Popen([sys.executable,'-m','scripts.serve_p037_a1_demo','--root',str(work/'runtime'),'--port',str(port)],cwd=ROOT,stdout=log,stderr=log)
 errors=[];displays=[]
 try:
  for _ in range(600):
   if server.poll() is not None:raise RuntimeError('Fixture service exited: '+str(work/'api.log'))
   try:
    with urlopen(base+'/api/health',timeout=1) as r:
     if r.status==200:break
   except (URLError,TimeoutError):time.sleep(.1)
  else:raise RuntimeError('Fixture service not ready')
  with sync_playwright() as pw:
   browser=pw.chromium.launch(executable_path=args.chromium,args=['--no-sandbox']);page=browser.new_page(accept_downloads=True)
   page.on('pageerror',lambda e:errors.append(str(e)))
   for mode,size in [('desktop',{'width':1440,'height':1000}),('mobile',{'width':390,'height':844})]:
    page.set_viewport_size(size);page.goto(base+'/stage-a1-check')
    page.get_by_role('button',name='运行五状态复验',exact=True).click();expect(page.locator('#status')).to_have_text('五状态实际检查通过',timeout=30000)
    expect(page.locator('#rows tr')).to_have_count(5)
    page.wait_for_function('document.querySelector("video").readyState>=2');page.locator('video').evaluate('(v)=>v.play()')
    page.wait_for_function('document.querySelector("video").currentTime>.2');page.locator('video').evaluate('(v)=>{v.pause();v.currentTime=1.5}')
    page.wait_for_function('(()=>{const v=document.querySelector("video");return !v.seeking&&Math.abs(v.currentTime-1.5)<.1})()')
    with page.expect_download() as pending:page.get_by_role('button',name='导出本次实际结果',exact=True).click()
    pending.value.save_as(out/(mode+'.json'));actual=json.loads((out/(mode+'.json')).read_text())
    assert actual['io_pool_tokens']==2 and len(actual['cases'])==5 and all(r['passed'] for r in actual['cases'])
    page.locator('video').evaluate('(v)=>v.remove()');assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.screenshot(path=str(out/(mode+'.png')),full_page=True,animations='disabled');displays.append(actual)
   assert not errors and not list((work/'runtime').rglob('viewing-previews/**/*.mp4'))
   result={'passed':True,'browser':browser.version,'desktop_mobile':True,'scope':'generated fixture HTTP only',
    'io_pool_tokens':2,'displays':displays,'playback_and_seek':True,'export_exact':True,'page_errors':errors,
    'forbidden_preview_cache_members':0,'screenshots_contain_media':False,'independent_acceptance':'pending','stage_b_started':False,
    'template_sha256':hashlib.sha256((ROOT/'demo/stage-a1-live.html').read_bytes()).hexdigest()}
   (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False));browser.close()
 finally:
  server.terminate()
  try:server.wait(10)
  except subprocess.TimeoutExpired:server.kill();server.wait()
  log.close()


if __name__=='__main__':main()
