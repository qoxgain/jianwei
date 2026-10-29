"""Actual CPU import, decoded video and frozen graph integration evidence."""
from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess

import numpy as np
import pytest

from packages.config.loader import load_bundle
from packages.config.errors import ContractError
from packages.contracts.ingestion import ImportRequest
from packages.contracts.learning import FeatureBatch, assert_future_pair
from packages.ingestion import process_import
from packages.ingestion_examples import build_example
from packages.knowledge.service import content_hash


def fixture_request(bundle, directory, scene='anonymous_observation'):
    example = build_example(bundle, scene, directory)
    request = ImportRequest(**example['request_fields'], upload_id='input', encoder_artifact_id='fingerprint')
    return example, request


def imported(bundle, example, request, output, scene='anonymous_observation', source_path=None):
    return process_import(bundle, scene, request, example['input_path'],
                          encoder_artifact_path=example['encoder_path'], source_path=source_path, output_dir=output)


def error_code(code, fn):
    with pytest.raises(ContractError) as caught:
        fn()
    assert caught.value.code == code
    return caught.value


@pytest.mark.parametrize('scene', ['garment_sop', 'electronics_assembly', 'workshop_safety', 'anonymous_observation'])
def test_four_scenes_share_real_npz_cache_path_without_labels(tmp_path, scene):
    bundle = load_bundle()
    example, request = fixture_request(bundle, tmp_path/'input', scene)
    output = tmp_path/'first'
    manifest = imported(bundle, example, request, output, scene)
    again = imported(bundle, example, request, tmp_path/'second', scene)
    assert manifest == again
    assert manifest['status'] == 'succeeded'
    assert manifest['source_verification'] == 'synthetic'
    assert manifest['source_hash'] != manifest['artifact_hash']
    assert manifest['eligible_rows'] == manifest['total_rows'] == 120
    assert len(manifest['episodes']) == 2
    assert manifest['encoder_evidence']['execution'] == 'not_executed'
    assert manifest['feature_cache']['sha256'] == hashlib.sha256((output/'vectors.npy').read_bytes()).hexdigest()
    np.testing.assert_array_equal(np.load(output/'vectors.npy', allow_pickle=False),
                                  np.load(example['input_path'], allow_pickle=False)['features'])
    assert all(row['capture_start_utc'] is None for row in manifest['rows'])
    assert str(tmp_path) not in str(manifest)
    for batch in manifest['feature_batches']:
        FeatureBatch.model_validate(batch)


def test_npz_encoder_bytes_must_match_and_are_never_executed(tmp_path):
    bundle = load_bundle(); example, request = fixture_request(bundle, tmp_path/'input')
    example['encoder_path'].write_bytes(b'changed fingerprint')
    error_code('ENCODER_HASH_MISMATCH', lambda: imported(bundle, example, request, tmp_path/'output'))
    assert not (tmp_path/'output/vectors.npy').exists()


def test_context_and_source_validation_is_actual_import_gate(tmp_path):
    bundle = load_bundle(); example, request = fixture_request(bundle, tmp_path/'input', 'garment_sop')
    error_code('CONTEXT_REQUIRED', lambda: imported(bundle, example, request.model_copy(update={'context': {}}), tmp_path/'out', 'garment_sop'))
    wrong = dict(request.context); wrong['camera_id'] = True
    error_code('CONTEXT_TYPE', lambda: imported(bundle, example, request.model_copy(update={'context': wrong}), tmp_path/'out', 'garment_sop'))
    wrong = dict(request.context); wrong['injected'] = 'bad'
    error_code('CONTEXT_UNKNOWN', lambda: imported(bundle, example, request.model_copy(update={'context': wrong}), tmp_path/'out', 'garment_sop'))
    error_code('SOURCE_SCENE_MISMATCH', lambda: imported(bundle, example, request.model_copy(update={'source_id': 'not_registered'}), tmp_path/'out', 'garment_sop'))
    error_code('SOURCE_ORIGIN_MISMATCH', lambda: imported(bundle, example, request.model_copy(update={'data_origin': 'real_video'}), tmp_path/'out', 'garment_sop'))


