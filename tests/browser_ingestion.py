"""Actual P0.2 browser acceptance against an API and self-contained static demo.

All video fixtures are generated test patterns, not field recordings. The API
must run with an isolated database, because this creates real import jobs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCENES = ['garment_sop', 'electronics_assembly', 'workshop_safety', 'anonymous_observation']


def navigate(page, scene):
    page.get_by_label('当前场景', exact=True).select_option(scene)
    page.get_by_role('button', name='数据接入', exact=True).click()
    expect(page.get_by_role('heading', name='让每份输入，都有来处。')).to_be_visible()


def export_manifest(page, destination):
    with page.expect_download() as pending:
        page.get_by_role('button', name='导出清单', exact=True).click()
    download = pending.value
    download.save_as(destination)
    return json.loads(destination.read_text())


def screenshot(page, destination, mobile=False):
    page.evaluate('window.scrollTo(0,0)')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'horizontal overflow'
    page.screenshot(path=str(destination), full_page=not mobile, animations='disabled')


def exercise(page, address, mode, directory, fixture):
    errors, console_errors, requests = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
    page.on('request', lambda request: requests.append((request.url, request.resource_type)))
    page.goto(address)
    expect(page.get_by_role('heading', name='从一个场景，开始看见。')).to_be_visible()
    cases = []
    for scene in SCENES:
        navigate(page, scene)
        action = '查看示例流程' if mode == 'offline' else '运行示例导入'
        page.get_by_role('button', name=action, exact=True).click()
        expect(page.locator('.ingest-job-detail h3')).to_have_text('示例流程清单' if mode == 'offline' else '导入完成', timeout=30000)
        expect(page.locator('.ingest-manifest')).to_be_visible()
        manifest = export_manifest(page, directory / f'{mode}-{scene}.manifest.json')
        assert manifest['scene_id'] == scene and manifest['data_origin'] == 'synthetic_features'
        assert manifest['total_rows'] == 120 and len(manifest['episodes']) == 2
        assert manifest['source_verification'] == 'synthetic' and not manifest['training_executed']
        job_id = page.locator('.ingest-detail-heading > div > code').inner_text()
        count = page.locator('.ingest-job').count()
        page.get_by_role('button', name=action, exact=True).click()
        expect(page.locator('.ingest-detail-heading > div > code')).to_have_text(job_id)
        expect(page.locator('.ingest-job')).to_have_count(count)
        page.reload()
        page.get_by_role('button', name='数据接入', exact=True).click()
        expect(page.locator('.ingest-manifest')).to_be_visible(timeout=30000)
        expect(page.locator('.ingest-detail-heading > div > code')).to_have_text(job_id)
        screenshot(page, directory / f'{mode}-{scene}.png')
        page.set_viewport_size({'width': 390, 'height': 844})
        screenshot(page, directory / f'{mode}-{scene}-mobile.png', mobile=True)
        page.locator('.ingest-results').screenshot(path=str(directory / f'{mode}-{scene}-mobile-results.png'), animations='disabled')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        cases.append({'scene': scene, 'example': 'passed', 'same_job_on_repeat': True,
                      'reload_and_manifest_export': True, 'desktop_and_mobile': True})
    video_result = None
    if mode == 'api':
        navigate(page, 'anonymous_observation')
        page.get_by_text('周期与采集时间（可选）', exact=True).click()
        page.get_by_label('周期边界 JSON', exact=False).fill(json.dumps([
            {'episode_id': 'browser_cycle', 'domain_index': 0, 'start_ms': 0, 'end_ms': 4000}]))
        page.get_by_label('MP4 视频文件', exact=False).set_input_files(fixture)
        page.get_by_role('button', name='上传并开始导入', exact=True).click()
        expect(page.locator('.ingest-job-detail h3')).to_have_text('等待条件', timeout=30000)
        expect(page.locator('.ingest-blocker')).to_contain_text('ENCODER_ADAPTER_UNAVAILABLE')
        video = export_manifest(page, directory / 'api-test-pattern-video.manifest.json')
        assert video['quality']['decoded_frames'] == 32 and video['status'] == 'blocked'
        assert video['source_hash'] == hashlib.sha256(fixture.read_bytes()).hexdigest()
        assert video['feature_batches'] == [] and video['feature_cache'] is None
        screenshot(page, directory / 'api-video-awaiting-weights.png')
        bad = fixture.parent / 'invalid.mp4'
        bad.write_bytes(b'not an mp4 file, deliberate browser validation fixture')
        page.get_by_label('MP4 视频文件', exact=False).set_input_files(bad)
        page.get_by_role('button', name='上传并开始导入', exact=True).click()
        expect(page.locator('.ingest-job-detail h3')).to_have_text('导入失败', timeout=30000)
        expect(page.locator('.ingest-blocker')).to_contain_text('VIDEO_DECODE_FAILED')
        screenshot(page, directory / 'api-invalid-video.png')
        video_result = {'decoded_test_pattern_frames': 32, 'missing_weights': 'explicitly_blocked',
                        'invalid_video': 'failed_with_safe_reason', 'field_data': False}
    else:
        expect(page.get_by_role('button', name='上传并开始导入', exact=True)).to_be_disabled()
        assert not [(url, kind) for url, kind in requests if url.startswith(('http:', 'https:')) and
                    (kind != 'document' or urlsplit(url)[:3] != urlsplit(address)[:3])], requests
    assert not errors, errors
    assert not console_errors, console_errors
    return {'mode': mode, 'scenes': cases, 'video': video_result, 'page_errors': errors,
            'console_errors': console_errors, 'offline_document_only': mode == 'offline'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:18021/')
    parser.add_argument('--demo', default='http://127.0.0.1:18022/')
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/delivery/p0.2-browser')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fixtures = ROOT / 'artifacts/p02-browser-work/fixtures'
    fixtures.mkdir(parents=True, exist_ok=True)
    fixture = fixtures / 'test-pattern.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=8',
                    '-t', '4', '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', '-y', str(fixture)], check=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, args=['--no-sandbox'])
        results = []
        for mode, address in [('api', args.url), ('offline', args.demo)]:
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN', accept_downloads=True)
            try:
                results.append(exercise(context.new_page(), address, mode, args.output, fixture))
            finally:
                context.close()
        browser.close()
    evidence = {'passed': True, 'browser': subprocess.check_output([args.chromium, '--version'], text=True).strip(),
                'build_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in [ROOT / 'apps/web/dist/index.html', ROOT / 'apps/web/dist-demo/index.html']},
                'results': results}
    output = json.dumps(evidence, ensure_ascii=False, indent=2) + '\n'
    (args.output / 'results.json').write_text(output)
    print(output)


if __name__ == '__main__':
    main()
