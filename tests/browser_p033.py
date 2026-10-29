"""Real P0.3.3 browser acceptance; all captured frames stay in ignored artifacts.

Runs an isolated API with original registered assets and the explicit frozen
encoder acceptance config. It never alters original assets or publishes media.
Use the global Python with Playwright; the server uses the project's .venv.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlparse
import uuid
import yaml
from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]
SCENES=['garment_sop','electronics_assembly','workshop_safety','anonymous_observation']


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def configuration(directory,port):
    origin=ROOT/'artifacts/p033-runtime'
    config=yaml.safe_load((origin/'config/system.yaml').read_text())
    runtime=directory/'runtime'/uuid.uuid4().hex[:12]
    (runtime/'config').mkdir(parents=True,exist_ok=True)
    shutil.copytree(ROOT/'knowledge',runtime/'knowledge',dirs_exist_ok=True)
    config['sop_review']['asset_root']=str(ROOT)
    config['profiles']['demo']['encoder_weights']=str(ROOT/'artifacts/models/r3d18-kinetics400-v1.onnx')
    config['profiles']['demo']['api_port']=port
    config['profiles']['demo']['tenant_id']='p033_browser_fixture'
    for source in config['profiles']['demo']['sources']:
        if source['type']=='file':source['uri']=str((origin/source['uri']).resolve())
    path=runtime/'config/system.yaml';path.write_text(yaml.safe_dump(config,sort_keys=False,allow_unicode=True))
    return path


def navigate(page,name):
    # Reload returns before React bootstrap finishes. Wait for the actual shell
    # before deciding whether the mobile drawer needs opening.
    expect(page.get_by_label('当前场景',exact=True)).to_be_visible()
    expect(page.get_by_label('当前场景',exact=True)).to_be_enabled()
    toggle=page.get_by_role('button',name='打开导航',exact=True)
    button=page.get_by_role('button',name=name,exact=True)
    if not button.is_visible():toggle.click()
    button.click()


def scene(page,identifier):
    page.get_by_label('当前场景',exact=True).select_option(identifier)
    expect(page.get_by_label('当前场景',exact=True)).to_have_value(identifier)
    expect(page.get_by_label('当前场景',exact=True)).to_be_enabled()


def screenshot(page,path,full_page=False):
    page.evaluate('scrollTo(0,0)')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), 'horizontal overflow'
    page.screenshot(path=str(path),full_page=full_page,animations='disabled')


def run(page,address,directory,phase):
    stamp=uuid.uuid4().hex[:8]
    errors=[];console_errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('console',lambda message:console_errors.append(message.text) if message.type=='error' else None)
    # Observe the actual XHR response in-page: Chromium may evict inspector
    # bodies for a 156 MiB request even when the UI consumed the small response.
    page.add_init_script("""(() => {const open=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(method,url,...args){if(String(url).includes('/api/uploads?'))this.addEventListener('load',()=>{if(this.status===201)window.__verifiedUpload=this.responseType==='json'?this.response:JSON.parse(this.responseText);});return open.call(this,method,url,...args);};})();""")
    page.goto(address)
    expect(page.get_by_role('heading',name='从一个场景，开始看见。')).to_be_visible()
    result={'desktop':[1440,1000],'mobile':[390,844],'scope':'Isolated browser fixture database. Test reviewers do not certify source SOP or operations.'}
    cases=[]
    for identifier in SCENES:
        scene(page,identifier);navigate(page,'知识工作台')
        editor=page.get_by_label('知识 YAML 编辑器');expect(editor).to_be_visible()
        assert f'scene_id: {identifier}' in editor.input_value()
        if identifier=='anonymous_observation':expect(page.get_by_role('heading',name='尚无已命名动作')).to_be_visible()
        if identifier in {'anonymous_observation','workshop_safety'}:expect(page.get_by_role('tab',name='SOP 流程',exact=True)).to_have_count(0)
        screenshot(page,directory/f'{identifier}-desktop.png')
        page.set_viewport_size({'width':390,'height':844});screenshot(page,directory/f'{identifier}-mobile.png')
        page.set_viewport_size({'width':1440,'height':1000});cases.append({'scene':identifier,'knowledge_and_no_label_scope':'passed','desktop_mobile_overflow':False})
    result['scenes']=cases
    catalog={a['asset_id']:a for a in json.loads((ROOT/'datasets/video_samples/catalog.json').read_text())['assets']}
    scene(page,'anonymous_observation');navigate(page,'数据接入')
    expect(page.get_by_text('单文件上限 512 MiB',exact=True)).to_be_visible()
    large=catalog['bull_station_02'];large_path=ROOT/large['path']
    page.get_by_label('文件来源',exact=True).select_option('bull_station_02')
    page.get_by_label('MP4 视频文件',exact=False).set_input_files(large_path)
    cdp=page.context.new_cdp_session(page)
    cdp.send('Network.enable')
    cdp.send('Network.emulateNetworkConditions',{'offline':False,'latency':0,'downloadThroughput':-1,'uploadThroughput':1048576})
    page.get_by_role('button',name='上传并开始导入',exact=True).click()
    expect(page.get_by_role('progressbar',name='上传进度')).to_be_visible()
    page.wait_for_function('document.querySelector(".ingest-transfer progress")?.value>0',timeout=20000)
    screenshot(page,directory/'large-upload-progress.png')
    page.get_by_role('button',name='取消上传',exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('上传已取消')
    cdp.send('Network.emulateNetworkConditions',{'offline':False,'latency':0,'downloadThroughput':-1,'uploadThroughput':-1})
    with page.expect_response(lambda response:'/api/uploads?kind=mp4' in response.url and response.request.method=='POST',timeout=90000) as uploaded:
        page.get_by_role('button',name='上传并开始导入',exact=True).click()
    assert uploaded.value.status==201
    page.wait_for_function('window.__verifiedUpload?.sha256')
    upload=page.evaluate('window.__verifiedUpload');assert upload['sha256']==large['sha256'] and upload['size_bytes']==large['bytes']
    expect(page.get_by_role('button',name='取消导入任务',exact=True)).to_be_visible(timeout=30000)
    job_id=page.locator('.ingest-detail-heading > div > code').inner_text()
    page.get_by_role('button',name='取消导入任务',exact=True).click()
    expect(page.locator('.ingest-blocker')).to_contain_text('IMPORT_CANCELLED',timeout=30000)
    page.reload();navigate(page,'数据接入')
    expect(page.locator('.ingest-blocker')).to_contain_text('IMPORT_CANCELLED',timeout=30000)
    expect(page.locator('.ingest-detail-heading > div > code')).to_have_text(job_id)
    screenshot(page,directory/'large-import-cancelled.png')
    page.get_by_role('button',name='重试导入',exact=True).click()
    expect(page.get_by_role('button',name='取消导入任务',exact=True)).to_be_visible(timeout=30000)
    page.get_by_role('button',name='取消导入任务',exact=True).click()
    expect(page.locator('.ingest-blocker')).to_contain_text('IMPORT_CANCELLED',timeout=30000)
    result['large_upload']={'asset_id':large['asset_id'],'bytes':upload['size_bytes'],'sha256':upload['sha256'],'stream_progress_visible':True,'upload_cancelled':True,'import_cancelled_and_persisted':True,'retry_same_job':True,'job_id':job_id}
    short=catalog['garment_test_short']
    page.get_by_label('文件来源',exact=True).select_option('garment_test_short')
    page.get_by_label('MP4 视频文件',exact=False).set_input_files(ROOT/short['path'])
    page.get_by_role('button',name='上传并开始导入',exact=True).click()
    expect(page.locator('.ingest-detail-heading h3')).to_have_text('导入完成',timeout=300000)
    expect(page.locator('.ingest-manifest')).to_contain_text('观察窗口 · 非业务周期')
    expect(page.locator('.ingest-manifest')).to_contain_text('可提特征')
    with page.expect_download() as downloading:page.get_by_role('button',name='导出清单',exact=True).click()
    downloading.value.save_as(directory/'short-video.manifest.json')
    manifest=json.loads((directory/'short-video.manifest.json').read_text())
    assert manifest['source_hash']==short['sha256'] and manifest['eligible_rows']>0 and manifest['split']=='unassigned'
    assert len(manifest['episodes'])==1 and manifest['episodes'][0]['end_ms']==20440
    assert manifest['episodes'][0]['complete'] is False and manifest['episodes'][0]['business_cycle_verified'] is False
    assert manifest['encoder_evidence']['execution']=='frozen_onnx_cpu'
    screenshot(page,directory/'short-video-desktop.png',True)
    page.set_viewport_size({'width':390,'height':844});screenshot(page,directory/'short-video-mobile.png')
    page.locator('.ingest-results').screenshot(path=str(directory/'short-video-mobile-result.png'),animations='disabled')
    page.set_viewport_size({'width':1440,'height':1000})
    result['short_video']={'source_sha256':manifest['source_hash'],'eligible_rows':manifest['eligible_rows'],'episode':manifest['episodes'][0],'encoder_evidence':manifest['encoder_evidence'],'desktop_mobile':'passed'}
    page.get_by_role('button',name='查看匿名探索',exact=True).click()
    expect(page.locator('.import-exploration .ingest-metrics')).to_be_visible(timeout=60000)
    page.wait_for_function('document.querySelector(".import-exploration video")?.videoWidth>0',timeout=660000)
    with page.expect_download() as downloading:page.get_by_role('button',name='下载探索报告',exact=True).click()
    downloading.value.save_as(directory/'short-video.exploration.json')
    exploration=json.loads((directory/'short-video.exploration.json').read_text())
    assert exploration['source_hash']==short['sha256'] and exploration['eligible_rows']==manifest['eligible_rows']
    assert exploration['feature_space']['dimension']==512 and exploration['training_executed'] is False
    page.get_by_role('button',name='查看代表片段',exact=True).first.click()
    target=exploration['segments'][0]['representative']['start_ms']/1000
    page.wait_for_function('(at)=>Math.abs(document.querySelector(".import-exploration video").currentTime-at)<1',arg=target)
    page.locator('.import-exploration video').evaluate('(v)=>v.pause()')
    screenshot(page,directory/'anonymous-exploration-desktop.png',True)
    page.set_viewport_size({'width':390,'height':844});screenshot(page,directory/'anonymous-exploration-mobile.png')
    page.locator('.import-exploration').screenshot(path=str(directory/'anonymous-exploration-mobile-result.png'),animations='disabled')
    page.set_viewport_size({'width':1440,'height':1000})
    result['exploration']={'source_sha256':exploration['source_hash'],'coverage':exploration['coverage']['fraction'],'anonymous_segments':len(exploration['segments']),'dimension':512,'representative_seek':'passed','training_executed':False}
    if phase=='ingestion':
        result['page_errors']=errors;result['console_errors']=console_errors
        assert not errors, errors
        return result
    scene(page,'electronics_assembly');navigate(page,'SOP 人工复核')
    expect(page.get_by_role('heading',name='从原文核对，保留每次判断')).to_be_visible()
    expect(page.get_by_label('工位录像',exact=True)).to_be_visible(timeout=30000)
    endpoint=address.rstrip('/')+'/api/scenes/electronics_assembly/sop'
    initial=page.request.get(endpoint).json()
    assert len(initial['sections'])==11 and len(initial['mappings'])==7 and initial['source']['pdf_pages']==26
    assert initial['source']['sha256']==catalog['bull_sop']['sha256']
    page.get_by_label('工位录像',exact=True).select_option('bull_station_02')
    expect(page.locator('.sop-media')).to_contain_text('岗2.mp4')
    # A loaded audio track with videoWidth == 0 is explicitly not a passing preview.
    page.wait_for_function('document.querySelector(".sop-media video").videoWidth>0',timeout=660000)
    dims=page.locator('.sop-media video').evaluate('(v)=>({width:v.videoWidth,height:v.videoHeight,duration:v.duration,src:v.currentSrc})')
    assert (dims['width'],dims['height'])==(360,640),dims
    page.get_by_label('跳转到（秒）',exact=True).fill('10')
    page.get_by_role('button',name='定位画面',exact=True).click()
    page.wait_for_function('Math.abs(document.querySelector(".sop-media video").currentTime-10)<.2',timeout=30000)
    page.locator('.sop-media video').evaluate('(v)=>v.play()')
    page.wait_for_function('document.querySelector(".sop-media video").currentTime>10.2',timeout=30000)
    page.locator('.sop-media video').evaluate('(v)=>v.pause()')
    position=page.locator('.sop-media video').evaluate('(v)=>v.currentTime')
    screenshot(page,directory/'sop-portrait-desktop.png')
    page.set_viewport_size({'width':390,'height':844});screenshot(page,directory/'sop-portrait-mobile.png')
    page.locator('.sop-media').screenshot(path=str(directory/'sop-portrait-mobile-video.png'),animations='disabled')
    page.set_viewport_size({'width':1440,'height':1000})
    pdf=page.request.get(address.rstrip('/')+initial['source']['media_url'],headers={'Range':'bytes=0-127'})
    assert pdf.status==206 and pdf.headers['content-type'].startswith('application/pdf') and pdf.body().startswith(b'%PDF')
    links=page.locator('.sop-document a[href*="#page="]');assert links.count()>0
    assert '#page=' in links.first.get_attribute('href')
    page.get_by_label('审核人 ID',exact=True).fill('browser_fixture')
    page.get_by_label('本次理由',exact=True).fill('自动浏览器隔离测试：仅验证审批界面，不确认实际工艺或真值 '+stamp)
    if initial['disputes']:
        page.get_by_role('button',name='批准当前映射',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('争议')
    page.get_by_label('该工位映射备注',exact=True).fill('自动浏览器隔离测试映射 '+stamp)
    boxes=page.locator('.sop-mapping input[type=checkbox]');boxes.first.check();boxes.nth(1).check()
    page.get_by_role('button',name='保存新草稿',exact=True).click()
    expect(page.get_by_role('status').filter(has_text='已保存新草稿')).to_be_visible(timeout=30000)
    current=page.request.get(endpoint).json();assert current['revision']!=initial['revision']
    stale=page.request.post(endpoint+'/revisions',data={'base_revision':initial['revision'],'reviewer':'browser_fixture','reason':'stale fixture','mappings':initial['mappings'],'disputes':initial['disputes']})
    assert stale.status==409 and stale.json()['detail']['code']=='BASE_HASH_CONFLICT'
    page.get_by_label('争议与待核实事项（每行一项）',exact=True).fill('')
    page.get_by_role('button',name='保存新草稿',exact=True).click()
    expect(page.get_by_role('status').filter(has_text='已保存新草稿')).to_be_visible(timeout=30000)
    page.get_by_role('button',name='批准当前映射',exact=True).click()
    expect(page.locator('.sop-version')).to_contain_text('已人工批准')
    expect(page.locator('.sop-version')).to_contain_text('未激活 · 非视频真值')
    page.get_by_role('button',name='撤销批准',exact=True).click()
    expect(page.locator('.sop-version')).to_contain_text('已撤销')
    page.reload();navigate(page,'SOP 人工复核');expect(page.locator('.sop-version')).to_contain_text('已撤销')
    page.get_by_label('审核人 ID',exact=True).fill('browser_fixture')
    page.get_by_label('本次理由',exact=True).fill('自动浏览器隔离测试：证据类别分离，不确认实际动作')
    current=page.request.get(endpoint).json()
    instrument=next(s for s in current['sections'] if s['evidence_classification']['instrument_required'])
    page.get_by_label('章节原文',exact=True).select_option(instrument['document_id'])
    page.get_by_label('证据开始（秒）',exact=True).fill('0');page.get_by_label('证据结束（秒）',exact=True).fill('1')
    for kind,label in [('observation','实际观测'),('prediction','模型预测'),('normative_expectation','规范期望'),('human_annotation','人工标注'),('semantic_candidate','语义候选')]:
        page.get_by_label('记录种类',exact=True).select_option(kind)
        page.get_by_label('证据说明',exact=True).fill('自动浏览器隔离测试记录 '+kind+'，不代表真实视频结论')
        page.get_by_role('button',name='记录为'+label,exact=True).click()
        expect(page.get_by_role('status').filter(has_text='证据已独立记录')).to_be_visible(timeout=30000)
    final=page.request.get(endpoint).json()
    evidence=final['evidence']
    assert {e['kind'] for e in evidence}=={'observation','prediction','normative_expectation','human_annotation','semantic_candidate'}
    assert all(e['result']=='insufficient_evidence' and e['compliance_result']=='not_evaluated' for e in evidence)
    assert final['activated'] is False and final['is_ground_truth'] is False
    save(directory/'sop-review.json',final)
    screenshot(page,directory/'sop-reviewed-desktop.png')
    page.set_viewport_size({'width':390,'height':844});screenshot(page,directory/'sop-reviewed-mobile.png');page.set_viewport_size({'width':1440,'height':1000})
    result['sop']={'chapters':11,'mapped_assets':7,'pdf_pages':26,'source_sha256':final['source']['sha256'],'video_display':dims,'seek_seconds':position,'raw_pdf_range':'passed','raw_pdf_page_link':'passed','mapping_revision_saved':True,'stale_revision_rejected':True,'pending_disputes_block_approval':True,'approve_revoke_reload':'passed','evidence_kinds_separate':True,'missing_instrument_result':'insufficient_evidence','activated':False,'is_ground_truth':False,'audit_events':len(final['audit'])}
    assert not errors,errors
    result['page_errors']=errors
    # Intentional negative requests may log their HTTP status in Chromium console.
    result['console_errors']=console_errors
    result['expected_console_statuses']=[409,422]
    assert all('409' in value or '422' in value for value in console_errors),console_errors
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'artifacts/p033-browser');parser.add_argument('--port',type=int,default=18054);parser.add_argument('--chromium',default='/usr/bin/chromium');parser.add_argument('--phase',choices=['all','ingestion'],default='all');args=parser.parse_args()
    directory=args.output.resolve();directory.mkdir(parents=True,exist_ok=True)
    config=configuration(directory,args.port);address=f'http://127.0.0.1:{args.port}/'
    with (directory/'api.log').open('w') as log:
        server=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-c',"import sys,uvicorn;from apps.api.main import create_app;uvicorn.run(create_app(sys.argv[1]),host='127.0.0.1',port=int(sys.argv[2]),log_level='warning')",str(config),str(args.port)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=args.chromium,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN',accept_downloads=True)
                context.set_default_timeout(30000)
                page=context.new_page()
                for _ in range(150):
                    if server.poll() is not None:raise RuntimeError('isolated API startup failed')
                    try:
                        if page.request.get(address+'api/health',timeout=1000).status==200:break
                    except Exception:pass
                    time.sleep(.2)
                else:raise TimeoutError('isolated API startup deadline')
                result=run(page,address,directory,args.phase)
                result.update(passed=True,phase=args.phase,browser=browser.version,build_sha256=hashlib.sha256((ROOT/'apps/web/dist/index.html').read_bytes()).hexdigest(),config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),screenshots_public=False)
                save(directory/('results.json' if args.phase=='all' else 'ingestion-results.json'),result);print(json.dumps(result,ensure_ascii=False,indent=2))
                context.close();browser.close()
        finally:
            server.terminate()
            try:server.wait(timeout=20)
            except subprocess.TimeoutExpired:server.kill();server.wait()


if __name__=='__main__':main()