def test_external_source_claim_remains_unverified_and_unassigned(tmp_path):
    bundle = load_bundle(); example, request = fixture_request(bundle, tmp_path/'input')
    source = next(s for s in bundle.profile.sources if s.id == 'file_01')
    space = request.feature_space.model_copy(update={'roi_policy_hash': content_hash({'source_id': source.id, 'roi_xyxy': source.roi_xyxy})})
    request = request.model_copy(update={'source_id': 'file_01', 'data_origin': 'real_video', 'feature_space': space})
    manifest = imported(bundle, example, request, tmp_path/'out')
    assert manifest['source_verification'] == 'declared'
    assert manifest['source_verified'] is False and manifest['split'] == 'unassigned'
    error_code('SOURCE_UNVERIFIED', lambda: imported(bundle, example, request.model_copy(update={'requested_split': 'train'}), tmp_path/'bad'))
    error_code('SOURCE_HASH_REQUIRED', lambda: imported(bundle, example, request.model_copy(update={'declared_source_hash': None}), tmp_path/'bad'))


def test_episode_boundaries_exclude_crossing_vectors_without_truncation(tmp_path):
    bundle = load_bundle(); example, request = fixture_request(bundle, tmp_path/'input', 'garment_sop')
    data = np.load(example['input_path'], allow_pickle=False)
    durations = data['durations_ms'].copy(); durations[59] = 2000
    np.savez_compressed(example['input_path'], features=data['features'], pts_ms=data['pts_ms'], durations_ms=durations)
    manifest = imported(bundle, example, request, tmp_path/'out', 'garment_sop')
    assert manifest['total_rows'] == 120 and manifest['eligible_rows'] == 119
    assert manifest['rejected_rows'] == [{'row': 59, 'reason': 'outside_complete_episode'}]
    assert manifest['rows'][59]['start_ms'] == 59000 and manifest['rows'][59]['end_ms'] == 61000


def test_time_domain_prevents_future_pair_even_with_reused_episode_id(tmp_path):
    bundle = load_bundle(); example, request = fixture_request(bundle, tmp_path/'input')
    manifest = imported(bundle, example, request, tmp_path/'out')
    history = FeatureBatch.model_validate(manifest['feature_batches'][0]).model_copy(update={'split': 'train'})
    target = FeatureBatch.model_validate(manifest['feature_batches'][2]).model_copy(update={'split': 'train', 'time_domain_id': 'd1'})
    with pytest.raises(ValueError): assert_future_pair(history, target, 1500)


@pytest.fixture
def video(tmp_path):
    path = tmp_path/'test-pattern.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=16:duration=4',
                    '-c:v', 'mpeg4', '-threads', '1', '-y', str(path)], check=True, timeout=30)
    return path


def video_request():
    return ImportRequest(upload_id='test-pattern', format='mp4', source_id='cam_01', data_origin='synthetic_features',
        episode_origin='manual', episodes=[{'episode_id': 'sample', 'domain_index': 0, 'start_ms': 0, 'end_ms': 4000}])


def test_actual_mp4_decode_preserves_pts_quality_and_blocks_missing_adapter(tmp_path, video):
    bundle = load_bundle()
    manifest = process_import(bundle, 'anonymous_observation', video_request(), video, output_dir=tmp_path/'out')
    assert manifest['status'] == 'blocked'
    assert manifest['blocker']['code'] == 'ENCODER_ADAPTER_UNAVAILABLE'
    assert manifest['quality']['decoded_frames'] == 64
    assert manifest['quality']['score'] > 0.8
    assert manifest['source_hash'] == hashlib.sha256(video.read_bytes()).hexdigest()
    assert manifest['source_verification'] == 'synthetic'
    assert manifest['domains'][0]['start_ms'] == 0 and manifest['domains'][0]['end_ms'] == 4000
    assert manifest['feature_batches'] == [] and not (tmp_path/'out/vectors.npy').exists()
    assert not list((tmp_path/'out').glob('decode-*'))


def onnx_bundle(tmp_path, *, pin=True):
    onnx = pytest.importorskip('onnx'); pytest.importorskip('onnxruntime')
    helper, tensor = onnx.helper, onnx.TensorProto
    graph = helper.make_graph([helper.make_node('ReduceMean', ['clips'], ['features'], axes=[1, 3, 4], keepdims=0)],
        'analytical_rgb_fixture_only', [helper.make_tensor_value_info('clips', tensor.FLOAT, ['N', 'T', 3, 16, 16])],
        [helper.make_tensor_value_info('features', tensor.FLOAT, ['N', 3])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 13)]); model.ir_version = 10
    path = tmp_path/'analytical.onnx'; path.write_bytes(model.SerializeToString())
    bundle = load_bundle()
    config = bundle.config.model_copy(deep=True)
    config.encoder.adapter = 'onnx'; config.encoder.feature_dim = 3
    config.encoder.weights_sha256 = hashlib.sha256(path.read_bytes()).hexdigest() if pin else None
    config.profiles['demo'].encoder_weights = str(path)
    config.ingestion.encoder_input_size = 16
    return replace(bundle, config=config, profile=config.profiles['demo'])


