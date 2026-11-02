"""P0.3.5 browser checks against an isolated API and explicit synthetic fixtures.

No real video, PDF, capture identity or effect conclusion is invented. Screenshots
contain only configuration, numeric test results and ordinary application UI.
"""
from __future__ import annotations
import argparse
import hashlib
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from threading import Thread

import yaml
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_video_dataset import server

SCENES = ['garment_sop', 'electronics_assembly', 'workshop_safety', 'anonymous_observation']


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')


def configuration(out, minimum_sessions, port):
    work = Path(tempfile.mkdtemp(prefix=f'minimum-{minimum_sessions}-' if minimum_sessions else 'missing-', dir=out))
    for name in ('config', 'knowledge'):
        shutil.copytree(ROOT/name, work/name)
    path = work/'config/system.yaml'
    raw = yaml.safe_load(path.read_text())
    raw['profiles']['demo']['api_port'] = port
    raw['profiles']['demo']['tenant_id'] = 'p035_browser_fixture'
    raw['temporal'].pop('research_protocol', None)
    if minimum_sessions:
        raw['temporal']['research_protocol'] = {
            'protocol_id': 'browser_mechanism_fixture',
            'registered_by': 'browser_fixture',
            'registered_at': '2026-10-30T08:00:00+08:00',
            'primary_metric': 'source_macro_mse',
            'min_independent_sessions': minimum_sessions,
            'confidence_level': 0.95,
            'bootstrap_replicates': 200,
            'bootstrap_seed': 23,
            'minimum_relative_improvement': 0.05,
            'controls': ['persistence', 'shuffled_ridge'],
        }
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    return path


def navigate(page, scene):
    expect(page.get_by_label('当前场景', exact=True)).to_be_enabled()
    page.get_by_label('当前场景', exact=True).select_option(scene)
    expect(page.get_by_label('当前场景', exact=True)).to_have_value(scene)
    button = page.get_by_role('button', name='时序研究', exact=True)
    if page.viewport_size['width'] < 700 and not button.is_visible():
        page.get_by_role('button', name='打开导航', exact=True).click()
    button.click()
    expect(page.get_by_role('heading', name='先验证时序是否可学习', exact=True)).to_be_visible()


def screenshots(page, section, out, prefix):
    for label, size in [('desktop', {'width': 1440, 'height': 1000}), ('mobile', {'width': 390, 'height': 844})]:
        page.set_viewport_size(size)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), prefix+' horizontal overflow'
        section.screenshot(path=str(out/f'{prefix}-{label}.png'), animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 1000})


def export_json(page, button, target):
    with page.expect_download() as pending:
        button.click()
    pending.value.save_as(target)
    return json.loads(target.read_text())


def wait_job(client, scene, identifier):
    deadline = time.monotonic()+180
    while time.monotonic() < deadline:
        response = client.get(f'/api/scenes/{scene}/temporal/runs/{identifier}')
        response.raise_for_status()
        job = response.json()
        if job['status'] not in {'queued', 'running'}:
            assert job['status'] == 'succeeded', job
            return job
        time.sleep(.2)
    raise TimeoutError('Synthetic browser mechanism job did not finish')


def missing_protocol(page, client, address, out):
    page.goto(address)
    cases = []
    for scene in SCENES:
        navigate(page, scene)
        panel = page.locator('.research-protocol-panel')
        expect(panel).to_be_visible()
        expect(panel.get_by_role('button', name='导出研究协议', exact=True)).to_be_enabled()
        data = export_json(page, panel.get_by_role('button', name='导出研究协议', exact=True), out/f'{scene}-missing-protocol.json')
        assert data['scene_id'] == scene
        assert data.get('protocol') is None, data
        assert data['metadata_only'] is True, data
        assert data['training_executed'] is False and data['test_values_read'] is False, data
        assert client.get(f'/api/scenes/{scene}/temporal/runs').json()['items'] == []
        expect(panel).to_contain_text('待登记')
        screenshots(page, panel, out, scene+'-missing-protocol')
        cases.append({'scene': scene, 'missing_protocol': True, 'read_only': True, 'download': True, 'desktop_mobile': True})
    # Fast scene changes must leave only the final scene's protocol response visible.
    chooser = page.get_by_label('当前场景', exact=True)
    chooser.select_option('garment_sop')
    chooser.select_option('workshop_safety')
    chooser.select_option('anonymous_observation')
    panel = page.locator('.research-protocol-panel')
    data = export_json(page, panel.get_by_role('button', name='导出研究协议', exact=True), out/'rapid-scene-protocol.json')
    assert data['scene_id'] == 'anonymous_observation', data
    return cases


