"""Actual Chromium review of executed scalar statistics; never publish video frames."""
import argparse,hashlib,json,re
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import sys
from playwright.sync_api import expect,sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chromium',default='/usr/bin/chromium');p.add_argument('--output',type=Path,default=ROOT/'artifacts/p036-browser')
    p.add_argument('--skip-local-video',action='store_true',help='CI only: keep the separate existing real-video browser check')
    args=p.parse_args();out=args.output.resolve()
    if not out.is_relative_to(ROOT/'artifacts'):p.error('Use isolated private evidence output')
    out.mkdir(parents=True,exist_ok=True)
    data=json.loads((ROOT/'apps/web/src/data/robustness.json').read_text())
    assert len(data['assets'])==9 and data['formal_status']=='blocked'
    http=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(ROOT/'apps/web/dist-demo')))
    thread=Thread(target=http.serve_forever,daemon=True);thread.start();address=f'http://127.0.0.1:{http.server_port}/index.html'
    cases=[];requests=[];errors=[];console=[]
    try:
      with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=args.chromium,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN',accept_downloads=True)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('console',lambda m:console.append(m.text) if m.type=='error' else None)
        page.on('request',lambda r:requests.append(r.url))
        page.goto(address);page.get_by_role('button',name='真实视频学习实验',exact=True).click()
        panel=page.locator('.robustness-panel');expect(panel).to_be_visible()
        expect(panel).to_contain_text('正式研究 blocked')
        for asset in data['assets']:
            identifier=asset['asset_id'];panel.get_by_label('稳健性视频',exact=True).select_option(identifier)
            panel.get_by_label('历史长度队列',exact=True).select_option('8')
            primary=asset['cohorts']['8'];runs=primary['runs']
            if not runs:
                expect(panel).to_contain_text('blocked：划分后窗口不足')
                expect(panel.get_by_label('随机种子',exact=True)).to_have_count(0)
            else:
                assert len(runs)==5
                panel.get_by_label('稳健性预测跨度',exact=True).select_option('0')
                panel.get_by_label('稳健性评价分区',exact=True).select_option('holdout')
                for run in runs:
                    panel.get_by_label('随机种子',exact=True).select_option(str(run['seed']))
                    table=panel.locator('table').filter(has=page.locator('caption',has_text='各模型实际结果'))
                    expect(table.locator('tbody tr')).to_have_count(18)
                    row=table.locator('tbody tr').filter(has=page.get_by_text('Ridge',exact=True))
                    actual=float(row.locator('td').nth(0).inner_text())
                    expected=run['splits']['holdout']['metrics']['ridge']['feature_mse'][0]
                    assert abs(actual-expected)<=1e-6
                for horizon in ('0','1','2'):panel.get_by_label('稳健性预测跨度',exact=True).select_option(horizon)
                for model in ('initial_tcn','causal_tcn','delta_tcn','multiscale_delta_tcn','delta_shuffled_history','train_mean'):
                    panel.get_by_label('显示模型',exact=True).select_option(model)
                panel.get_by_label('显示模型',exact=True).select_option('multiscale_delta_tcn')
                panel.get_by_label('随机种子',exact=True).select_option('23')
                for history,cohort in asset['cohorts'].items():
                    panel.get_by_label('历史长度队列',exact=True).select_option(history)
                    if cohort['runs']:
                        expect(panel.get_by_label('随机种子',exact=True)).to_be_visible()
                        for split in ('train','validation','holdout'):panel.get_by_label('稳健性评价分区',exact=True).select_option(split)
                        for points in panel.locator('polyline').evaluate_all('(els)=>els.map(e=>e.getAttribute("points"))'):
                            assert 'NaN' not in points and 'Infinity' not in points
                    else:expect(panel).to_contain_text('blocked：划分后窗口不足')
                panel.get_by_label('历史长度队列',exact=True).select_option('8')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
            panel.screenshot(path=str(out/f'{identifier}-desktop.png'),animations='disabled')
            if identifier in ('bull_station_05','bull_station_07','garment_test','garment_test_short'):
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                panel.screenshot(path=str(out/f'{identifier}-mobile.png'),animations='disabled')
                page.set_viewport_size({'width':1440,'height':1000})
            cases.append({'source':identifier,'all_seeds_checked':len(runs),'cohorts_checked':list(asset['cohorts']),
                'blocked_without_fabrication':not bool(runs),'scalar_table_matches_executed_json':True})
        with page.expect_download() as pending:panel.get_by_role('button',name='导出本轮实际统计',exact=True).click()
        pending.value.save_as(out/'exported-statistics.json')
        assert json.loads((out/'exported-statistics.json').read_text())==data
        # Legacy, executed A/B records remain explicitly separate and reviewable.
        page.get_by_role('button',name='P0.3.5 历史探索',exact=True).click()
        expect(page.get_by_text('2026-11-02',exact=False)).to_be_visible()
        page.get_by_label('真实来源',exact=True).select_option('garment_test_short')
        page.get_by_label('实验方案',exact=True).select_option('B')
        expect(page.locator('.real-learning').last).to_contain_text('blocked')
        page.get_by_label('实验方案',exact=True).select_option('A')
        expect(page.get_by_text('TCN 参数更新 L2：',exact=False)).to_be_visible()
        page.get_by_role('button',name='P0.3.6 稳健性研究',exact=True).click()
        panel=page.locator('.robustness-panel')
        local={'executed':False,'scope':'local video check omitted; separate CI original-video check retained'}
        if not args.skip_local_video:
            panel.get_by_label('稳健性视频',exact=True).select_option('garment_test')
            panel.get_by_label('历史长度队列',exact=True).select_option('8')
            panel.get_by_label('稳健性预测跨度',exact=True).select_option('0')
            field=panel.get_by_label('稳健性本机原片',exact=True)
            field.set_input_files({'name':'wrong.mp4','mimeType':'video/mp4','buffer':b'wrong source validation fixture'})
            expect(panel).to_contain_text('SHA 不匹配')
            expect(panel.locator('video')).to_have_count(0)
            field.set_input_files(ROOT/'datasets/video_samples/garment_quality/videos/test.mp4')
            expect(panel).to_contain_text('原片 SHA 匹配',timeout=60000)
            page.wait_for_function('document.querySelector(".robustness-panel video")?.readyState>=1',timeout=30000)
            panel.get_by_role('button',name='回查未来观测',exact=True).click()
            page.wait_for_function('(()=>{const v=document.querySelector(".robustness-panel video");return v&&v.currentTime>0&&!v.seeking&&v.readyState>=2})()',timeout=30000)
            assert panel.locator('video').evaluate('(v)=>v.currentTime')>0
            local={'executed':True,'original_sha_matched':True,'wrong_sha_rejected':True,
                   'metadata_loaded':True,'future_clip_seek':True,'frames_screenshotted':False}
            # Remove the player before any further screenshot/evidence export.
            panel.get_by_label('稳健性视频',exact=True).select_option('bull_station_07')
            expect(panel.locator('video')).to_have_count(0)
        assert not errors,errors;assert not console,console
        assert not [u for u in requests if u.startswith(('http:','https:')) and u!=address],requests
        result={'passed':True,'browser':browser.version,'cases':cases,'desktop_mobile':True,'exact_report_export':True,
            'local_video':local,'api_or_remote_requests':0,'page_errors':errors,'console_errors':console,
            'screenshots_contain_media_frames':False,'formal_research_passed':False,
            'build_sha256':hashlib.sha256((ROOT/'apps/web/dist-demo/index.html').read_bytes()).hexdigest(),
            'statistics_sha256':hashlib.sha256((ROOT/'apps/web/src/data/robustness.json').read_bytes()).hexdigest()}
        save(out/'browser-results.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))
        context.close();browser.close()
    finally:http.shutdown();http.server_close();thread.join()


if __name__=='__main__':main()
