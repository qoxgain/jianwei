"""Actual local video playback and standalone exploration acceptance."""
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'docs/delivery/p0.3.1/preview'


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)
    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    expected = json.loads((OUTPUT/'report.json').read_text())
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            context = browser.new_context(viewport={'width':1440,'height':1000}, accept_downloads=True)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
            page.goto(f'http://127.0.0.1:{server.server_port}/docs/delivery/p0.3.1/preview/index.html')
            expect(page.get_by_role('heading', name='真实视频 · 匿名探索')).to_be_visible()
            expect(page.locator('.segment')).to_have_count(len(expected['segments']))
            page.wait_for_function('document.querySelector("video").readyState >= 1')
            duration = page.locator('video').evaluate('(v)=>v.duration')
            assert abs(duration-118.5) < .1
            # Check both rejected selection and byte-verified local source fallback.
            page.locator('#video-file').set_input_files({'name':'wrong.mp4','mimeType':'video/mp4','buffer':b'wrong video'})
            expect(page.locator('#file-check')).to_contain_text('文件 SHA256 不匹配')
            page.locator('#video-file').set_input_files(ROOT/'examples/test.mp4')
            expect(page.locator('#file-check')).to_contain_text('SHA256 核验通过', timeout=30000)
            page.wait_for_function('document.querySelector("video").readyState >= 1')
            chosen = expected['segments'][-1]['representative']
            page.get_by_role('button', name='查看片段', exact=True).last.click()
            page.wait_for_function('(start)=>document.querySelector("video").currentTime>start+.1', arg=chosen['start_ms']/1000)
            page.locator('video').evaluate('(v)=>v.pause()')
            position = page.locator('video').evaluate('(v)=>v.currentTime')
            assert chosen['start_ms']/1000 <= position <= chosen['end_ms']/1000+.5
            with page.expect_download() as download:
                page.get_by_role('button', name='下载完整 JSON 报告').click()
            downloaded = Path(download.value.path()).read_bytes()
            assert json.loads(downloaded) == expected
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.screenshot(path=str(OUTPUT/'desktop.png'), full_page=True, animations='disabled')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.screenshot(path=str(OUTPUT/'mobile.png'), full_page=True, animations='disabled')
            page.reload()
            expect(page.locator('.segment')).to_have_count(len(expected['segments']))
            page.wait_for_function('document.querySelector("video").readyState >= 1')
            assert not errors, errors
            evidence={'passed':True,'browser':browser.version,'desktop':[1440,1000],'mobile':[390,844],
                'page_and_console_errors':errors,'horizontal_overflow':False,'reload_preserves_report':True,
                'source_duration_seconds':duration,'local_wrong_sha_rejected':True,'local_correct_sha_accepted':True,
                'representative_seek':{'row':chosen['row'],'start_ms':chosen['start_ms'],'actual_seconds':position},
                'video_playback_advanced':True,
                'download_matches_report':True,'download_sha256':hashlib.sha256(downloaded).hexdigest()}
            (OUTPUT/'browser.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(evidence,ensure_ascii=False))
            context.close();browser.close()
    finally:
        server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    main()