def configured_protocol(page, client, address, out, minimum_sessions):
    scene = 'anonymous_observation'
    page.goto(address)
    navigate(page, scene)
    panel = page.locator('.research-protocol-panel')
    expect(panel.get_by_role('button', name='导出研究协议', exact=True)).to_be_enabled()
    prefix = f'minimum-{minimum_sessions}'
    preview = export_json(page, panel.get_by_role('button', name='导出研究协议', exact=True), out/f'{prefix}-configured-protocol.json')
    assert preview['protocol']['protocol_id'] == 'browser_mechanism_fixture'
    assert preview['protocol']['min_independent_sessions'] == minimum_sessions
    assert preview['metadata_only'] and not preview['training_executed'] and not preview['test_values_read']
    expect(panel).to_contain_text('browser_mechanism_fixture')
    screenshots(page, panel, out, prefix+'-configured-protocol')
    # Create synthetic features explicitly through the existing fixture route. This
    # is a UI/mechanism check, not an experiment on real independent recordings.
    response = client.post(f'/api/scenes/{scene}/temporal/example')
    response.raise_for_status();result = response.json()
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        rows = client.get(f'/api/scenes/{scene}/temporal/inputs').json()['items']
        if len(rows) == 8:
            break
        time.sleep(.2)
    else:
        raise TimeoutError('Synthetic feature fixtures did not finish')
    page.reload();navigate(page, scene)
    expect(page.locator('.temporal-inputs input')).to_have_count(8, timeout=30000)
    for checkbox in page.locator('.temporal-inputs input').all():
        checkbox.check()
    page.get_by_role('button', name='检查选中来源', exact=True).click()
    expect(page.locator('.readiness-result')).to_contain_text('封存测试窗口未检查')
    precheck = export_json(page, page.get_by_role('button', name='导出预检报告', exact=True), out/f'{prefix}-before-training-readiness.json')
    assert precheck['evaluation']['windows_checked'] is False
    assert precheck['partitions']['test']['causal_windows'] is None
    assert not client.get(f'/api/scenes/{scene}/temporal/runs').json()['items']
    response = client.post(f'/api/scenes/{scene}/temporal/train', json={'import_ids': result['train_import_ids']})
    response.raise_for_status()
    job = wait_job(client, scene, response.json()['id'])
    report = job['report']
    assert report['data_origin'] == 'synthetic_features' and report['auto_activated'] is False
    stats = report['session_statistics']
    expected_status = 'estimated' if minimum_sessions == 2 else 'insufficient_sessions'
    assert stats['uncertainty']['status'] == expected_status, stats
    assert stats['gate']['status'] == 'mechanism_only' and not stats['gate']['real_effect_pass']
    save(out/f'{prefix}-synthetic-calibration-report.json', report)
    page.reload()
    navigate(page, scene)
    expect(page.locator('.temporal-result')).to_be_visible(timeout=30000)
    statistics = page.locator('.session-statistics')
    expect(statistics).to_be_visible()
    expect(statistics).to_contain_text('机制')
    if minimum_sessions > 2:
        expect(statistics).to_contain_text('不足')
    else:
        expect(statistics).to_contain_text('95.0% 区间')
    # Changing the metric or display model must not change the candidate selected
    # in calibration. Exercise every horizon metric rather than only screenshots.
    for metric, label in [('original_feature_cosine','原空间余弦'),('anonymous_state_nll','匿名状态 NLL'),('anonymous_state_brier','匿名状态 Brier'),('normalized_feature_mse','MSE')]:
        statistics.get_by_label('查看指标', exact=True).select_option(metric)
        expect(statistics.locator('caption').last).to_contain_text(label)
    statistics.get_by_label('查看模型指标', exact=True).select_option('persistence')
    expect(statistics.locator('caption').first).to_contain_text('固定候选')
    screenshots(page, statistics, out, prefix+'-synthetic-calibration-statistics')
    with page.expect_download() as pending:
        page.get_by_role('button', name='导出报告', exact=True).click()
    pending.value.save_as(out/f'{prefix}-browser-calibration-report.json')
    assert json.loads((out/f'{prefix}-browser-calibration-report.json').read_text()) == report
    jobs = [job]
    if minimum_sessions == 2:
        response = client.post(f'/api/scenes/{scene}/temporal/evaluate', json={
            'model_run_id': job['id'], 'import_ids': result['test_import_ids'], 'sealed_test': True})
        response.raise_for_status();evaluated = wait_job(client, scene, response.json()['id'])
        final = evaluated['report'];stats = final['session_statistics']
        assert final['selected_model'] == report['model']['selected_model']
        assert stats['uncertainty']['status'] == 'estimated'
        assert stats['gate']['status'] == 'mechanism_only' and not stats['gate']['real_effect_pass']
        assert all(value['confidence_interval'] is not None for value in stats['comparisons'].values())
        save(out/f'{prefix}-synthetic-test-report.json', final)
        page.reload();navigate(page, scene)
        expect(page.get_by_role('heading', name='独立测试报告', exact=True)).to_be_visible(timeout=30000)
        statistics = page.locator('.session-statistics')
        expect(statistics).to_contain_text('合成机制验证 · 真实效果未验收')
        expect(statistics).to_contain_text('已核验真实采集会话 0 个')
        screenshots(page, statistics, out, prefix+'-synthetic-test-statistics')
        downloaded = export_json(page, page.get_by_role('button', name='导出报告', exact=True), out/f'{prefix}-browser-test-report.json')
        assert downloaded == final
        jobs.append(evaluated)
    save(out/f'{prefix}-synthetic-jobs.json', jobs)
    return {'protocol_id': 'browser_mechanism_fixture', 'minimum_sessions': minimum_sessions,
            'source': 'synthetic_features', 'calibration_sources': report['calibration']['independent_sources'],
            'uncertainty_status': expected_status, 'sealed_synthetic_evaluation': minimum_sessions == 2,
            'metric_and_model_display_switching': True,
            'report_export': True, 'desktop_mobile': True, 'training_scope': 'explicit synthetic mechanism fixture only',
            'real_effect_verified': False, 'auto_activated': False}


