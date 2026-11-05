"""Real browser engineering on generated annotations, never industrial truth."""
import argparse,json,hashlib,uuid,sys
from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright,expect
from packages.local_motion.annotation import compile_annotations
from packages.knowledge.service import content_hash
from scripts.evaluate_b11 import evaluate_files

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--video',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--industrial-source',type=Path);p.add_argument('--industrial-video',type=Path);p.add_argument('--chromium',default='/usr/bin/chromium');a=p.parse_args()
    source=json.loads(a.source.read_text());assert source['evidence_class']=='synthetic_engineering','Automatic labels only on generated engineering material'
    out=a.output.resolve();assert out.is_relative_to(ROOT/'artifacts');out.mkdir(parents=True,exist_ok=False)
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(ROOT/'apps/web/dist-demo')));thread=Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}/index.html';records=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=a.chromium,args=['--no-sandbox'])
            for mode,width,height in [('desktop',1440,1000),('mobile',390,844)]:
                context=browser.new_context(viewport={'width':width,'height':height},accept_downloads=True,is_mobile=mode=='mobile',has_touch=mode=='mobile');page=context.new_page();errors=[];requests=[]
                page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
                def open_panel():
                    if mode=='mobile':page.get_by_role('button',name='打开导航',exact=True).click()
                    page.get_by_role('button',name='动作边界盲标注',exact=True).click()
                def load_source(path):page.get_by_label('独立标注源PTS文件',exact=True).set_input_files(path)
                def associate(path,s):
                    body=bytearray(path.read_bytes());body[-1]^=1;bad=out/(mode+'-incorrect.mp4');bad.write_bytes(body)
                    page.get_by_label('独立标注原视频',exact=True).set_input_files(bad);expect(page.get_by_text('原片SHA不匹配，未加载',exact=True)).to_be_visible();assert page.locator('video').count()==0
                    page.get_by_label('独立标注原视频',exact=True).set_input_files(path);expect(page.get_by_text('原片SHA与显示尺寸已核验',exact=True)).to_be_visible(timeout=30000)
                    receipts=[]
                    for i in [1,2,7,len(s['frames'])//2,len(s['frames'])*3//4,len(s['frames'])-2,len(s['frames'])-1]:
                        page.get_by_label('独立标注原始帧时间轴',exact=True).fill(str(i))
                        expect(page.get_by_test_id('annotation-frame-receipt')).not_to_contain_text('未验证',timeout=15000)
                        import re
                        text=page.get_by_test_id('annotation-frame-receipt').inner_text();error=float(re.search(r'误差 ([\d.]+) ms',text)[1]);assert error<=2,(i,text);receipts.append(error)
                    page.get_by_label('独立标注原始帧时间轴',exact=True).fill('7');page.get_by_role('button',name='下一原始帧',exact=True).click();page.get_by_role('button',name='上一原始帧',exact=True).click();page.wait_for_function('()=>!document.querySelector("video").seeking&&document.querySelector("video").currentTime<2');page.locator('video').evaluate('v=>v.muted=true');before=page.locator('video').evaluate('v=>v.currentTime');page.get_by_role('button',name='播放',exact=True).click();page.wait_for_function('t=>document.querySelector("video").currentTime>t+.1',arg=before);page.get_by_role('button',name='暂停',exact=True).click()
                    return receipts
                page.goto(url,wait_until='networkidle');open_panel();load_source(a.source)
                expect(page.locator('.annotation-panel')).to_contain_text('盲标声明')
                assert page.locator('.lm-chart,.lm-industrial-summary,[data-testid="lm-candidates"]').count()==0
                frame_errors=associate(a.video,source)
                page.get_by_label('独立标注人员',exact=True).fill('自动浏览器工程测试，不是工业真人标注')
                page.get_by_label('独立人工边界PTS',exact=True).fill('1000');page.get_by_role('button',name='添加独立人工边界',exact=True).click()
                row=page.get_by_test_id('independent-boundaries').locator('tbody tr').first
                page.get_by_label('独立人工边界PTS',exact=True).fill('1100');page.get_by_label('独立人工边界类型',exact=True).select_option('offset');row.get_by_role('button',name='按输入修订',exact=True).click();row.get_by_role('button',name='记录复核',exact=True).click()
                page.get_by_label('独立人工边界PTS',exact=True).fill('3000');page.get_by_role('button',name='添加独立人工边界',exact=True).click();page.get_by_test_id('independent-boundaries').locator('tbody tr').last.get_by_role('button',name='删除人工边界',exact=True).click()
                def interval(start,end,kind):
                    page.get_by_label('审核区间起点',exact=True).fill(str(start));page.get_by_label('审核区间终点',exact=True).fill(str(end));page.get_by_label('审核区间类型',exact=True).select_option(kind);page.get_by_role('button',name='记录人工区间',exact=True).click()
                end=source['source']['last_pts_end_ms'];interval(0,end-500,'reviewed')
                page.get_by_label('声明完整人工审核',exact=True).check();page.get_by_label('人工标注真实性声明',exact=True).check();page.get_by_role('button',name='封存当前标注版本',exact=True).click();expect(page.locator('.lm-message')).to_contain_text('补齐全部审核区间')
                interval(end-500,end,'reviewed')
                for start,stop,kind in [(2000,2500,'no_action'),(4000,4500,'uncertain'),(6000,6500,'lighting'),(8000,8500,'occluded'),(8700,9000,'background_invalid')]:interval(start,stop,kind)
                page.get_by_role('button',name='封存当前标注版本',exact=True).click();expect(page.get_by_role('button',name='新建修订草稿',exact=True)).to_be_enabled()
                with page.expect_download() as download:page.get_by_role('button',name='导出标注JSON',exact=True).click()
                annotation=out/(mode+'-synthetic-annotations.json');download.value.save_as(annotation);doc=json.loads(annotation.read_text());compiled=compile_annotations(source,doc)
                assert compiled['eligible_for_evaluation'] and len(compiled['boundaries'])==1 and compiled['boundaries'][0]['pts_ms']==1100
                assert {'boundary_add','boundary_update','boundary_review','boundary_delete','interval_add','attest'}<={e['op'] for e in doc['events']}
                evaluated=evaluate_files(a.report,a.source,annotation,ROOT/'apps/web/src/data/localMotionStatistics.json');assert evaluated['status']=='evaluated_synthetic_engineering_only';result_path=out/(mode+'-synthetic-evaluation.json');result_path.write_text(json.dumps(evaluated,ensure_ascii=False))
                page.get_by_label('固定预测评价文件',exact=True).set_input_files(result_path);expect(page.get_by_test_id('fixed-evaluation')).to_be_visible();assert page.get_by_test_id('fixed-evaluation').locator('table').first.locator('tbody tr').count()==14
                malformed={**evaluated,'methods':'corrupt'};malformed_path=out/(mode+'-bad-evaluation.json');malformed_path.write_text(json.dumps(malformed));page.get_by_label('固定预测评价文件',exact=True).set_input_files(malformed_path);expect(page.locator('.lm-message')).to_contain_text('评价格式无效');assert page.get_by_test_id('fixed-evaluation').locator('table').first.locator('tbody tr').count()==14
                # Reload and recover the same immutable annotation version.
                page.reload(wait_until='networkidle');open_panel();load_source(a.source);expect(page.get_by_role('button',name='新建修订草稿',exact=True)).to_be_enabled();expect(page.get_by_label('独立标注人员',exact=True)).to_have_value(doc['annotator'])
                wrong={**doc,'source_sha256':'b'*64};wrong_path=out/(mode+'-wrong-annotations.json');wrong_path.write_text(json.dumps(wrong));page.get_by_label('独立标注恢复文件',exact=True).set_input_files(wrong_path);expect(page.locator('.lm-message')).to_contain_text('不匹配')
                second=json.loads(annotation.read_text());second.update(annotation_id=str(uuid.uuid4()),annotator='第二位自动合成工程测试员')
                for e in second['events']:e['actor']=second['annotator']
                for e in second['events']:
                    if e['op']=='boundary_update':e['kind']='onset'
                second['sealed_hash']=content_hash({k:v for k,v in second.items() if k!='sealed_hash'});second_path=out/(mode+'-second-synthetic.json');second_path.write_text(json.dumps(second,ensure_ascii=False));page.get_by_label('第二位独立标注文件',exact=True).set_input_files(second_path);expect(page.get_by_text('类型分歧 1',exact=False)).to_be_visible()
                page.get_by_role('button',name='新建修订草稿',exact=True).click();expect(page.locator('.annotation-panel')).to_contain_text('辅助标注 · 已知提示偏差')
                real_frame_errors=[]
                if a.industrial_source:
                    assert a.industrial_video;real=json.loads(a.industrial_source.read_text());load_source(a.industrial_source);expect(page.locator('.annotation-panel')).to_contain_text('真实开发视频 · 等待真人标注');real_frame_errors=associate(a.industrial_video,real)
                    assert page.get_by_test_id('independent-boundaries').locator('tbody tr').count()==0,'never auto-create industrial truth'
                assert not errors,errors;assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                assert not any('/api/' in u or not (u.startswith(url.rsplit('/',1)[0]) or u.startswith('blob:')) for u in requests),requests
                if page.locator('video').count():page.locator('video').evaluate('v=>v.style.visibility="hidden"')
                page.locator('.lm-video-wrap').evaluate('e=>{const d=document.createElement("div");d.textContent="原片画面已从交付截图隐藏";d.style.cssText="position:absolute;inset:0;display:grid;place-items:center;background:#112a30;color:white";e.append(d)}');page.screenshot(path=str(out/(mode+'.png')),full_page=True,animations='disabled')
                records.append({'mode':mode,'passed':True,'blind_dom_contains_candidates_or_curves':False,'actual_video_playback':True,'frame_receipt_error_ms':frame_errors,'industrial_frame_receipt_error_ms':real_frame_errors,'full_tail_required':True,'append_update_delete_review':True,'cross_language_seal_and_evaluation':True,'methods_compared_on_synthetic_fixture':14,'second_rater_type_disagreement_preserved':True,'reload_preserves_sealed_revision':True,'cross_source_annotation_rejected':True,'post_exposure_draft_assisted':True,'industrial_human_boundaries_created':0,'api_or_remote_requests':0,'page_errors':errors});context.close()
            result={'passed':True,'browser':browser.version,'build_sha256':hashlib.sha256((ROOT/'apps/web/dist-demo/index.html').read_bytes()).hexdigest(),'records':records,'human_industrial_f1':None,'b2_started':False};(out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));browser.close()
    finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
