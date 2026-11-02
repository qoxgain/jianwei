"""Review actual recorded results in the built demo; captures numerical UI only."""
import argparse
import hashlib
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from functools import partial
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser();p.add_argument('--chromium',default='/usr/bin/chromium');p.add_argument('--output',type=Path,default=ROOT/'artifacts/exploratory/browser');args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(ROOT/'apps/web/dist-demo')))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    data=json.loads((ROOT/'apps/web/src/data/realVideoLearning.json').read_text());assert len(data['assets'])==9
    errors=[];cases=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=args.chromium,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN',accept_downloads=True)
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.get_by_role('button',name='真实视频学习实验',exact=True).click()
            expect(page.get_by_role('heading',name='真实视频学习实验',exact=True)).to_be_visible()
            for asset in data['assets']:
                page.get_by_label('真实来源',exact=True).select_option(asset['asset_id'])
                for mode in ('A','B'):
                    page.get_by_label('实验方案',exact=True).select_option(mode)
                    report=asset['modes'][mode]
                    expect(page.get_by_text('执行状态：'+report['status'],exact=False)).to_be_visible()
                    if report['status']=='executed':
                        expect(page.get_by_role('img',name='TCN 实际训练损失 · 横轴为 epoch',exact=True)).to_be_visible()
                        for horizon in ('0','1','2'):page.get_by_label('预测跨度',exact=True).select_option(horizon)
                        for split in ('train','validation','holdout'):page.get_by_label('评价区间',exact=True).select_option(split)
                    cases.append({'asset':asset['asset_id'],'mode':mode,'status':report['status']})
            with page.expect_download() as pending:page.get_by_role('button',name='导出实际实验报告',exact=True).click()
            pending.value.save_as(args.output/'download.json')
            assert json.loads((args.output/'download.json').read_text())==data
            for label,size in [('desktop',{'width':1440,'height':1000}),('mobile',{'width':390,'height':844})]:
                page.set_viewport_size(size)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.locator('.real-learning').screenshot(path=str(args.output/f'{label}.png'),animations='disabled')
            assert not errors,errors
            version=browser.version;browser.close()
    finally:server.shutdown();server.server_close();thread.join()
    result={'passed':True,'browser':version,'cases':cases,'page_errors':errors,'export_matches_actual_record':True,
        'desktop_mobile':True,'industrial_frames_included':False,'html_sha256':hashlib.sha256((ROOT/'apps/web/dist-demo/index.html').read_bytes()).hexdigest()}
    (args.output/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
