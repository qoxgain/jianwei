"""Adversarial NPZ input and clock/episode boundaries for P0.2 imports."""
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest
from pydantic import ValidationError

from packages.config.errors import ContractError
from packages.contracts.ingestion import ClockAnchor, ImportRequest, IngestionSettings
from packages.contracts.scenes import EpisodePolicy
from packages.ingestion.npz import read_npz
from packages.ingestion.time import build_episodes, time_domains


def write_npz(path, **changes):
    data = {'features': np.ones((3, 2), dtype=np.float32),
            'pts_ms': np.array([0, 1000, 2000], dtype=np.int64),
            'durations_ms': np.full(3, 1000, dtype=np.int64)}
    data.update(changes)
    np.savez_compressed(path, **data)
    return path


def expect_code(code, fn):
    with pytest.raises(ContractError) as caught:
        fn()
    assert caught.value.code == code


@pytest.mark.parametrize('dtype', [np.float32, np.float64])
def test_numeric_npz_survives_archive_close_and_preserves_precision(tmp_path, dtype):
    original = np.array([[1.0, 2.5], [3.0, -4.0], [5.0, 6.0]], dtype=dtype)
    path = write_npz(tmp_path / 'features.npz', features=original)
    features, pts, durations = read_npz(path, IngestionSettings(), 2)
    path.unlink()
    np.testing.assert_array_equal(features, original)
    assert features.dtype == dtype
    np.testing.assert_array_equal(pts, [0, 1000, 2000])
    np.testing.assert_array_equal(durations, [1000, 1000, 1000])


@pytest.mark.parametrize('array,code', [
    (np.array([[{'payload': 'not executed'}] * 2] * 3, dtype=object), 'NPZ_DTYPE'),
    (np.zeros((3, 2), dtype=[('value', 'f4')]), 'NPZ_DTYPE'),
    (np.ones((3, 2), dtype=np.complex64), 'NPZ_SHAPE_DTYPE'),
    (np.ones((3, 2), dtype=np.int32), 'NPZ_SHAPE_DTYPE'),
    (np.ones((3, 2), dtype=np.float16), 'NPZ_SHAPE_DTYPE'),
    (np.asfortranarray(np.ones((3, 2))), 'NPZ_DTYPE'),
])
def test_unsafe_or_unsupported_array_is_rejected_before_numpy_load(tmp_path, monkeypatch, array, code):
    path = write_npz(tmp_path / 'bad.npz', features=array)
    def no_load(*args, **kwargs):
        raise AssertionError('Unsafe archive reached numpy.load')
    monkeypatch.setattr(np, 'load', no_load)
    expect_code(code, lambda: read_npz(path, IngestionSettings(), 2))


@pytest.mark.parametrize('field,value', [
    ('features', np.array([[1., np.nan], [1., 2.], [3., 4.]])),
    ('features', np.array([[1., np.inf], [1., 2.], [3., 4.]])),
    ('pts_ms', np.array([0., np.nan, 2000.])),
    ('durations_ms', np.array([1000., 1000., np.inf])),
])
def test_nonfinite_values_never_enter_feature_cache(tmp_path, field, value):
    path = write_npz(tmp_path / 'bad.npz', **{field: value})
    expect_code('NPZ_NONFINITE', lambda: read_npz(path, IngestionSettings(), 2))


@pytest.mark.parametrize('changes,dimension,code', [
    ({}, 3, 'NPZ_SHAPE_DTYPE'),
    ({'features': np.ones((3,), dtype=np.float32)}, 2, 'NPZ_SHAPE_DTYPE'),
    ({'pts_ms': np.array([0, 1000])}, 2, 'NPZ_ROW_MISMATCH'),
    ({'durations_ms': np.array([[1000, 1000, 1000]])}, 2, 'NPZ_SHAPE_DTYPE'),
    ({'features': np.ones((0, 2), dtype=np.float32)}, 2, 'NPZ_ROW_LIMIT'),
    ({'pts_ms': np.array([False, True, True])}, 2, 'NPZ_SHAPE_DTYPE'),
])
def test_shape_dimension_and_row_contracts(tmp_path, changes, dimension, code):
    path = write_npz(tmp_path / 'bad.npz', **changes)
    expect_code(code, lambda: read_npz(path, IngestionSettings(), dimension))


@pytest.mark.parametrize('field,values', [
    ('pts_ms', [0., 1000.5, 2000.]),
    ('pts_ms', [-1, 1000, 2000]),
    ('pts_ms', [0, 1000, 2**53]),
    ('durations_ms', [1000., 0.5, 1000.]),
    ('durations_ms', [1000, 0, 1000]),
    ('durations_ms', [1000, -1, 1000]),
    ('durations_ms', [1000, 600001, 1000]),
])
def test_invalid_times_are_rejected(tmp_path, field, values):
    path = write_npz(tmp_path / 'bad.npz', **{field: np.asarray(values)})
    expect_code('NPZ_TIME', lambda: read_npz(path, IngestionSettings(), 2))


