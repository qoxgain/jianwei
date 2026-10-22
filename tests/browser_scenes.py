"""Four-scene browser acceptance. Requires an actual reachable API and Chromium.

Not run by pytest. It must pass separately before P0.1.1 full acceptance.
"""
import argparse,json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]

def exercise(page,address,mode,directory):
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(address)
    expect(page.get_by_role('heading',name='从一个场景，开始看见。')).to_be_visible()
    results=[]
    for scene in ['garment_sop','electronics_assembly','workshop_safety','anonymous_observation']:
        page.get_by_label('当前场景',exact=True).select_option(scene)
        expect(page.get_by_label('当前场景',exact=True)).to_have_value(scene)
        page.get_by_role('button',name='知识工作台',exact=True).click()
        editor=page.get_by_label('知识 YAML 编辑器')
        expect(editor).to_be_visible()
        original=editor.input_value()
        assert f'scene_id: {scene}' in original
        if scene=='electronics_assembly':
            page.get_by_role('tab',name='SOP 流程',exact=True).click()
            expect(page.locator('.jw-node').filter(has_text='inspect')).to_have_count(2)
            expect(page.locator('.jw-node').filter(has_text='node_3')).to_be_visible()
            expect(page.locator('.jw-node').filter(has_text='node_5')).to_be_visible()
        if scene in {'workshop_safety','anonymous_observation'}:
            expect(page.get_by_role('tab',name='SOP 流程',exact=True)).to_have_count(0)
            expect(page.locator('.jw-extension')).to_have_count(0)
        if scene=='anonymous_observation':
            expect(page.get_by_role('heading',name='尚无已命名动作')).to_be_visible()
        # Make a real metadata edit without inventing video/model evidence.
        lines=original.splitlines()
        lines=[line+'-browser' if line.startswith('knowledge_version:') else line for line in lines]
        editor.fill('\n'.join(lines)+'\n')
        page.get_by_role('button',name='校验并预览',exact=True).click()
        expect(page.get_by_role('dialog')).to_contain_text('严格校验通过')
        page.get_by_role('button',name='保存候选版本',exact=True).click()
        expect(page.get_by_role('dialog')).not_to_be_visible()
        expect(page.get_by_text('未激活候选',exact=True).first).to_be_visible()
        page.reload()
        expect(page.get_by_label('当前场景',exact=True)).to_have_value(scene)
        page.get_by_role('button',name='知识工作台',exact=True).click()
        expect(page.get_by_label('知识 YAML 编辑器')).to_have_value(original)
        page.get_by_role('tab',name='版本记录',exact=False).click()
        expect(page.get_by_text('未激活候选',exact=True).first).to_be_visible()
        page.get_by_role('button',name='场景概览',exact=True).click()
        page.screenshot(path=str(directory/f'{mode}-{scene}.png'),full_page=True)
        results.append({'scene':scene,'edit_preview_save_refresh':True,'baseline_unchanged':True})
    assert not errors,errors
    return {'mode':mode,'scenes':results,'console_errors':errors}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8000/');parser.add_argument('--demo',default='http://127.0.0.1:8001/');parser.add_argument('--chromium',default='/usr/bin/chromium');args=parser.parse_args()
    directory=ROOT/'artifacts/browser-scenes';directory.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=args.chromium,args=['--no-sandbox'])
        results=[]
        for mode,address in [('api',args.url),('offline',args.demo)]:
            context=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN')
            try:results.append(exercise(context.new_page(),address,mode,directory))
            finally:context.close()
        browser.close()
    output=json.dumps(results,ensure_ascii=False,indent=2)+'\n';(directory/'results.json').write_text(output);print(output)
