import copy
import json
from pathlib import Path

import numpy as np
import pytest

from packages.config.errors import ContractError
from packages.contracts.temporal import TemporalSettings
from packages.ingestion.pipeline import file_hash
from packages.temporal.exploration import explore, union_intervals
from scripts.preview_real_video import render_html


@pytest.fixture
def source(tmp_path):
    original = Path(__file__).resolve().parents[1] / 'docs/delivery/p0.3/video/diagnostic-manifest.json'
    manifest = json.loads(original.read_text())
    video = tmp_path / 'test.mp4'
    video.write_bytes(b'unit fixture bytes; not claimed as an actual video experiment')
    digest = file_hash(video)
    vectors = tmp_path / 'vectors.npy'
    x = np.arange(9)
    np.save(vectors, np.stack([np.sin(x), np.cos(x), x / 9], axis=1).astype('float32'))
    cache_hash = file_hash(vectors)
    manifest.update(source_hash=digest, config_hash='a' * 64, total_rows=9, eligible_rows=9)
    manifest['feature_cache'].update(sha256=cache_hash, rows=9, uri=f'features://{cache_hash}')
    manifest['rows'] = manifest['rows'][:9]
    manifest['feature_batches'] = manifest['feature_batches'][:9]
    for batch in manifest['feature_batches']:
        row = int(batch['vector_uri'].split('#row=')[1])
        batch.update(source_hash=digest, vector_uri=f'features://{cache_hash}#row={row}')
    manifest['domains'][0]['end_ms'] = 12000
    manifest['episodes'][0]['end_ms'] = 6000
    manifest['episodes'][1].update(start_ms=6000, end_ms=12000)
    return manifest, vectors, video, TemporalSettings(), 'a' * 64


def test_exploration_uses_actual_rows_without_train_test_claim(source):
    result = explore(*source)
    assert result['split'] == 'unassigned'
    assert result['independent_original_sources'] == 1
    assert result['semantic_accuracy'] is None
    assert result['test_read'] is result['training_executed'] is False
    assert result['coverage']['covered_ms'] == 6000
    assert result['coverage']['fraction'] == .5
    assert result['incomplete_episodes'][0]['exclusion_reason'] == 'incomplete_episode'
    assert result['cluster_version'] == explore(*source)['cluster_version']
    for segment in result['segments']:
        representative = segment['representative']
        assert representative['row'] in segment['rows']
        row = source[0]['rows'][representative['row']]
        assert representative['start_ms'] == row['start_ms']
        assert representative['end_ms'] == row['end_ms']


@pytest.mark.parametrize('split', ['train', 'calibration', 'test'])
def test_exploration_cannot_read_reserved_partition(source, split):
    source[0]['split'] = split
    with pytest.raises(ContractError, match='未分区') as error:
        explore(*source)
    assert error.value.code == 'EXPLORATION_SPLIT_FORBIDDEN'


@pytest.mark.parametrize('target,code', [(1, 'EXPLORATION_CACHE_CHANGED'), (2, 'EXPLORATION_SOURCE_CHANGED')])
def test_changed_bytes_rejected(source, target, code):
    with source[target].open('ab') as stream:
        stream.write(b'tampered')
    with pytest.raises(ContractError) as error:
        explore(*source)
    assert error.value.code == code


def test_nonfinite_values_rejected_even_with_rehashed_file(source):
    values = np.load(source[1])
    values[0, 0] = np.nan
    np.save(source[1], values)
    digest = file_hash(source[1])
    source[0]['feature_cache']['sha256'] = digest
    for index, batch in enumerate(source[0]['feature_batches']):
        batch['vector_uri'] = f'features://{digest}#row={index}'
    with pytest.raises(ContractError) as error:
        explore(*source)
    assert error.value.code == 'EXPLORATION_NUMERICAL_FAILURE'


def test_representative_cannot_cross_missing_row(source):
    manifest = source[0]
    manifest['rows'][4]['eligible'] = False
    manifest['feature_batches'].pop(4)
    manifest['eligible_rows'] -= 1
    result = explore(*source)
    assert all(not (min(s['rows']) < 4 < max(s['rows'])) for s in result['segments'])
    assert 4 not in [c['row'] for c in result['clips']]


def test_episode_barrier_and_incomplete_rows_rejected(source):
    manifest = source[0]
    manifest['feature_batches'][0]['episode_id'] = manifest['episodes'][1]['episode_id']
    manifest['rows'][0]['episode_id'] = manifest['episodes'][1]['episode_id']
    with pytest.raises(ContractError) as error:
        explore(*source)
    assert error.value.code == 'EXPLORATION_ROW_INVALID'


def test_intervals_count_overlap_once():
    assert union_intervals([(0, 2000), (500, 2500), (3000, 4000), (3500, 4500)]) == [[0, 2500], [3000, 4500]]


def test_html_escapes_report_and_video_url(source):
    result = explore(*source)
    result['source_file'] = '</script><script>alert(1)</script>'
    output = render_html(result, 'test.mp4" onerror="alert(2)')
    assert '</script><script>alert(1)' not in output
    assert '\\u003c/script\\u003e' in output
    assert 'src="test.mp4&quot; onerror=&quot;alert(2)"' in output
    # The payload survives the safe script serialization without changing data.
    payload = output.split('<script type="application/json" id="report-data">')[1].split('</script>')[0]
    assert json.loads(payload)['source_file'] == result['source_file']


def test_declared_or_external_encoder_not_executed_is_rejected(source):
    source[0]['encoder_evidence']['vector_production'] = 'declared_external'
    with pytest.raises(ContractError) as error:
        explore(*source)
    assert error.value.code == 'EXPLORATION_ENCODER_UNVERIFIED'
