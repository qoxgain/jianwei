"""Desktop/mobile executed zero-label research. Generated media only in captures."""
import argparse,hashlib,json,sys
from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright,expect


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--query',type=Path,required=True);p.add_argument('--video',type=Path,required=True);p.add_argument('--chromium',default='/usr/bin/chromium');p.add_argument('--industrial-report',type=Path);p.add_argument('--industrial-video',type=Path);a=p.parse_args()
    report=json.loads(a.report.read_text());query=json.loads(a.query.read_text());assert report['evidence_class']==query['evidence_class']=='synthetic_engineering'
    out=a.output.resolve();assert out.is_relative_to(ROOT/'artifacts');out.mkdir(parents=True,exist_ok=False)
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(ROOT/'apps/web/dist-demo')));thread=Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}/index.html';records=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=a.chromium,args=['--no-sandbox'])
            for mode,width,height in [('desktop',1440,1000),('mobile',390,844)]:
                context=browser.new_context(viewport={'width':width,'height':height},is_mobile=mode=='mobile',has_touch=mode=='mobile',accept_downloads=True);page=context.new_page();errors=[];requests=[]
                page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url));page.goto(url,wait_until='networkidle')
                if mode=='mobile':page.get_by_role('button',name='打开导航',exact=True).click()
                page.get_by_role('button',name='无标注模式研究',exact=True).click();expect(page.get_by_test_id('unlabeled-industrial')).to_be_visible()
                assert page.get_by_test_id('unlabeled-industrial').locator('tbody tr').count()==11
                page.get_by_label('无标注研究文件',exact=True).set_input_files(a.report);expect(page.locator('.unlabeled-panel')).to_contain_text(report['run_id'][:12]);page.get_by_label('无标注跨来源查询',exact=True).set_input_files(a.query)
                expect(page.get_by_test_id('unlabeled-query')).to_be_visible();assert page.get_by_test_id('unlabeled-query').locator('tbody tr').count()==len(query['methods'])
                trials=0
                for m in report['methods']:
                    if m['status']!='computed':continue
                    page.get_by_label('无标注特征表示',exact=True).select_option(m['method'])
                    for role in ['calibration','replay']:
                        page.get_by_label('无标注时间角色',exact=True).select_option(role)
                        for s in m['evaluations'][role]['segmentation']:
                            page.get_by_label('无标注分段方法',exact=True).select_option(s['method']);assert page.get_by_test_id('unlabeled-segments').locator('button').count()==s['segment_count'];trials+=1
                    assert page.get_by_test_id('unlabeled-prototypes').locator('tbody tr').count()==len(m['prototypes'])
                    assert page.get_by_test_id('unlabeled-controls').locator('tbody tr').count()==len(m['controls_all_results'])
                chosen=next(m for m in report['methods'] if m['status']=='computed' and m['method']=='block_motion');page.get_by_label('无标注特征表示',exact=True).select_option(chosen['method'])
                # Bad same-size source must not acquire a playback URL.
                body=bytearray(a.video.read_bytes());body[-1]^=1;wrong=out/(mode+'-wrong-generated.mp4');wrong.write_bytes(body)
                page.get_by_label('无标注原视频',exact=True).set_input_files(wrong);expect(page.get_by_test_id('unlabeled-media-status')).to_have_text('原片SHA不匹配，未加载');assert page.locator('video').count()==0
                page.get_by_label('无标注原视频',exact=True).set_input_files(a.video);expect(page.get_by_test_id('unlabeled-media-status')).to_have_text('原片SHA与显示尺寸已核验',timeout=30000)
                page.get_by_label('无标注PTS定位',exact=True).fill('12000');page.wait_for_function('()=>!document.querySelector("video").seeking&&Math.abs(document.querySelector("video").currentTime-12)<.1')
                page.locator('video').evaluate('async v=>{v.muted=true;await v.play()}');page.wait_for_function('()=>document.querySelector("video").currentTime>12.2');page.locator('video').evaluate('v=>v.pause()')
                if chosen['prototypes']:
                    position=chosen['prototypes'][0]['occurrences'][-1]['start_ms'];page.get_by_test_id('unlabeled-prototypes').locator('tbody tr').first.locator('button').last.click();page.wait_for_function('t=>!document.querySelector("video").seeking&&Math.abs(document.querySelector("video").currentTime*1000-t)<100',arg=position)
                with page.expect_download() as d:page.get_by_role('button',name='导出当前研究',exact=True).click()
                exported=out/(mode+'-synthetic-report.json');d.value.save_as(exported);assert json.loads(exported.read_text())==report
                malformed={**report,'real_f1':.99};bad=out/(mode+'-invalid-study.json');bad.write_text(json.dumps(malformed));page.get_by_label('无标注研究文件',exact=True).set_input_files(bad);expect(page.locator('.lm-message')).to_contain_text('必须为null');expect(page.locator('.unlabeled-panel')).to_contain_text(report['run_id'][:12])
                bad_query={**query,'parent_run_id':'a'*64};bad.write_text(json.dumps(bad_query));page.get_by_label('无标注跨来源查询',exact=True).set_input_files(bad);expect(page.locator('.lm-message')).to_contain_text('不匹配')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'responsive viewport overflow';assert not errors,errors
                # This screenshot is generated material only; industrial import happens afterwards.
                page.screenshot(path=str(out/(mode+'-synthetic.png')),full_page=True,animations='disabled')
                field_check=False
                if a.industrial_report:
                    assert a.industrial_video;real=json.loads(a.industrial_report.read_text());page.get_by_label('无标注研究文件',exact=True).set_input_files(a.industrial_report)
                    expect(page.locator('.unlabeled-panel')).to_contain_text('本机工业私有研究');page.get_by_label('无标注原视频',exact=True).set_input_files(a.industrial_video);expect(page.get_by_test_id('unlabeled-media-status')).to_have_text('原片SHA与显示尺寸已核验',timeout=30000)
                    assert page.get_by_label('无标注特征表示',exact=True).locator('option').count()==len(real['methods']);page.get_by_label('无标注PTS定位',exact=True).fill('60000');page.wait_for_function('()=>!document.querySelector("video").seeking&&Math.abs(document.querySelector("video").currentTime-60)<.1');field_check=True
                assert not any('/api/' in u or not (u.startswith(url.rsplit('/',1)[0]) or u.startswith('blob:')) for u in requests),requests
                records.append({'mode':mode,'passed':True,'four_segment_methods_and_all_roles':trials,'method_comparison_count':len(report['methods']),'actual_video_playback':True,'sha_refusal':True,'candidate_seek':True,'report_exact_export':True,'invalid_truth_import_refused':True,'frozen_parent_query_refused':True,'industrial_private_read_and_seek':field_check,'api_or_remote_requests':0,'page_errors':errors});context.close()
            result={'passed':True,'browser':browser.version,'build_sha256':hashlib.sha256((ROOT/'apps/web/dist-demo/index.html').read_bytes()).hexdigest(),'records':records,'real_precision':None,'real_recall':None,'real_f1':None,'production_updated':False,'p04_admitted':False};(out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));browser.close()
    finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