def offline_protocol(browser, out):
    """Inspect the built standalone bytes without an API or remote requests."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT/'apps/web/dist-demo'))
    static = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = Thread(target=static.serve_forever, daemon=True)
    thread.start()
    address = f'http://127.0.0.1:{static.server_port}/index.html'
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN', accept_downloads=True)
    page = context.new_page();requests, errors = [], []
    page.on('request', lambda request: requests.append(request.url))
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(address)
        navigate(page, 'anonymous_observation')
        panel = page.locator('.research-protocol-panel')
        expect(panel).to_contain_text('离线未核验')
        expect(panel.get_by_role('button', name='导出研究协议', exact=True)).to_be_disabled()
        page.get_by_role('button', name='独立封存评估', exact=False).first.click()
        statistics = page.locator('.session-statistics')
        expect(statistics).to_contain_text('合成机制验证 · 真实效果未验收')
        expect(statistics).to_contain_text('已核验真实采集会话 0 个')
        statistics.get_by_label('查看指标', exact=True).select_option('anonymous_state_brier')
        expect(statistics.locator('caption').last).to_contain_text('匿名状态 Brier')
        screenshots(page, statistics, out, 'offline-synthetic-test-statistics')
        actual = export_json(page, page.get_by_role('button', name='导出报告', exact=True), out/'offline-synthetic-test-report.json')
        recorded = json.loads((ROOT/'apps/web/src/researchExample.json').read_text())
        expected = next(row['report'] for row in recorded['scenes']['anonymous_observation'] if row['kind'] == 'evaluate')
        assert actual == expected
        assert actual['session_statistics']['uncertainty']['status'] == 'estimated'
        page.get_by_role('button', name='时序训练与校准', exact=False).first.click()
        expect(page.get_by_role('heading', name='候选研究报告', exact=True)).to_be_visible()
        expect(statistics).to_contain_text('合成机制验证 · 真实效果未验收')
        assert not errors, errors
        assert not [url for url in requests if url.startswith(('http:', 'https:')) and url != address], requests
        return {'passed': True, 'source': 'committed executed synthetic mechanism fixture',
                'desktop_mobile': True, 'metric_switching': True, 'exact_report_export': True,
                'api_or_remote_requests': 0, 'static_document_requests': len(requests),
                'real_effect_verified': False, 'page_errors': errors,
                'demo_build_sha256': hashlib.sha256((ROOT/'apps/web/dist-demo/index.html').read_bytes()).hexdigest()}
    finally:
        context.close()
        static.shutdown();static.server_close();thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/p035-browser')
    parser.add_argument('--port', type=int, default=18056)
    parser.add_argument('--write-demo-fixture', action='store_true',
                        help='Save the successfully executed synthetic jobs for the offline app; never real-data evidence')
    parser.add_argument('--offline-only', action='store_true', help='Verify an already built standalone demo without starting API servers')
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT/'artifacts'):
        parser.error('Use an isolated artifacts directory')
    out.mkdir(parents=True, exist_ok=True)
    errors, console_errors = [], []
    result = {'scope': 'Isolated real HTTP UI checks; configured protocol and training use synthetic mechanism fixtures only',
              'real_video_read': False, 'real_learning_effect_verified': False, 'screenshots_contain_media': False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, args=['--no-sandbox'])
        if args.offline_only:
            result = offline_protocol(browser, out)
            result['browser'] = browser.version
            save(out/'offline-protocol-browser.json', result)
            print(json.dumps(result, ensure_ascii=False, indent=2));browser.close();return
        for minimum_sessions in (None, 3, 2):
            path = configuration(out, minimum_sessions, args.port)
            original_config = hashlib.sha256(path.read_bytes()).hexdigest()
            with (path.parent.parent/'api.log').open('w') as log:
                process, client, _ = server(path, log)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN', accept_downloads=True)
                page = context.new_page()
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('console', lambda m: console_errors.append(m.text) if m.type == 'error' else None)
                try:
                    address = f'http://127.0.0.1:{args.port}/'
                    if minimum_sessions:
                        result[f'configured_minimum_{minimum_sessions}'] = configured_protocol(page, client, address, out, minimum_sessions)
                    else:
                        result['missing_protocol'] = missing_protocol(page, client, address, out)
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_config
                finally:
                    context.close();client.close();process.terminate()
                    try: process.wait(timeout=20)
                    except subprocess.TimeoutExpired: process.kill();process.wait()
        if not args.write_demo_fixture:
            result['offline'] = offline_protocol(browser, out)
        result.update(passed=True, browser=browser.version, page_errors=errors, console_errors=console_errors,
                      build_sha256=hashlib.sha256((ROOT/'apps/web/dist/index.html').read_bytes()).hexdigest())
        browser.close()
    assert not errors, errors
    assert not console_errors, console_errors
    if args.write_demo_fixture:
        jobs = json.loads((out/'minimum-2-synthetic-jobs.json').read_text())
        assert len(jobs) == 2 and all(j['report']['data_origin'] == 'synthetic_features' for j in jobs)
        save(ROOT/'apps/web/src/researchExample.json', {
            'scope': 'Recorded P0.3.5 synthetic mechanism browser fixtures; no real acquisition or real learning evidence.',
            'real_effect_verified': False,
            'scenes': {'anonymous_observation': [dict(job, demo_reference=True) for job in reversed(jobs)]}})
        result['demo_fixture_written'] = True
    save(out/'protocol-browser.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
