"""Actual media-free desktop/mobile A.3 evidence browsing, filters and exact export."""
import argparse,hashlib,json
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--chromium',default='/usr/bin/chromium')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.resolve()
    assert out.is_relative_to(ROOT/'artifacts');out.mkdir(parents=True,exist_ok=False)
    source=ROOT/'docs/demo/p0.3.7-a3';expected=json.loads((ROOT/'docs/delivery/p0.3.7-a3/verification.json').read_text())
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(source)))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}/index.html'
    errors=[];requests=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=a.chromium,args=['--no-sandbox'])
            page=browser.new_page(accept_downloads=True);page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:requests.append(r.url))
            for mode,size in [('desktop',{'width':1440,'height':1000}),('mobile',{'width':390,'height':844})]:
                page.set_viewport_size(size);page.goto(url)
                expect(page.locator('h1')).to_have_text('见微 P0.3.7-A.3 SQLite整改证据')
                expect(page.locator('#summary')).to_contain_text('710')
                expect(page.locator('.notice')).to_contain_text('原2/11损坏根因未确认')
                for group,count in [('all',28),('integrity',19),('backup',8),('forensics',1)]:
                    page.get_by_label('专项',exact=True).select_option(group);expect(page.locator('#rows tr')).to_have_count(count)
                for version,count in [('all',66),('baseline',22),('a3_initial',22),('a3',22)]:
                    page.get_by_label('代码',exact=True).select_option(version);expect(page.locator('#runs tr')).to_have_count(count)
                with page.expect_download() as download:page.get_by_role('button',name='下载完整执行证据',exact=True).click()
                download.value.save_as(out/(mode+'.json'));assert json.loads((out/(mode+'.json')).read_text())==expected
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=str(out/(mode+'.png')),full_page=True,animations='disabled')
            assert not errors and all(r==url or r.startswith('blob:') for r in requests),(errors,requests)
            result={'passed':True,'browser':browser.version,'desktop_mobile':True,'all_28_test_filters':True,
                'all_66_runs_visible':True,'exact_report_export':True,'api_or_remote_requests':0,'page_errors':errors,
                'independent_corrupt_copy':'blocked_not_available','original_root_cause':'root_cause_not_established',
                'stage_b_started':False,'html_sha256':hashlib.sha256((source/'index.html').read_bytes()).hexdigest()}
            (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));browser.close()
    finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
