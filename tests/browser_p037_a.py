"""Real Chromium and HTTP admission check using generated, isolated media only."""
import argparse,hashlib,json,socket,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.request import urlopen,Request
from urllib.error import URLError,HTTPError
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--chromium',default='/usr/bin/chromium')
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/p037-a/browser');args=p.parse_args()
    out=args.output.resolve();assert out.is_relative_to(ROOT/'artifacts');out.mkdir(parents=True,exist_ok=True)
    parent=ROOT/'artifacts/p037-browser-work';parent.mkdir(exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='isolated-',dir=parent));runtime=work/'runtime'
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    base=f'http://127.0.0.1:{port}';log=(work/'api.log').open('w')
    server=subprocess.Popen([sys.executable,'-m','scripts.serve_p037_a_demo','--root',str(runtime),'--port',str(port)],cwd=ROOT,stdout=log,stderr=log)
    errors=[];console=[];displays=[];vite=None;vite_log=None
    try:
        for _ in range(300):
            if server.poll() is not None:raise RuntimeError('Isolated server exited; inspect '+str(work/'api.log'))
            try:
                with urlopen(base+'/api/health',timeout=1) as response:
                    if response.status==200:break
            except (URLError,TimeoutError):time.sleep(.1)
        else:raise RuntimeError('Isolated server not ready')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=args.chromium,args=['--no-sandbox'])
            context=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':1000},locale='zh-CN')
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('console',lambda m:console.append(m.text) if m.type=='error' else None)
            for mode,size in [('desktop',{'width':1440,'height':1000}),('mobile',{'width':390,'height':844})]:
                page.set_viewport_size(size);page.goto(base+'/stage-a-check')
                expect(page.get_by_role('heading',name='见微 P0.3.7-A 工程验收',exact=True)).to_be_visible()
                expect(page.locator('.notice')).to_contain_text('阶段B–E未开始')
                page.get_by_role('button',name='运行实际媒体检查',exact=True).click()
                expect(page.locator('#status')).to_have_text('8 项实际媒体请求检查通过',timeout=30000)
                expect(page.locator('#rows tr')).to_have_count(8)
                page.wait_for_function('document.querySelector("video").readyState>=2')
                page.locator('video').evaluate('(v)=>v.play()');page.wait_for_function('document.querySelector("video").currentTime>.2')
                page.locator('video').evaluate('(v)=>{v.pause();v.currentTime=1.5}')
                page.wait_for_function('(()=>{const v=document.querySelector("video");return !v.seeking&&Math.abs(v.currentTime-1.5)<.1})()')
                with page.expect_download() as pending:page.get_by_role('button',name='导出本次实际结果',exact=True).click()
                target=out/(mode+'-http-results.json');pending.value.save_as(target)
                actual=json.loads(target.read_text());assert len(actual['cases'])==8 and all(r['passed'] for r in actual['cases'])
                assert actual['stage_b_started'] is False and actual['user_acceptance']=='pending'
                # Evidence images contain status tables only; even fixture frames are removed.
                page.locator('video').evaluate('(v)=>v.remove()')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=str(out/(mode+'.png')),full_page=True,animations='disabled')
                displays.append({'mode':mode,'checks':actual['cases'],'playback_advanced':True,'seek_seconds':1.5,'export_exact':True})
            unexpected=[m for m in console if not ('Failed to load resource' in m and ('422' in m or 'favicon' in m))]
            assert not errors and not unexpected,(errors,unexpected)
            assert not list(runtime.rglob('viewing-previews/**/*.mp4'))
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));vite_port=sock.getsockname()[1]
            vite_log=(work/'vite.log').open('w')
            vite=subprocess.Popen(['npm','run','dev','--','--mode','demo','--host','127.0.0.1','--port',str(vite_port)],
                cwd=ROOT/'apps/web',stdout=vite_log,stderr=vite_log,start_new_session=True)
            dev=f'http://127.0.0.1:{vite_port}'
            for _ in range(200):
                try:
                    with urlopen(dev,timeout=1):break
                except (URLError,TimeoutError):time.sleep(.1)
            else:raise RuntimeError('Vite did not start')
            private=runtime/'sealed.mp4';denials=[]
            for suffix in ('','?raw','?url','?import'):
                for method in ('GET','HEAD'):
                    req=Request(dev+'/@fs'+str(private)+suffix,method=method,headers={'Range':'bytes=0-15'})
                    try:
                        with urlopen(req,timeout=10) as response:status=response.status
                    except HTTPError as error:status=error.code
                    assert status in (403,404),(method,suffix,status)
                    denials.append({'method':method,'query':suffix,'status':status})
            dev_page=context.new_page();dev_page.on('pageerror',lambda e:errors.append(str(e)));dev_page.goto(dev)
            expect(dev_page.get_by_role('button',name='知识工作台',exact=True)).to_be_visible(timeout=30000)
            dev_page.get_by_role('button',name='知识工作台',exact=True).click()
            expect(dev_page.get_by_role('heading',name='定义知识，保留每次变化。',exact=True)).to_be_visible()
            assert not errors,errors
            result={'passed':True,'browser':browser.version,'scope':'generated test pattern and temporary DB only',
                'stage':'P0.3.7-A','stage_b_started':False,'user_acceptance':'pending','displays':displays,
                'preview_derivatives_created':0,'vite_private_file_denials':denials,'vite_normal_workbench_loaded':True,'page_errors':errors,'unexpected_console_errors':unexpected,
                'screenshots_contain_media_frames':False,'template_sha256':hashlib.sha256((ROOT/'demo/stage-a-live.html').read_bytes()).hexdigest()}
            (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
            context.close();browser.close()
    finally:
        server.terminate()
        try:server.wait(10)
        except subprocess.TimeoutExpired:server.kill();server.wait()
        log.close()
        if vite is not None:
            import os,signal
            os.killpg(vite.pid,signal.SIGTERM)
            try:vite.wait(10)
            except subprocess.TimeoutExpired:os.killpg(vite.pid,signal.SIGKILL);vite.wait()
        if vite_log:vite_log.close()


if __name__=='__main__':main()
