"""Execute all four scenes through the API; preserve real numeric model outputs.

The eight independent synthetic source seeds are reused across scenes. They are
eight sources, not 32 independent videos. No real video or semantic labels enter
this experiment. Test is a separate request after calibration selected a model.
"""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import time

from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.config.loader import PROJECT_ROOT


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def wait(client, path, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(path)
        response.raise_for_status()
        job = response.json()
        if job['status'] not in {'queued', 'running'}:
            if job['status'] != 'succeeded':
                raise RuntimeError(f"{path}: {job['error']}")
            return job
        time.sleep(.05)
    raise TimeoutError(path)


def capture(output, fixture):
    references, summary = {}, {}
    with tempfile.TemporaryDirectory(prefix='jianwei-p03-evidence-') as temporary:
        root = Path(temporary)
        shutil.copytree(PROJECT_ROOT / 'config', root / 'config')
        shutil.copytree(PROJECT_ROOT / 'knowledge', root / 'knowledge')
        app = create_app(root / 'config/system.yaml')
        with TestClient(app) as client:
            for scene in app.state.bundle.scenes:
                prefix = f'/api/scenes/{scene}/temporal'
                response = client.post(prefix + '/example')
                response.raise_for_status()
                example = response.json()
                for identifiers in example['groups'].values():
                    for identifier in identifiers:
                        wait(client, f'/api/scenes/{scene}/imports/{identifier}')
                request = {'import_ids': example['train_import_ids']}
                response = client.post(prefix + '/train', json=request)
                response.raise_for_status()
                trained = wait(client, prefix + '/runs/' + response.json()['id'])
                assert client.post(prefix + '/train', json=request).json()['id'] == trained['id']
                response = client.post(prefix + '/evaluate', json={
                    'model_run_id': trained['id'], 'import_ids': example['test_import_ids'], 'sealed_test': True})
                response.raise_for_status()
                evaluated = wait(client, prefix + '/runs/' + response.json()['id'])
                references[scene] = [{**r, 'demo_reference': True} for r in (trained, evaluated)]
                for row in (trained, evaluated):
                    write_json(output / scene / (row['kind'] + '.json'), row)
                if scene == 'anonymous_observation':
                    for name in ('weights.npz', 'model.json'):
                        artifact = client.get(prefix + '/runs/' + trained['id'] + '/artifacts/' + name)
                        artifact.raise_for_status()
                        (output / scene / name).write_bytes(artifact.content)
                summary[scene] = {
                    'train_sources': trained['report']['used_training_sources'],
                    'calibration_sources': trained['report']['calibration']['independent_sources'],
                    'test_sources': evaluated['report']['test']['independent_sources'],
                    'selected_model': trained['report']['model']['selected_model'],
                    'test_source_macro_mse': evaluated['report']['test']['source_macro_mse'],
                    'weights_sha256': trained['report']['model']['weights_sha256'],
                    'repeat_same_id': True,
                }
                assert len({s['weights_sha256'] for s in summary.values()}) == 1, 'Identical source content changed weights across scenes'
                print(scene, summary[scene], flush=True)
    write_json(output / 'summary.json', {
        'scope': 'Eight synthetic sources reused across four scenes; not field evidence.',
        'independent_source_seeds': list(range(1001, 1009)), 'scenes': summary,
        'semantic_accuracy': None, 'visual_encoder_trained': False, 'auto_activated': False,
    })
    if fixture:
        write_json(fixture, {'scenes': references})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, default=Path('docs/delivery/p0.3'))
    parser.add_argument('--ui-fixture', type=Path)
    args = parser.parse_args()
    capture(args.output_dir, args.ui_fixture)
