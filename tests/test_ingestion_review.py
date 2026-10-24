"""Source-clock identity regressions across independently imported NPZ subsets."""
import hashlib
from pathlib import Path

import numpy as np
import pytest

from packages.config.loader import load_bundle
from packages.contracts.ingestion import ImportRequest
from packages.contracts.learning import FeatureBatch, assert_future_pair
from packages.ingestion import process_import
from packages.ingestion_examples import build_example
from packages.knowledge.service import content_hash


def import_subset(tmp_path, monkeypatch, name, pts, original_pts, source_domain, values=1):
    """Exercise provenance mapping with the decoder's documented PTS output.

    Real FFmpeg decoding is covered separately. This unit fixture isolates the
    reset/gap mapping, including timestamps that repeat after a source reset.
    """
    bundle = load_bundle()
    directory = tmp_path / name
    sample = build_example(bundle, 'anonymous_observation', directory)
    source = tmp_path / 'original.mp4'
    source.write_bytes(b'source-clock-mapping-unit-fixture')
    fields = sample['request_fields'] | {
        'upload_id': name, 'encoder_artifact_id': 'fingerprint', 'source_upload_id': 'source',
        'source_id': 'file_01', 'data_origin': 'real_video', 'requested_split': 'train',
        'declared_source_hash': hashlib.sha256(source.read_bytes()).hexdigest(),
        'source_domain_map': source_domain,
        'episode_origin': 'manual',
        'episodes': [{'episode_id': 'external_cycle', 'domain_index': 0,
                      'start_ms': int(pts[0]), 'end_ms': int(pts[-1]) + 1000}],
    }
    fields['feature_space']['roi_policy_hash'] = content_hash(
        {'source_id': 'file_01', 'roi_xyxy': [0.0, 0.0, 1.0, 1.0]})
    np.savez_compressed(sample['input_path'],
        features=np.full((len(pts), bundle.config.encoder.feature_dim), values, dtype=np.float32),
        pts_ms=np.array(pts, dtype=np.int64), durations_ms=np.full(len(pts), 1000, dtype=np.int64))
    def decoded(*args):
        return (np.zeros((len(original_pts), 1, 1, 3), dtype=np.uint8),
                np.array(original_pts, dtype=np.int64), np.full(len(original_pts), 1000),
                np.ones(len(original_pts)), {}, {})
    monkeypatch.setattr('packages.ingestion.pipeline.decode_video', decoded)
    return process_import(bundle, 'anonymous_observation', ImportRequest.model_validate(fields),
        sample['input_path'], sample['encoder_path'], source, directory / 'output')


def test_independent_npz_subsets_in_distinct_source_reset_domains_cannot_pair(tmp_path, monkeypatch):
    original = list(range(0, 6000, 1000)) * 2
    history = import_subset(tmp_path, monkeypatch, 'before_reset', [0, 1000], original, [0])
    future = import_subset(tmp_path, monkeypatch, 'after_reset', [3000, 4000], original, [1])
    assert history['domains'][0]['local_time_domain_id'] == future['domains'][0]['local_time_domain_id'] == 'd0'
    assert history['domains'][0]['source_time_domain_id'] == 'd0'
    assert future['domains'][0]['source_time_domain_id'] == 'd1'
    left = FeatureBatch.model_validate(history['feature_batches'][0])
    right = FeatureBatch.model_validate(future['feature_batches'][0])
    assert left.time_domain_id != right.time_domain_id
    with pytest.raises(ValueError, match='时间域'):
        assert_future_pair(left, right, issued_at_pts_ms=2000)


def test_distinct_continuous_subsets_in_one_source_domain_do_not_erase_gap_barrier(tmp_path, monkeypatch):
    original = list(range(0, 10000, 1000))
    early = import_subset(tmp_path, monkeypatch, 'early', [0, 1000], original, [0])
    late = import_subset(tmp_path, monkeypatch, 'late', [8000, 9000], original, [0])
    assert early['domains'][0]['source_time_domain_id'] == late['domains'][0]['source_time_domain_id'] == 'd0'
    left = FeatureBatch.model_validate(early['feature_batches'][0])
    right = FeatureBatch.model_validate(late['feature_batches'][0])
    assert left.time_domain_id != right.time_domain_id
    with pytest.raises(ValueError, match='时间域'):
        assert_future_pair(left, right, issued_at_pts_ms=2000)


def test_same_verified_source_and_continuous_span_has_stable_domain_identity(tmp_path, monkeypatch):
    original = list(range(0, 10000, 1000))
    first = import_subset(tmp_path, monkeypatch, 'one', [0, 1000, 2000], original, [0])
    second = import_subset(tmp_path, monkeypatch, 'two', [0, 1000, 2000], original, [0], values=2)
    left = FeatureBatch.model_validate(first['feature_batches'][0])
    right = FeatureBatch.model_validate(second['feature_batches'][2])
    assert left.time_domain_id == right.time_domain_id
    assert_future_pair(left, right, issued_at_pts_ms=1000)