def npy_bytes(array):
    output = BytesIO()
    np.save(output, array, allow_pickle=False)
    return output.getvalue()


@pytest.mark.parametrize('member', ['features.npy', '../outside.npy', 'metadata.json'])
def test_duplicate_or_extra_zip_members_are_rejected(tmp_path, member):
    path = write_npz(tmp_path / 'bad.npz')
    with zipfile.ZipFile(path, 'a') as archive:
        if member == 'features.npy':
            with pytest.warns(UserWarning, match='Duplicate name'):
                archive.writestr(member, b'bad')
        else:
            archive.writestr(member, b'bad')
    expect_code('NPZ_MEMBERS', lambda: read_npz(path, IngestionSettings(), 2))
    assert not (tmp_path.parent / 'outside.npy').exists()


def test_huge_npy_header_is_rejected_before_allocation(tmp_path, monkeypatch):
    header = BytesIO()
    # 100,000 rows are within the row limit, but 8 GB exceeds the expanded limit.
    np.lib.format.write_array_header_1_0(header, {'descr': '<f8', 'fortran_order': False,
                                                'shape': (100000, 10000)})
    path = tmp_path / 'header-bomb.npz'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('features.npy', header.getvalue())
        archive.writestr('pts_ms.npy', npy_bytes(np.array([0])))
        archive.writestr('durations_ms.npy', npy_bytes(np.array([1000])))
    monkeypatch.setattr(np, 'load', lambda *args, **kwargs: pytest.fail('Oversized NPY must fail before allocation'))
    expect_code('NPZ_HEADER_SIZE', lambda: read_npz(path, IngestionSettings(), 10000))


def test_compressed_size_does_not_bypass_expanded_limit(tmp_path):
    path = write_npz(tmp_path / 'compressed.npz', features=np.zeros((200, 2), dtype=np.float32),
                     pts_ms=np.arange(200), durations_ms=np.ones(200))
    assert path.stat().st_size < 1024
    expect_code('NPZ_SIZE_LIMIT', lambda: read_npz(path, IngestionSettings(max_expanded_bytes=1024), 2))


def test_row_limit_is_enforced_before_loading(tmp_path):
    path = write_npz(tmp_path / 'many.npz')
    expect_code('NPZ_ROW_LIMIT', lambda: read_npz(path, IngestionSettings(max_rows=2), 2))


def domains(pts=(0, 1000, 6000, 7000, 0, 1000), anchors=()):
    return time_domains(np.asarray(pts), np.full(len(pts), 1000), IngestionSettings(max_gap_ms=1000), anchors)


def test_source_gap_and_reset_preserve_input_order_and_create_distinct_domains():
    groups, gaps, rows = domains()
    assert [(x['start_ms'], x['end_ms']) for x in groups] == [(0, 2000), (6000, 8000), (0, 2000)]
    assert rows == [0, 0, 1, 1, 2, 2]
    assert [x['reason'] for x in gaps] == ['source_gap', 'pts_reset']
    assert len({x['time_domain_id'] for x in groups}) == 3
    assert all(x['capture_start_utc'] is None and x['clock_status'] == 'unknown' for x in groups)


def test_equal_pts_creates_new_domain_instead_of_sorting_or_deduplicating():
    groups, gaps, rows = domains((0, 1000, 1000, 2000))
    assert len(groups) == 2
    assert gaps[0]['reason'] == 'pts_reset'
    assert rows == [0, 0, 1, 1]


def test_clock_anchor_is_normalized_to_utc_without_leaking_into_other_domains():
    anchor = ClockAnchor(domain_index=0, pts_ms=1000, capture_utc='2026-10-23T08:00:01+08:00')
    groups, _, _ = domains(anchors=[anchor])
    assert groups[0]['capture_start_utc'] == '2026-10-23T00:00:00+00:00'
    assert groups[1]['capture_start_utc'] is None
    assert groups[2]['capture_start_utc'] is None


@pytest.mark.parametrize('domain,pts,code', [(4, 0, 'CLOCK_DOMAIN'), (0, 3000, 'CLOCK_ANCHOR_RANGE')])
def test_clock_anchor_cannot_reference_missing_or_out_of_range_domain(domain, pts, code):
    anchor = ClockAnchor(domain_index=domain, pts_ms=pts, capture_utc='2026-10-23T00:00:00Z')
    expect_code(code, lambda: domains(anchors=[anchor]))


