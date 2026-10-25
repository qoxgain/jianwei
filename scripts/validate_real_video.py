"""Reproducible full-source MP4 smoke test with explicit diagnostic provenance.

The optional --config uses a supplied, pinned ONNX encoder. By default we first
record the unchanged configuration's missing-encoder blocker, then execute an analytical RGB-mean
graph to test tensor plumbing only. This is not a trained vision model and never
produces action labels or research accuracy. The whole source stays unassigned.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import yaml
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.config.loader import PROJECT_ROOT


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def analytical_model(path, size):
    import onnx
    helper, tensor = onnx.helper, onnx.TensorProto
    graph = helper.make_graph(
        [helper.make_node('ReduceMean', ['clips'], ['features'], axes=[1, 3, 4], keepdims=0)],
        'diagnostic_rgb_mean_not_a_trained_vision_model',
        [helper.make_tensor_value_info('clips', tensor.FLOAT, ['N', 'T', 3, size, size])],
        [helper.make_tensor_value_info('features', tensor.FLOAT, ['N', 3])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 13)])
    model.ir_version = 10
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(model.SerializeToString())


def execute(config_path, video, expected, output, name):
    app = create_app(config_path)
    with TestClient(app) as client:
        upload = client.post('/api/uploads?kind=mp4', content=video.read_bytes(),
                             headers={'Content-Type': 'application/octet-stream'})
        upload.raise_for_status()
        body = {'upload_id': upload.json()['id'], 'format': 'mp4', 'source_id': 'file_01',
                'data_origin': 'real_video', 'requested_split': 'unassigned', 'context': {}}
        response = client.post('/api/scenes/anonymous_observation/imports', json=body)
        response.raise_for_status()
        deadline = time.monotonic() + 180
        job = response.json()
        while job['status'] in {'queued', 'running'} and time.monotonic() < deadline:
            time.sleep(.1)
            job = client.get('/api/scenes/anonymous_observation/imports/' + job['id']).json()
        if expected and job['status'] != expected:
            raise RuntimeError(f"{name}: {job['status']}, {job.get('error')}")
        repeated = client.post('/api/scenes/anonymous_observation/imports', json=body).json()
        assert repeated['id'] == job['id']
        manifest = job.get('manifest')
        if manifest:
            write_json(output / (name + '-manifest.json'), manifest)
        result = {'status': job['status'], 'error': job.get('error'), 'repeat_same_id': True,
                  'config_hash': app.state.bundle.config_hash,
                  'split': 'unassigned', 'research_train_or_test_used': False}
        if manifest:
            result.update({key: manifest.get(key) for key in (
                'source_hash', 'source_verification', 'video', 'quality', 'domains', 'episodes',
                'total_rows', 'eligible_rows', 'feature_space_id', 'feature_cache', 'encoder_evidence')})
            result['discarded_clips'] = len(manifest.get('discarded_clips', []))
        return result


def capture(video, output, supplied_config=None):
    source_sha = hashlib.sha256(video.read_bytes()).hexdigest()
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                        '-show_format', '-of', 'json', str(video)], text=True))
    # Do not publish machine-specific absolute paths.
    probe['format']['filename'] = video.name
    report = {'source_file': video.name, 'source_sha256': source_sha, 'probe': probe,
              'independent_original_sources': 1, 'semantic_accuracy': None,
              'real_world_learning_effect_verified': False, 'tests': {}}
    output.mkdir(parents=True, exist_ok=True)
    if supplied_config:
        report['tests']['configured_encoder'] = execute(supplied_config, video, None, output, 'configured')
    else:
        with tempfile.TemporaryDirectory(prefix='jianwei-real-video-') as temporary:
            root = Path(temporary)
            shutil.copytree(PROJECT_ROOT / 'knowledge', root / 'knowledge')
            (root / 'config').mkdir()
            config = yaml.safe_load((PROJECT_ROOT / 'config/system.yaml').read_text())
            path = root / 'config/system.yaml'
            path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
            report['tests']['default_config'] = execute(path, video, 'blocked', output, 'default')
            model_path = root / 'models/diagnostic.onnx'
            analytical_model(model_path, config['ingestion']['encoder_input_size'])
            config['profiles']['demo']['encoder_weights'] = './models/diagnostic.onnx'
            config['encoder'].update(adapter='onnx', feature_dim=3, pooling='diagnostic_rgb_mean',
                weights_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
                checkpoint_revision='diagnostic-rgb-mean-v1-not-pretrained')
            path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
            report['tests']['diagnostic_encoder'] = execute(path, video, 'succeeded', output, 'diagnostic')
            report['tests']['diagnostic_encoder']['encoder_purpose'] = 'Analytical RGB mean; plumbing only, not learned visual perception.'
            report['diagnostic_overrides'] = {'max_expanded_bytes': config['ingestion']['max_expanded_bytes'], 'feature_dim': 3,
                                             'adapter': 'onnx', 'pretrained': False}
    write_json(output / 'real-video.json', report)
    for name, result in report['tests'].items():
        print(name, result['status'], result.get('error'), result.get('eligible_rows'), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--video', type=Path, default=PROJECT_ROOT / 'examples/test.mp4')
    parser.add_argument('--output-dir', type=Path, default=Path('artifacts/video-review'))
    parser.add_argument('--config', type=Path, help='Optional actual pinned encoder configuration; no diagnostic override')
    args = parser.parse_args()
    capture(args.video, args.output_dir, args.config)
