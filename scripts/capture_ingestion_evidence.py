"""Capture actual API import results for review and the standalone demo.

Uses an isolated database and deterministic synthetic fixtures, never user data.
"""
import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.config.loader import PROJECT_ROOT


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def wait_job(client, scene, job):
    deadline = time.monotonic() + 30
    while job['status'] in {'queued', 'running'} and time.monotonic() < deadline:
        time.sleep(0.05)
        response = client.get(f"/api/scenes/{scene}/imports/{job['id']}")
        response.raise_for_status()
        job = response.json()
    if job['status'] in {'queued', 'running'}:
        raise RuntimeError('Import worker did not finish within the evidence deadline')
    return job


def capture(output: Path, ui_fixture: Path | None):
    scenes = ['garment_sop', 'electronics_assembly', 'workshop_safety', 'anonymous_observation']
    results, manifests = {}, {}
    with tempfile.TemporaryDirectory(prefix='jianwei-p02-evidence-') as temp:
        root = Path(temp)
        (root / 'config').mkdir()
        shutil.copytree(PROJECT_ROOT / 'knowledge', root / 'knowledge')
        config = yaml.safe_load((PROJECT_ROOT / 'config/system.yaml').read_text())
        config_path = root / 'config/system.yaml'
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
        with TestClient(create_app(config_path)) as client:
            results['health'] = client.get('/api/health').json()
            results['examples'] = {}
            for scene in scenes:
                response = client.post(f'/api/scenes/{scene}/imports/example')
                response.raise_for_status()
                job = wait_job(client, scene, response.json())
                assert job['status'] == 'succeeded', job
                again = client.post(f'/api/scenes/{scene}/imports/example').json()
                assert again['id'] == job['id'], 'Example import was not idempotent'
                manifests[scene] = job['manifest']
                results['examples'][scene] = {'job': job, 'repeat_same_id': True}
            fixture = root / 'synthetic-test-pattern.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=160x120:rate=8', '-t', '4', '-c:v', 'libx264',
                            '-threads', '1', '-pix_fmt', 'yuv420p', '-y', str(fixture)], check=True)
            upload = client.post('/api/uploads?kind=mp4', content=fixture.read_bytes(),
                                 headers={'Content-Type': 'application/octet-stream'})
            upload.raise_for_status()
            response = client.post('/api/scenes/anonymous_observation/imports', json={
                'upload_id': upload.json()['id'], 'format': 'mp4', 'source_id': 'cam_01',
                'data_origin': 'synthetic_features', 'episode_origin': 'manual',
                'episodes': [{'episode_id': 'test_pattern', 'domain_index': 0, 'start_ms': 0, 'end_ms': 4000}],
                'context': {}, 'requested_split': 'unassigned', 'clock_anchors': []})
            response.raise_for_status()
            video = wait_job(client, 'anonymous_observation', response.json())
            assert video['status'] == 'blocked', video
            assert video['manifest']['quality']['decoded_frames'] == 32
            results['synthetic_video_without_weights'] = video
    results['scope'] = 'Synthetic fixtures, isolated SQLite; no field accuracy or trained visual model.'
    write_json(output / 'api-imports.json', results)
    if ui_fixture:
        write_json(ui_fixture, {'notice': '真实服务端导入合成输入得到的静态记录；浏览器未执行解码或任务。', 'scenes': manifests})
    print('Captured four actual NPZ imports, idempotency, and MP4 decode/blocked evidence')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, default=Path('docs/delivery/p0.2'))
    parser.add_argument('--ui-fixture', type=Path)
    args = parser.parse_args()
    capture(args.output_dir, args.ui_fixture)
