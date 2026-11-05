"""Real desktop/mobile B.1 file interactions; synthetic reviews are not truth."""
import argparse,json,hashlib
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import yaml
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--chromium',default='/usr/bin/chromium');p.add_argument('--report',type=Path);p.add_argument('--video',type=Path)
    a=p.parse_args();out=a.output.resolve();assert out.is_relative_to(ROOT/'artifacts');out.mkdir(parents=True,exist_ok=False)
    demo=json.loads((ROOT/'apps/web/src/data/localMotionDemo.json').read_text());expected=json.loads(a.report.read_text()) if a.report else demo
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(ROOT/'apps/web/dist-demo')))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}/index.html';records=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=a.chromium,args=['--no-sandbox'])
            for mode,width,height in [('desktop',1440,1000),('mobile',390,844)]:
                context=browser.new_context(viewport={'width':width,'height':height},accept_downloads=True,is_mobile=mode=='mobile',has_touch=mode=='mobile')
                page=context.new_page();errors=[];requests=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
                page.goto(url,wait_until='networkidle')
                if mode=='mobile':page.get_by_role('button',name='打开导航',exact=True).click()
                page.get_by_role('button',name='局部动态研究',exact=True).click()
                expect(page.get_by_role('heading',name='局部动态，回到操作现场。')).to_be_visible()
                expect(page.locator('.lm-notice')).to_contain_text('合成素材')
                public=json.loads((ROOT/'apps/web/src/data/localMotionStatistics.json').read_text())
                expect(page.locator('.lm-industrial-summary')).to_contain_text('不能视为纯净负对照')
                for arm in public['arms']:
                    page.get_by_label('工业汇总窗口',exact=True).select_option(arm['id'])
                    expect(page.locator('.lm-industrial-summary tbody tr').first.locator('td').nth(1)).to_have_text(f"{arm['operation_background'][0]['correlation']:.4f}")
                with page.expect_download() as download:page.get_by_role('button',name='导出工业汇总',exact=True).click()
                aggregate=out/(mode+'-industrial-aggregate.json');download.value.save_as(aggregate);assert json.loads(aggregate.read_text())==public
                # Exercise edits on the executed synthetic run only. No automatic
                # UI click is accepted as a real industrial human annotation.
                page.get_by_label('编辑ROI',exact=True).select_option('hands')
                page.get_by_label('ROI x2',exact=True).fill('0')
                expect(page.get_by_role('alert')).to_contain_text('ROI为空或越界')
                page.get_by_label('ROI x2',exact=True).fill('.8')
                page.get_by_role('button',name='框选ROI',exact=True).click();canvas=page.get_by_label('ROI框选画布',exact=True);canvas.scroll_into_view_if_needed();box=canvas.bounding_box()
                page.mouse.move(box['x']+box['width']*.2,box['y']+box['height']*.2);page.mouse.down();page.mouse.move(box['x']+box['width']*.75,box['y']+box['height']*.7,steps=6);page.mouse.up()
                page.get_by_role('button',name='退出框选',exact=True).click()
                expect(page.locator('.lm-top-grid .lm-warning').first).to_contain_text('曲线仍对应原已执行ROI')
                with page.expect_download() as download:page.get_by_role('button',name='导出ROI配置草稿',exact=True).click()
                path=out/(mode+'-roi.yaml');download.value.save_as(path);draft=yaml.safe_load(path.read_text())
                chosen=next(r for r in draft['local_motion']['rois'] if r['id']=='hands');assert abs(chosen['xyxy'][0]-.2)<.02 and abs(chosen['xyxy'][2]-.75)<.02
                page.get_by_label('边界审核人',exact=True).fill('自动浏览器验收（合成工程，不是人工真值）')
                rows=page.get_by_test_id('lm-candidates').locator('tbody tr');assert rows.count()==demo['primary']['count']>0
                rows.first.get_by_role('button',name='确认',exact=True).click()
                page.get_by_label('人工边界PTS',exact=True).fill(str(demo['primary']['candidates'][0]['pts_ms']+100))
                rows.first.get_by_role('button',name='按当前输入修订',exact=True).click();rows.first.get_by_role('button',name='删除候选',exact=True).click()
                page.get_by_label('人工边界PTS',exact=True).fill('3000');page.get_by_role('button',name='新增人工边界',exact=True).click()
                with page.expect_download() as download:page.get_by_role('button',name='导出人工复核',exact=True).click()
                manual=out/(mode+'-synthetic-manual.json');download.value.save_as(manual);doc=json.loads(manual.read_text())
                assert [e['action'] for e in doc['events']]==['confirm','adjust','delete','add'] and not doc['coverage_complete']
                assert doc['source_sha256']==demo['source']['sha256']
                wrong={**doc,'source_sha256':'b'*64};wrong_path=out/(mode+'-wrong-review.json');wrong_path.write_text(json.dumps(wrong))
                page.get_by_label('人工边界复核JSON',exact=True).set_input_files(wrong_path)
                expect(page.locator('.lm-message')).to_contain_text('不匹配')
                if a.report:
                    page.get_by_label('局部动态实验JSON',exact=True).set_input_files(a.report)
                    expect(page.get_by_label('局部动态实验版本',exact=True)).to_have_value(expected['run_id'])
                for arm in expected['arms']:
                    page.get_by_label('局部动态窗口密度',exact=True).select_option(arm['id'])
                    first=next(s for s in arm['series'] if s['id']=='global:r3d')
                    expect(page.locator('.lm-curves .lm-table tbody tr').first.locator('td').nth(2)).to_have_text('—' if first['stats']['p50'] is None else f"{first['stats']['p50']:.4f}")
                page.get_by_label('局部动态窗口密度',exact=True).select_option(expected['primary']['arm'])
                actual_video=False;seek_error=None
                if a.video:
                    # A private incorrect copy tests SHA rejection, never modifies the source.
                    body=bytearray(a.video.read_bytes());body[-1]^=1;bad=out/(mode+'-wrong.mp4');bad.write_bytes(body)
                    page.get_by_label('局部动态原视频文件',exact=True).set_input_files(bad)
                    expect(page.get_by_text('原片SHA不匹配，未加载视频',exact=True)).to_be_visible()
                    assert page.locator('video').count()==0
                    page.get_by_label('局部动态原视频文件',exact=True).set_input_files(a.video)
                    expect(page.get_by_text('原片SHA与显示尺寸均已核验',exact=True)).to_be_visible(timeout=30000)
                    target=min(expected['settings']['end_ms']-1000,expected['settings']['start_ms']+3000)
                    page.get_by_label('局部动态PTS时间轴',exact=True).fill(str(target))
                    page.wait_for_function('t=>Math.abs(document.querySelector("video").currentTime-t)<.12',arg=target/1000)
                    seek_error=page.locator('video').evaluate('(v,t)=>Math.abs(v.currentTime-t)',target/1000)
                    page.locator('video').evaluate('v=>v.muted=true');page.get_by_role('button',name='播放原片',exact=True).click()
                    page.wait_for_function('t=>document.querySelector("video").currentTime>t+.1',arg=target/1000)
                    page.locator('video').evaluate('v=>v.pause()');actual_video=True
                with page.expect_download() as download:page.get_by_role('button',name='导出实验结果',exact=True).click()
                exported=out/(mode+'-report.json');download.value.save_as(exported);assert json.loads(exported.read_text())==expected
                assert not errors,errors
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'horizontal overflow'
                assert not any('/api/' in u or not (u.startswith(url.rsplit('/',1)[0]) or u.startswith('blob:')) for u in requests),requests
                # Every published screenshot masks the video, including synthetic.
                if page.locator('video').count():
                    page.locator('video').evaluate('v=>v.style.visibility="hidden"')
                page.locator('.lm-video-wrap').evaluate('e=>{const d=document.createElement("div");d.textContent="原片画面已从交付截图隐藏";d.style.cssText="position:absolute;inset:0;display:grid;place-items:center;background:#112a30;color:white";e.append(d)}')
                page.screenshot(path=str(out/(mode+'.png')),full_page=True,animations='disabled')
                records.append({'mode':mode,'passed':True,'roi_drag_and_invalid_rejection':True,'manual_confirm_adjust_delete_add':'synthetic_only_not_truth',
                    'review_source_mismatch_rejected':True,'all_sampling_arms':len(expected['arms']),'exact_report_export':True,'original_video_playback':actual_video,
                    'seek_error_seconds':seek_error,'industrial_frames_published':False,'remote_or_api_requests':0,'console_page_errors':errors})
                context.close()
            result={'passed':True,'browser':browser.version,'records':records,'source_asset':expected['source']['asset_id'],'run_id':expected['run_id'],
                'real_human_annotations_created':False,'b2_started':False,'build_sha256':hashlib.sha256((ROOT/'apps/web/dist-demo/index.html').read_bytes()).hexdigest()}
            (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));browser.close()
    finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