def test_clock_without_timezone_is_rejected():
    with pytest.raises(ValidationError, match='时区'):
        ClockAnchor(domain_index=0, pts_ms=0, capture_utc='2026-10-23T00:00:00')


def test_time_domain_duration_limit():
    expect_code('DURATION_LIMIT', lambda: time_domains(np.array([0, 1000]), np.array([1000, 1000]),
                IngestionSettings(max_duration_seconds=1), []))


def request(**kwargs):
    return ImportRequest(upload_id='upload', format='mp4', source_id='file_01', data_origin='real_video', **kwargs)


def spec(mode='manual_cycle', window=None):
    return SimpleNamespace(episode_policy=EpisodePolicy(mode=mode, window_seconds=window))


def episode(identifier='cycle', domain=0, start=0, end=2000):
    return {'episode_id': identifier, 'domain_index': domain, 'start_ms': start, 'end_ms': end}


@pytest.mark.parametrize('mode,origin', [('manual_cycle', 'manual'), ('external_cycle', 'external'),
                                       ('manual_or_external_cycle', 'external')])
def test_complete_cycles_are_independent_even_with_repeated_pts_after_reset(mode, origin):
    groups, _, _ = domains()
    result = build_episodes(spec(mode), request(episode_origin=origin,
                episodes=[episode('first', 0), episode('after-reset', 2)]), groups)
    assert [item['time_domain_id'] for item in result] == ['d0', 'd2']
    assert all(item['complete'] for item in result)


@pytest.mark.parametrize('mode,origin', [('manual_cycle', 'external'), ('external_cycle', 'manual'),
                                       ('manual_or_external_cycle', None)])
def test_cycle_origin_policy_is_enforced(mode, origin):
    groups, _, _ = domains()
    expect_code('EPISODE_ORIGIN', lambda: build_episodes(spec(mode),
                request(episode_origin=origin, episodes=[episode()]), groups))


@pytest.mark.parametrize('item,code', [(episode(end=7000), 'EPISODE_RANGE'),
                                     (episode(domain=8), 'EPISODE_DOMAIN')])
def test_cycle_cannot_cross_gap_or_reference_missing_domain(item, code):
    groups, _, _ = domains()
    expect_code(code, lambda: build_episodes(spec(), request(episode_origin='manual', episodes=[item]), groups))


def test_manual_cycle_overlap_is_rejected_not_silently_trimmed():
    groups, _, _ = domains()
    expect_code('EPISODE_OVERLAP', lambda: build_episodes(spec(), request(episode_origin='manual',
                episodes=[episode('a', end=1500), episode('b', start=1000)]), groups))


def test_manual_policy_cannot_invent_cycles_when_no_boundaries_are_given():
    groups, _, _ = domains()
    expect_code('EPISODES_REQUIRED', lambda: build_episodes(spec(), request(episode_origin='manual'), groups))


def test_fixed_windows_restart_at_domain_boundary_and_mark_partial_window():
    groups, _, _ = domains()
    result = build_episodes(spec('fixed_window', 1.5), request(), groups)
    assert len(result) == 6
    for domain_index in range(3):
        windows = [item for item in result if item['domain_index'] == domain_index]
        assert [item['complete'] for item in windows] == [True, False]
        assert windows[0]['start_ms'] == groups[domain_index]['start_ms']
        assert windows[-1]['end_ms'] == groups[domain_index]['end_ms']


def test_fixed_window_cannot_accept_manual_cycle_overrides():
    groups, _, _ = domains()
    expect_code('EPISODE_POLICY', lambda: build_episodes(spec('fixed_window', 1.),
                request(episode_origin='manual', episodes=[episode()]), groups))


def test_manual_or_fixed_window_requires_explicit_manual_origin_for_supplied_cycles():
    groups, _, _ = domains()
    expect_code('EPISODE_ORIGIN', lambda: build_episodes(spec('manual_or_fixed_window', 1.),
                request(episode_origin='external', episodes=[episode()]), groups))


def test_submillisecond_window_is_rejected_before_nonadvancing_loop():
    groups, _, _ = domains()
    expect_code('EPISODE_WINDOW_TOO_SMALL', lambda: build_episodes(spec('fixed_window', 0.0001),
                request(), groups))


def test_automatic_windows_cannot_expand_beyond_explicit_episode_limit():
    groups, _, _ = time_domains(np.array([0, 10000]), np.array([10000, 10000]),
                               IngestionSettings(), [])
    expect_code('EPISODE_COUNT_LIMIT', lambda: build_episodes(spec('fixed_window', 0.001),
                request(), groups))
