"""Camera/session contracts use fabricated metadata, never attest actual recordings."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from packages.config.errors import ContractError
from packages.config.loader import load_bundle
from packages.contracts.capture import Acquisition, CaptureIdentity, ViewInterval
from packages.contracts.configuration import SourceBinding
from packages.contracts.ingestion import ImportRequest
from packages.contracts.scenes import EpisodePolicy
from packages.ingestion.capture import feature_window, reviewed_intervals, roi_identity, view_identity
from packages.ingestion.time import build_episodes
from packages.temporal.provenance import source_provenance


def source(identifier='station_1', identity=None):
    return SourceBinding(id=identifier, type='file', uri='fixture.mp4',
        scene_ids=['anonymous_observation'], roi_xyxy=[0, 0, 1, 1], capture_identity=identity)


def request(**extra):
    return ImportRequest(upload_id='fixture', format='mp4', source_id='station_1',
                         data_origin='real_video', **extra)


def test_unknown_views_are_isolated_by_station_and_bytes_without_inventing_camera():
    a = source()
    assert a.capture_identity is None
    assert view_identity(a, 'a' * 64, True) != view_identity(a, 'b' * 64, True)
    assert view_identity(a, 'a' * 64, True) != view_identity(source('station_2'), 'a' * 64, True)
    pending = source(identity=CaptureIdentity(physical_camera_id='unconfirmed', stable_view_id='guess'))
    assert view_identity(pending, 'a' * 64, True) == view_identity(a, 'a' * 64, True)


def test_confirmed_fixed_camera_view_survives_independent_file_names_and_sessions():
    identity = CaptureIdentity(physical_camera_id='camera_1', stable_view_id='fixed_view', review_status='confirmed')
    first, next_day = source('station_1', identity), source('station_1_day2', identity)
    assert view_identity(first, 'a' * 64, True) == view_identity(next_day, 'b' * 64, True)
    assert roi_identity(first) == roi_identity(next_day)
    moved = source('station_1', identity.model_copy(update={'stable_view_id': 'moved_view'}))
    assert view_identity(first, 'a' * 64, True) != view_identity(moved, 'a' * 64, True)
    cropped = first.model_copy(update={'roi_xyxy': [0, 0, 0.5, 1]})
    assert view_identity(first, 'a' * 64, True) == view_identity(cropped, 'a' * 64, True)
    assert roi_identity(first) != roi_identity(cropped)


@pytest.mark.parametrize('factory,fields', [
    (CaptureIdentity, {'review_status': 'confirmed'}),
    (CaptureIdentity, {'physical_camera_id': 'camera', 'review_status': 'confirmed'}),
    (Acquisition, {'review_status': 'confirmed', 'capture_session_id': 'session'}),
    (Acquisition, {'review_status': 'confirmed', 'capture_session_id': 'session', 'captured_at': '2026-10-27T00:00:00'}),
    (ViewInterval, {'domain_index': 0, 'start_ms': 0, 'end_ms': 1000, 'status': 'usable', 'reason': 'stable'}),
])
def test_confirmation_requires_real_identity_timezone_and_reviewer(factory, fields):
    with pytest.raises(ValidationError):
        factory(**fields)


def test_review_intervals_cover_all_original_time_domains_and_preserve_unknown_gaps():
    domains = [{'domain_index': 0, 'start_ms': 0, 'end_ms': 10000},
               {'domain_index': 1, 'start_ms': 0, 'end_ms': 5000}]
    metadata = request(view_intervals=[
        {'domain_index': 0, 'start_ms': 1000, 'end_ms': 3000, 'status': 'usable', 'reason': 'fixture stable', 'reviewer': 'reviewer'},
        {'domain_index': 0, 'start_ms': 3000, 'end_ms': 5000, 'status': 'excluded', 'reason': 'fixture camera movement', 'reviewer': 'reviewer'},
    ])
    before = deepcopy(domains)
    result = reviewed_intervals(metadata, domains)
    assert [(r['domain_index'], r['start_ms'], r['end_ms'], r['status']) for r in result] == [
        (0, 0, 1000, 'unreviewed'), (0, 1000, 3000, 'usable'), (0, 3000, 5000, 'excluded'),
        (0, 5000, 10000, 'unreviewed'), (1, 0, 5000, 'unreviewed')]
    assert domains == before  # Camera movement must not invent a PTS reset/domain.


@pytest.mark.parametrize('intervals,code', [
    ([(0, 0, 12000)], 'VIEW_INTERVAL_RANGE'),
    ([(1, 0, 1000)], 'VIEW_INTERVAL_DOMAIN'),
    ([(0, 0, 5000), (0, 4000, 6000)], 'VIEW_INTERVAL_RANGE'),
])
def test_view_review_rejects_unknown_domains_overlap_and_outside_range(intervals, code):
    supplied = [{'domain_index': d, 'start_ms': a, 'end_ms': b, 'status': 'unreviewed', 'reason': 'fixture'}
                for d, a, b in intervals]
    with pytest.raises(ContractError) as invalid:
        reviewed_intervals(request(view_intervals=supplied), [{'domain_index': 0, 'start_ms': 0, 'end_ms': 10000}])
    assert invalid.value.code == code


def test_partial_observation_requires_explicit_version_and_does_not_claim_business_cycle():
    spec = load_bundle().scenes['anonymous_observation'].model_copy(deep=True)
    domain = [{'domain_index': 0, 'time_domain_id': 'd0', 'start_ms': 0, 'end_ms': 20440}]
    legacy = build_episodes(spec, request(), domain)[0]
    assert legacy['complete'] is False and not feature_window(legacy)
    with pytest.raises(ValidationError):
        EpisodePolicy(mode='fixed_window', window_seconds=60, partial_window='extract_only')
    spec.episode_policy = EpisodePolicy(mode='fixed_window', window_seconds=60,
        observation_contract='p033-observation-v1', partial_window='extract_only')
    observation = build_episodes(spec, request(), domain)[0]
    assert observation['end_ms'] == 20440 and observation['complete'] is False
    assert observation['kind'] == 'observation_window' and observation['business_cycle_verified'] is False
    assert feature_window(observation) is True
    assert spec.episode_policy.window_seconds == 60


@pytest.mark.parametrize('review_status,interval_status,code', [
    ('pending', 'usable', 'TEMPORAL_CAPTURE_PENDING'),
    ('confirmed', 'unreviewed', 'TEMPORAL_VIEW_REVIEW_PENDING'),
])
def test_real_import_research_remains_blocked_by_unknown_capture_or_view_review(review_status, interval_status, code):
    manifest = {'data_origin': 'real_video', 'capture_contract': 'p033-capture-v1',
        'capture_identity': {'review_status': review_status, 'physical_camera_id': 'camera', 'stable_view_id': 'stable'},
        'acquisition': {'review_status': review_status, 'capture_session_id': 'session', 'captured_at': '2026-10-27T00:00:00Z'},
        'view_review': [{'status': interval_status}]}
    from packages.knowledge.service import content_hash
    manifest['view_id'] = content_hash({'contract': 'physical-view-v1', 'physical_camera_id': 'camera', 'stable_view_id': 'stable'})
    with pytest.raises(ContractError) as blocked:
        source_provenance(load_bundle(), manifest)
    assert blocked.value.code == code
