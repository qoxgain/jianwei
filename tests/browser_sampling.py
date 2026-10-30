"""Real HTTP/browser checks using recorded timestamps, not a new video encoding.

Only numerical UI evidence is captured. No video frames, source PDF or SOP pages.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

import yaml
from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from infra.database import Database
from infra.models import ImportJob, SourceSplit
from packages.config.loader import database_url, load_bundle
from scripts.validate_video_dataset import server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium',default='/usr/bin/chromium')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/p034-browser/sampling')
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(ROOT/'artifacts'):
        parser.error('Use an isolated artifacts directory')
    out.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='runtime-',dir=out))
    for name in ('config','knowledge'):shutil.copytree(ROOT/name,work/name)
    path=work/'config/system.yaml';raw=yaml.safe_load(path.read_text())
    raw['profiles']['demo']['api_port']=18054
    path.write_text(yaml.safe_dump(raw,allow_unicode=True,sort_keys=False))
    bundle=load_bundle(path);db=Database(database_url(bundle))
    db.migrate(bundle.profile.tenant_id,bundle.config.active_scene)
    fixtures=[]
    for asset in ('garment_test','garment_test_short'):
        m=json.loads((ROOT/'docs/delivery/p0.3.3'/asset/'manifest.json').read_text())
        assert m['tenant_id']==bundle.profile.tenant_id and m['split']=='unassigned'
        fixtures.append((asset,m))
        with db.session() as session:
            session.add(ImportJob(id=asset,tenant_id=m['tenant_id'],scene_id=m['scene_id'],
                idempotency_key=m['source_hash'],config_hash=m['config_hash'],knowledge_hash=m['knowledge_hash'],
                request_json='{}',config_snapshot_json='{}',status='succeeded',manifest_json=json.dumps(m)))
            session.add(SourceSplit(id=m['source_hash'],tenant_id=m['tenant_id'],source_hash=m['source_hash'],split='unassigned'))
    db.close()
    errors=[]
    with (work/'api.log').open('w') as log:
        process,client,_=server(path,log)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=args.chromium,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN',accept_downloads=True)
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto('http://127.0.0.1:18054/')
                expect(page.get_by_label('当前场景',exact=True)).to_be_enabled()
                page.get_by_label('当前场景',exact=True).select_option('anonymous_observation')
                page.get_by_role('button',name='时序研究',exact=True).click()
                expect(page.locator('.temporal-inputs input')).to_have_count(2)
                for box in page.locator('.temporal-inputs input').all():box.check()
                page.get_by_role('button',name='检查选中来源',exact=True).click()
                expect(page.get_by_role('heading',name='采样与预测跨度',exact=True)).to_be_visible()
                expect(page.get_by_text('训练准入：条件未满足',exact=True)).to_be_visible()
                for detail in page.locator('.sampling-diagnostics summary').all():detail.click()
                expect(page.get_by_text('目标超出时间容差：37',exact=True)).to_be_visible()
                expect(page.get_by_text('当前配置的理想网格：时间约束相容。',exact=False)).to_be_visible()
                expect(page.get_by_text('计数不代表训练准入或学习效果。',exact=False)).to_have_count(2)
                with page.expect_download() as pending:
                    page.get_by_role('button',name='导出预检报告',exact=True).click()
                pending.value.save_as(out/'readiness.json')
                report=json.loads((out/'readiness.json').read_text())
                assert not report['can_submit_train'] and not report['can_submit_evaluate']
                assert sorted(r['sampling_diagnostics']['eligible_rows'] for r in report['records'])==[10,59]
                assert all(r['sampling_diagnostics']['causal_windows']==0 for r in report['records'])
                assert not client.get('/api/scenes/anonymous_observation/temporal/runs').json()['items']
                for mode,size in [('desktop',{'width':1440,'height':1000}),('mobile',{'width':390,'height':844})]:
                    page.set_viewport_size(size)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    page.locator('.readiness-panel').screenshot(path=str(out/f'{mode}.png'),animations='disabled')
                assert not errors,errors
                version=browser.version;browser.close()
        finally:
            client.close();process.terminate();process.wait(timeout=30)
    result={'passed':True,'browser':version,'scope':'Recorded metadata fixtures through real HTTP API; no new video extraction or model fitting',
            'sources':[a for a,_ in fixtures],'desktop_mobile':True,'report_export':True,
            'zero_pairs_explained':True,'training_remains_blocked':True,'jobs_created':False,'page_errors':errors}
    (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
