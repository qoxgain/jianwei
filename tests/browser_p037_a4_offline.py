"""Actual A.4 desktop/mobile evidence filters, complete failures and exact JSON export."""
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
    source=ROOT/'docs/demo/p0.3.7-a4';expected=json.loads((ROOT/'docs/delivery/p0.3.7-a4/verification.json').read_text())
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(source)))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}/index.html'
    errors=[];requests=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=a.chromium,args=['--no-sandbox']);page=browser.new_page(accept_downloads=True)
            page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
            for mode,size in [('desktop',{'width':1440,'height':1000}),('mobile',{'width':390,'height':844})]:
                page.set_viewport_size(size);page.goto(url)
                expect(page.locator('h1')).to_have_text('见微 P0.3.7-A.4 隔离与性能证据')
                expect(page.locator('.notice')).to_contain_text('精确原件复验blocked')
                expect(page.locator('#summary')).to_contain_text(str(expected['backend']['tests']-expected['backend']['skipped']))
                for mb,count in [('79',8),('1',4),('160',4)]:
                    page.get_by_label('容量',exact=True).select_option(mb);expect(page.locator('#runs tr')).to_have_count(count)
                page.get_by_label('容量',exact=True).select_option('79')
                page.get_by_label('日志',exact=True).select_option('WAL');page.get_by_label('写者',exact=True).select_option('4')
                page.get_by_label('备份',exact=True).select_option('true');expect(page.locator('#runs tr')).to_have_count(8)
                page.get_by_label('备份',exact=True).select_option('false');page.get_by_label('状态',exact=True).select_option('true')
                expect(page.locator('#runs tr')).to_have_count(4)
                page.get_by_label('容量',exact=True).select_option('1');expect(page.locator('#runs tr')).to_have_count(0)
                expect(page.locator('#selection')).to_contain_text('未测量，不补造数据')
                for group,count in [('all',55),('isolation',11),('admission',22),('performance_policy',17),('failure_seal',5)]:
                    page.get_by_label('专项',exact=True).select_option(group);expect(page.locator('#tests tr')).to_have_count(count)
                page.locator('details').evaluate('(e)=>e.open=true')
                expect(page.locator('#failures tr')).to_have_count(expected['performance']['summary']['failed']+expected['initial_performance']['summary']['failed']+expected['intermediate_performance']['summary']['failed'])
                with page.expect_download() as download:page.get_by_role('button',name='下载全部实际执行证据',exact=True).click()
                download.value.save_as(out/(mode+'.json'));assert json.loads((out/(mode+'.json')).read_text())==expected
                for label,value in [('容量','79'),('日志','DELETE'),('写者','1'),('备份','false'),('状态','false'),('专项','all')]:
                    page.get_by_label(label,exact=True).select_option(value)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=str(out/(mode+'.png')),full_page=True,animations='disabled')
            assert not errors and all(r==url or r.startswith('blob:') for r in requests),(errors,requests)
            result={'passed':True,'browser':browser.version,'desktop_mobile':True,'performance_filters':True,
                'all_failures_visible':expected['performance']['summary']['failed']+expected['initial_performance']['summary']['failed']+expected['intermediate_performance']['summary']['failed'],
                'all_55_new_test_filters':True,
                'unmeasured_not_fabricated':True,'exact_export':True,'api_or_remote_requests':0,'page_errors':errors,
                'original_corrupt_copy':'blocked_missing_original','fault_state_architecture_status':'pending_independent_review',
                'stage_b_started':False,'html_sha256':hashlib.sha256((source/'index.html').read_bytes()).hexdigest()}
            (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));browser.close()
    finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
