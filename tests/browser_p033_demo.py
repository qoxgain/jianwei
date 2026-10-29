"""Inspect the packaged numeric evidence UI without publishing media or screenshots."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from urllib.parse import quote
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def verify(source, output, chromium):
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN', accept_downloads=True)
        page = context.new_page()
        server = None
        file_scheme, file_blocker = True, None
        try:
            try:
                page.goto(source.as_uri())
            except Exception as error:
                if 'ERR_BLOCKED_BY_ADMINISTRATOR' not in str(error):
                    raise
                file_scheme = False
                file_blocker = 'Managed Chromium denies file URLs: ERR_BLOCKED_BY_ADMINISTRATOR; tested identical bytes over localhost HTTP'
                server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(source.parent)))
                Thread(target=server.serve_forever, daemon=True).start()
                page.close()
                page = context.new_page()
            errors, console = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: console.append(message.text) if message.type == 'error' else None)
            address = source.as_uri() if file_scheme else f'http://127.0.0.1:{server.server_port}/{quote(source.name)}'
            page.goto(address)
            expect(page.locator('#rows tr')).to_have_count(9)
            expect(page.locator('#metrics .number')).to_have_text(['9', '695', '512', '未分配'])
            page.get_by_label('选择录像').select_option('bull_station_02')
            expect(page.locator('#metadata')).to_contain_text('bull_station_02')
            assert page.locator('#bar button').count() > 0
            page.locator('#bar button').first.click()
            expect(page.locator('#detail')).to_contain_text('代表片段')
            with page.expect_download() as downloading:
                page.get_by_role('button', name='下载完整数字报告').click()
            downloading.value.save_as(output/'demo-numeric-download.json')
            exported = json.loads((output/'demo-numeric-download.json').read_text())
            assert len(exported['acceptance']['assets']) == 9
            assert sum(a['eligible_rows'] for a in exported['acceptance']['assets']) == 695
            for name, width, height in [('desktop', 1440, 1000), ('mobile', 390, 844)]:
                page.set_viewport_size({'width': width, 'height': height})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=str(output/f'numeric-demo-{name}.png'), full_page=True, animations='disabled')
            assert not errors, errors
            assert not console, console
            result = {'passed': True, 'source': str(source.relative_to(ROOT)), 'file_scheme': file_scheme,
                      'file_scheme_blocker': file_blocker, 'rows': 9, 'total_feature_rows': 695,
                      'selection': 'bull_station_02', 'segment_click': 'passed', 'json_download': 'passed',
                      'desktop_mobile_overflow': False, 'page_errors': errors, 'console_errors': console,
                      'browser': browser.version, 'screenshots_public': False}
            (output/'demo-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps(result, ensure_ascii=False))
        finally:
            browser.close()
            if server is not None:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', type=Path, default=ROOT/'docs/demo/p0.3.3/P0.3.3-验收Demo.html')
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/p033-browser')
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    args = parser.parse_args()
    verify(args.demo.resolve(), args.output.resolve(), args.chromium)