def test_actual_frozen_onnx_video_features_are_reproducible(tmp_path, video):
    bundle = onnx_bundle(tmp_path)
    first = process_import(bundle, 'anonymous_observation', video_request(), video, output_dir=tmp_path/'one')
    second = process_import(bundle, 'anonymous_observation', video_request(), video, output_dir=tmp_path/'two')
    assert first == second
    assert first['status'] == 'succeeded' and first['eligible_rows'] == 5
    assert first['encoder_evidence']['execution'] == 'frozen_onnx_cpu'
    assert first['feature_space']['encoder_sha'] == bundle.config.encoder.weights_sha256
    vectors = np.load(tmp_path/'one/vectors.npy', allow_pickle=False)
    assert vectors.shape == (5, 3) and np.isfinite(vectors).all()
    assert np.all((vectors >= 0) & (vectors <= 1)) and not np.all(vectors == vectors[0])
    np.testing.assert_array_equal(vectors, np.load(tmp_path/'two/vectors.npy', allow_pickle=False))
    assert all(b['pts_end_ms'] <= 4000 for b in first['feature_batches'])


def test_onnx_import_requires_pinned_sha(tmp_path, video):
    bundle = onnx_bundle(tmp_path, pin=False)
    manifest = process_import(bundle, 'anonymous_observation', video_request(), video, output_dir=tmp_path/'out')
    assert manifest['status'] == 'blocked' and manifest['blocker']['code'] == 'ENCODER_SHA_NOT_PINNED'
    assert manifest['feature_cache'] is None


def test_actual_invalid_video_returns_safe_error(tmp_path):
    path = tmp_path/'private-customer-video.mp4'; path.write_bytes(b'not a movie')
    error = error_code('VIDEO_DECODE_FAILED', lambda: process_import(load_bundle(), 'anonymous_observation', video_request(), path, output_dir=tmp_path/'out'))
    assert str(tmp_path) not in str(error.detail())


def test_short_observation_extracts_features_without_inventing_full_cycle_or_future(tmp_path):
    """Analytical test graph checks mechanics only; no pretrained learning claim."""
    from packages.contracts.scenes import EpisodePolicy
    from packages.temporal.data import metadata_sequences, window_refs
    path = tmp_path / 'short-20.44.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=64x48:rate=25:duration=20.44', '-c:v', 'mpeg4', '-threads', '1', '-y', str(path)],
        check=True, timeout=30)
    bundle = onnx_bundle(tmp_path)
    bundle.config.video.stride_seconds = 2
    bundle.scenes['anonymous_observation'].episode_policy = EpisodePolicy(mode='fixed_window',
        window_seconds=60, observation_contract='p033-observation-v1', partial_window='extract_only')
    request = ImportRequest(upload_id='short', format='mp4', source_id='cam_01', data_origin='synthetic_features')
    manifest = process_import(bundle, 'anonymous_observation', request, path, output_dir=tmp_path / 'short-out')
    assert manifest['status'] == 'succeeded' and manifest['eligible_rows'] == 10
    assert manifest['domains'][0]['end_ms'] == 20440
    episode, = manifest['episodes']
    assert episode['complete'] is False and episode['kind'] == 'observation_window'
    assert episode['business_cycle_verified'] is False and episode['end_ms'] == 20440
    assert all(b['pts_end_ms'] <= 20440 for b in manifest['feature_batches'])
    sequences = metadata_sequences(manifest, bundle.config.temporal)
    learning = bundle.config.learning.model_copy(update={'history_windows': 4, 'forecast_horizons_seconds': [30.0]})
    windows, excluded = window_refs(sequences, learning, bundle.config.temporal)
    assert windows == [] and excluded['future_not_available'] == 7
    assert bundle.scenes['anonymous_observation'].episode_policy.window_seconds == 60
