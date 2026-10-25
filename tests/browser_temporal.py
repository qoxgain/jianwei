"""Actual P0.3 research UI acceptance, using isolated synthetic source fixtures.

Run an API with a copied config/data root and a standalone demo HTTP server first.
The optional --isolated-data-root enables a controlled cache corruption/recovery
check: it is restricted to the browser fixture directory, never production data.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCENES = ['garment_sop', 'electronics_assembly', 'workshop_safety', 'anonymous_observation']


def navigate(page, scene):
    page.get_by_label('当前场景', exact=True).select_option(scene)
    page.get_by_role('button', name='时序研究', exact=True).click()
    expect(page.get_by_role('heading', name='先验证时序是否可学习')).to_be_visible()


def screenshot(page, path, mobile=False):
    page.evaluate('window.scrollTo(0, 0)')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), str(path)
    page.screenshot(path=str(path), full_page=not mobile, animations='disabled')


def download(page, locator, path):
    with page.expect_download() as pending:
        locator.click()
    pending.value.save_as(path)
    return path.read_bytes()


def verify_predictions(content):
    rows = [json.loads(line) for line in content.splitlines()]
    assert rows and len(rows) % 2 == 0
    for prediction, observation in zip(rows[::2], rows[1::2]):
        assert prediction['kind'] == 'empirical_forecast'
        assert observation['kind'] == 'future_feature_observation'
        assert observation['prediction_hash'] == prediction['record_hash']
        assert prediction['input_max_pts_ms'] <= prediction['issued_at_pts_ms']
        assert all(start > prediction['issued_at_pts_ms'] for start, _ in prediction['target_windows'])
    return len(rows) // 2


def wait_imports(page, address, scene):
    endpoint = address.rstrip('/') + f'/api/scenes/{scene}/temporal/inputs'
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        response = page.request.get(endpoint)
        assert response.ok, response.text()
        inputs = response.json()['items']
        if len(inputs) == 8:
            return inputs
        page.wait_for_timeout(200)
    raise TimeoutError('synthetic imports did not finish')


def exercise(page, address, mode, directory, data_root, downloads):
    errors, console_errors, requests = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
    page.on('request', lambda request: requests.append((request.url, request.resource_type)))
    page.goto(address)
    expect(page.get_by_role('heading', name='从一个场景，开始看见。')).to_be_visible()
    cases = []
    for scene in SCENES:
        navigate(page, scene)
        prefix = f'{mode}-{scene}'
        retry = None
        trained = None
        if mode == 'api':
            page.get_by_role('button', name='准备合成研究示例', exact=True).click()
            inputs = wait_imports(page, address, scene)
            expect(page.locator('.temporal-inputs input')).to_have_count(8, timeout=30000)
            cache, original = None, None
            if data_root:
                train_id = next(row['id'] for row in inputs if row['split'] == 'train')
                matches = list(data_root.glob(f'**/{train_id}/vectors.npy'))
                assert len(matches) == 1, matches
                cache = matches[0]
                original = cache.read_bytes()
                cache.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            try:
                page.get_by_role('button', name='用训练 / 校准分区建立候选', exact=True).click()
                if cache:
                    expect(page.get_by_text('TEMPORAL_CACHE_CORRUPTED', exact=True)).to_be_visible(timeout=180000)
                    screenshot(page, directory / f'{prefix}-blocked.png')
                    cache.write_bytes(original)
                    page.get_by_role('button', name='重试', exact=True).click()
                    retry = {'failure': 'TEMPORAL_CACHE_CORRUPTED', 'restored_original_cache': True, 'retry': 'passed'}
                expect(page.locator('.temporal-result')).to_be_visible(timeout=180000)
            finally:
                if cache and original is not None:
                    cache.write_bytes(original)
            trained = json.loads(download(page, page.get_by_role('button', name='导出报告', exact=True), downloads / f'{prefix}-train.json'))
            assert trained['calibration']['independent_sources'] == 2
            assert trained['used_training_sources'] == 4
            assert trained['training']['parameter_delta_l2'] > 0
            weights = download(page, page.get_by_role('link', name='下载候选权重', exact=True), downloads / f'{prefix}-weights.npz')
            assert hashlib.sha256(weights).hexdigest() == trained['model']['weights_sha256']
            records = download(page, page.get_by_role('link', name='预测与观测记录', exact=True), downloads / f'{prefix}-calibration.jsonl')
            verify_predictions(records)
            full = json.loads(download(page, page.get_by_role('link', name='完整报告', exact=True), downloads / f'{prefix}-full-train.json'))
            assert full == trained
            screenshot(page, directory / f'{prefix}-train.png')
            page.get_by_role('button', name='读取封存 test，运行独立评估', exact=True).click()
            expect(page.get_by_role('heading', name='独立测试报告', exact=True)).to_be_visible(timeout=30000)
            expect(page.locator('.temporal-result')).to_be_visible(timeout=180000)
        else:
            expect(page.get_by_text('浏览器不执行训练或测试。', exact=False)).to_be_visible()
            expect(page.get_by_role('button', name='准备合成研究示例', exact=True)).to_have_count(0)
            page.get_by_role('button', name='独立封存评估', exact=False).click()
            expect(page.locator('.temporal-result')).to_be_visible()
        report = json.loads(download(page, page.get_by_role('button', name='导出报告', exact=True), downloads / f'{prefix}-test.json'))
        assert report['test']['independent_sources'] == 2
        assert report['data_origin'] == 'synthetic_features'
        assert len(report['test']['source_macro_mse']) == 5
        if trained:
            assert report['selected_model'] == trained['model']['selected_model']
            records = download(page, page.get_by_role('link', name='预测与观测记录', exact=True), downloads / f'{prefix}-test.jsonl')
            verify_predictions(records)
        page.reload()
        page.get_by_role('button', name='时序研究', exact=True).click()
        expect(page.locator('.temporal-result')).to_be_visible(timeout=30000)
        # API selects the newest run after refresh; the standalone demo keeps its
        # supplied train/evaluation records and permits selecting either again.
        if mode == 'offline':
            page.get_by_role('button', name='独立封存评估', exact=False).click()
        expect(page.get_by_role('heading', name='独立测试报告', exact=True)).to_be_visible()
        restored = json.loads(download(page, page.get_by_role('button', name='导出报告', exact=True), downloads / f'{prefix}-reloaded.json'))
        assert restored == report
        screenshot(page, directory / f'{prefix}.png')
        page.set_viewport_size({'width': 390, 'height': 844})
        screenshot(page, directory / f'{prefix}-mobile.png', mobile=True)
        page.locator('.temporal-result').screenshot(path=str(directory / f'{prefix}-mobile-results.png'), animations='disabled')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        cases.append({'scene': scene, 'independent_train_sources': 4, 'calibration_sources': 2,
                      'test_sources': 2, 'candidate': report['selected_model'], 'retry': retry,
                      'report_export_and_reload': True, 'desktop_and_mobile': True,
                      'numeric_weights_and_prediction_downloads': mode == 'api'})
        print(f'{mode}/{scene}: passed', flush=True)
    assert not errors, errors
    assert not console_errors, console_errors
    if mode == 'offline':
        assert not [(url, kind) for url, kind in requests if url.startswith(('http:', 'https:')) and
                    (kind != 'document' or urlsplit(url)[:3] != urlsplit(address)[:3])], requests
    return {'mode': mode, 'cases': cases, 'page_errors': errors, 'console_errors': console_errors,
            'offline_document_only': mode == 'offline'}


def isolated_servers(work, api_address, demo_address):
    """Keep servers alive as children of the browser run, then terminate exactly them."""
    import yaml
    for name in ('config', 'knowledge'):
        shutil.copytree(ROOT / name, work / name)
    config_path = work / 'config/system.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['profiles']['demo']['api_host'] = '127.0.0.1'
    config['profiles']['demo']['api_port'] = urlsplit(api_address).port
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    api_log = (work / 'api.log').open('w')
    static_log = (work / 'static.log').open('w')
    processes = [
        subprocess.Popen([str(ROOT / '.venv/bin/python'), '-m', 'jianwei', 'serve', '--config', str(config_path), '--profile', 'demo'],
                         cwd=ROOT, stdout=api_log, stderr=subprocess.STDOUT),
        subprocess.Popen(['python', '-m', 'http.server', str(urlsplit(demo_address).port), '--bind', '127.0.0.1',
                          '--directory', str(ROOT / 'apps/web/dist-demo')], stdout=static_log, stderr=subprocess.STDOUT),
    ]
    try:
        for address in (api_address, demo_address):
            deadline = time.monotonic() + 30
            while True:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError(f'Browser fixture service exited; inspect {work}')
                try:
                    with socket.create_connection(('127.0.0.1', urlsplit(address).port), timeout=.2):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(address)
                    time.sleep(.1)
        return processes, [api_log, static_log]
    except BaseException:
        for process in processes:
            process.terminate()
            process.wait(timeout=30)
        api_log.close(); static_log.close()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:18031/')
    parser.add_argument('--demo', default='http://127.0.0.1:18032/')
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/delivery/p0.3.1-browser')
    parser.add_argument('--isolated-data-root', type=Path)
    parser.add_argument('--serve-isolated', action='store_true', help='Start fresh local fixture servers for this run')
    args = parser.parse_args()
    if args.isolated_data_root:
        args.isolated_data_root = args.isolated_data_root.resolve()
        assert args.isolated_data_root.is_relative_to(ROOT / 'artifacts/p031-browser-work'), 'Use only the isolated browser fixture data'
    args.output.mkdir(parents=True, exist_ok=True)
    work_root = ROOT / 'artifacts/p031-browser-work'
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=work_root))
    downloads = work / 'downloads'
    downloads.mkdir()
    processes, logs = [], []
    try:
        if args.serve_isolated:
            assert not args.isolated_data_root, 'Choose existing services or a new isolated run'
            assert all(urlsplit(address).hostname in {'127.0.0.1', 'localhost'} for address in (args.url, args.demo))
            processes, logs = isolated_servers(work, args.url, args.demo)
            args.isolated_data_root = work / 'data_demo'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.chromium, args=['--no-sandbox'])
            results = []
            for mode, address in [('api', args.url), ('offline', args.demo)]:
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN', accept_downloads=True)
                try:
                    results.append(exercise(context.new_page(), address, mode, args.output, args.isolated_data_root, downloads))
                finally:
                    context.close()
            browser.close()
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=30)
        for log in logs:
            log.close()
    evidence = {'passed': True, 'scope': 'Eight independent synthetic sources reused across scenes; no field accuracy claim.',
                'browser': subprocess.check_output([args.chromium, '--version'], text=True).strip(),
                'build_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in [ROOT / 'apps/web/dist/index.html', ROOT / 'apps/web/dist-demo/index.html']},
                'downloads': {path.name: {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
                              for path in sorted(downloads.iterdir())}, 'results': results}
    output = json.dumps(evidence, ensure_ascii=False, indent=2) + '\n'
    (args.output / 'results.json').write_text(output)
    print(output)


if __name__ == '__main__':
    main()
