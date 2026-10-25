"""Run an actual, pinned encoder through the durable API and preserve evidence.

Pass an isolated system.yaml prepared with the documented pretrained recipe.
This command never partitions the single source or calls temporal training.
It writes the actual portable manifest/cache, not an invented example result.
"""
import argparse
import json
from pathlib import Path
import shutil
import time

import numpy as np
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.config.loader import PROJECT_ROOT, resolve_path
from packages.ingestion.pipeline import file_hash
from packages.knowledge.service import content_hash
from scripts.validate_real_video import write_json


def capture(config, video, output, timeout_seconds):
    output.mkdir(parents=True, exist_ok=True)
    app = create_app(config)
    started = time.monotonic()
    with TestClient(app) as client:
        bundle = app.state.bundle
        if bundle.profile.mode != 'demo':
            raise ValueError('Use an isolated demo profile for acceptance')
        weights = resolve_path(bundle.root, bundle.profile.encoder_weights)
        if bundle.config.encoder.weights_sha256 != file_hash(weights):
            raise ValueError('The configured encoder must be pinned to actual bytes')
        upload = client.post('/api/uploads?kind=mp4', content=video.read_bytes(),
                             headers={'Content-Type': 'application/octet-stream'})
        upload.raise_for_status()
        request = {'upload_id': upload.json()['id'], 'format': 'mp4', 'source_id': 'file_01',
                   'data_origin': 'real_video', 'requested_split': 'unassigned', 'context': {}}
        base = '/api/scenes/anonymous_observation/imports'
        response = client.post(base, json=request)
        response.raise_for_status()
        job = response.json()
        deadline = time.monotonic() + timeout_seconds
        while job['status'] in {'queued', 'running'}:
            if time.monotonic() >= deadline:
                raise TimeoutError('Acceptance deadline exceeded; worker shuts down cleanly before exit')
            time.sleep(.25)
            response = client.get(base + '/' + job['id'])
            response.raise_for_status()
            job = response.json()
        write_json(output/'job.json', job)
        if job['status'] != 'succeeded':
            raise RuntimeError(f"Official encoder import {job['status']}: {job.get('error')}")
        manifest = job['manifest']
        if manifest['source_hash'] != file_hash(video) or manifest['split'] != 'unassigned':
            raise ValueError('Source identity or whole-source partition changed')
        if manifest['encoder_evidence']['execution'] != 'frozen_onnx_cpu':
            raise ValueError('Acceptance requires actual frozen ONNX execution')
        repeated = client.post(base, json=request)
        repeated.raise_for_status()
        if repeated.json()['id'] != job['id']:
            raise ValueError('Repeated import was not idempotent')
        vectors_path = app.state.imports.output_root / job['id'] / 'vectors.npy'
        if file_hash(vectors_path) != manifest['feature_cache']['sha256']:
            raise ValueError('Feature cache hash differs from manifest')
        shutil.copyfile(vectors_path, output/'vectors.npy')
        write_json(output/'manifest.json', manifest)
        write_json(output/'config-snapshot.json', bundle.snapshot)
        vectors = np.load(vectors_path, allow_pickle=False)
        variation = float(np.max(np.std(vectors.astype(np.float64), axis=0)))
        if not np.isfinite(vectors).all() or variation <= 1e-6:
            raise ValueError('Real clips did not produce finite, nonconstant features')
        report = {
            'stage': 'P0.3.1', 'source_file': video.name, 'source_sha256': file_hash(video),
            'baseline_commit': '6f33b2b66486e9e852c5fda55ff414d6715318fa',
            'config_hash': bundle.config_hash, 'manifest_sha256': file_hash(output/'manifest.json'),
            'feature_cache_sha256': file_hash(output/'vectors.npy'),
            'feature_space_id': manifest['feature_space_id'], 'encoder_sha256': file_hash(weights),
            'encoder_preprocessing_version': bundle.config.encoder.preprocessing_version,
            'decoder_width': bundle.config.ingestion.decode_width,
            'encoder_input_size': bundle.config.ingestion.encoder_input_size,
            'decoded_frames': manifest['quality']['decoded_frames'],
            'total_rows': manifest['total_rows'], 'eligible_rows': manifest['eligible_rows'],
            'feature_dimension': int(vectors.shape[1]), 'max_feature_std_across_actual_clips': variation,
            'finite_nonconstant_features': True, 'repeat_same_import_id': True,
            'split': manifest['split'], 'independent_original_sources': 1,
            'semantic_accuracy': None, 'real_world_learning_effect_verified': False,
            'train_calibration_test_executed': False,
            'elapsed_seconds': round(time.monotonic()-started, 3),
            'episodes': manifest['episodes'], 'quality': manifest['quality'],
        }
        report['evidence_hash'] = content_hash(report)
        write_json(output/'real-video.json', report)
        print(json.dumps({key: report[key] for key in ('decoded_frames','eligible_rows','feature_dimension',
                         'finite_nonconstant_features','elapsed_seconds')}, ensure_ascii=False), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--video', type=Path, default=PROJECT_ROOT/'examples/test.mp4')
    parser.add_argument('--output-dir', type=Path, default=Path('artifacts/p031-video-evidence'))
    parser.add_argument('--timeout-seconds', type=float, default=1800)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error('--timeout-seconds must be positive')
    capture(args.config, args.video, args.output_dir, args.timeout_seconds)
