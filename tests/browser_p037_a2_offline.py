"""Validate the shipped media-free review Demo, not a simulated live API."""
import argparse,hashlib,json
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--chromium',default='/usr/bin/chromium')
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/p037-a2/offline-browser');args=p.parse_args()
    out=args.output.resolve();assert out.is_relative_to(ROOT/'artifacts');out.mkdir(parents=True,exist_ok=True)
    source=ROOT/'docs/demo/p0.3.7-a2';expected=json.loads((ROOT/'docs/delivery/p0.3.7-a2/verification.json').read_text())
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(source)))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}/index.html'
    errors=[];requests=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=args.chromium,args=['--no-sandbox'])
            page=browser.new_page(accept_downloads=True);page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
            for mode,size in [('desktop',{'width':1440,'height':1000}),('mobile',{'width':390,'height':844})]:
                page.set_viewport_size(size);page.goto(url)
                expect(page.locator('h1')).to_have_text('见微 P0.3.7-A.2 整改证据')
                expect(page.locator('#summary')).to_contain_text('682')
                expect(page.locator('.notice')).to_contain_text('原媒体/Base64专项未重跑')
                for group,count in [('all',44),('backup',18),('leases',7),('tool_versions',19)]:
                    page.get_by_label('专项',exact=True).select_option(group)
                    expect(page.locator('#rows tr')).to_have_count(count)
                with page.expect_download() as pending:page.get_by_role('button',name='下载完整执行证据',exact=True).click()
                pending.value.save_as(out/(mode+'.json'))
                assert json.loads((out/(mode+'.json')).read_text())==expected
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=str(out/(mode+'.png')),full_page=True,animations='disabled')
            assert not errors and all(u==url or u.startswith('blob:') for u in requests),(errors,requests)
            result={'passed':True,'browser':browser.version,'desktop_mobile':True,'exact_report_export':True,
                'all_counterexample_filters':True,'api_or_remote_requests':0,'page_errors':errors,
                'exact_media_cache_case':'not_rerun','independent_acceptance':'pending','stage_b_started':False,'html_sha256':hashlib.sha256((source/'index.html').read_bytes()).hexdigest()}
            (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result));browser.close()
    finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
