"""Fabricated acquisition reviews verify admission rules, never actual video truth."""
from copy import deepcopy
import pytest
from packages.config.errors import ContractError
from packages.knowledge.service import content_hash
from packages.temporal.provenance import source_provenance
from tests.test_dataset_provenance import source, imported, dataset, bundle


def fixture():
    original = source(1)
    identity = {'physical_camera_id': original['camera_id'], 'stable_view_id': 'fixture_stable_view',
                'review_status': 'confirmed'}
    original['view_id'] = content_hash({'contract': 'physical-view-v1',
        'physical_camera_id': identity['physical_camera_id'], 'stable_view_id': identity['stable_view_id']})
    manifest = imported(original)
    manifest.update(capture_contract='p033-capture-v1', capture_identity=identity,
        acquisition={'capture_session_id': original['capture_session_id'],
            'captured_at': original['captured_at'], 'review_status': 'confirmed'},
        domains=[{'domain_index': 0, 'start_ms': 0, 'end_ms': 10000},
                 {'domain_index': 1, 'start_ms': 0, 'end_ms': 3000}],
        view_review=[{'domain_index': 0, 'start_ms': 0, 'end_ms': 4000, 'status': 'usable',
                     'reason': 'Fixture stable interval', 'reviewer': 'fixture_reviewer'},
                    {'domain_index': 0, 'start_ms': 4000, 'end_ms': 10000, 'status': 'excluded',
                     'reason': 'Fixture camera movement', 'reviewer': 'fixture_reviewer'},
                    {'domain_index': 1, 'start_ms': 0, 'end_ms': 3000, 'status': 'usable',
                     'reason': 'Fixture stable interval', 'reviewer': 'fixture_reviewer'}])
    return bundle(dataset([original])), manifest


def assert_blocked(b, manifest, code):
    with pytest.raises(ContractError) as error:
        source_provenance(b, manifest)
    assert error.value.code == code


def test_reviewed_capture_matches_registry_and_timezone_equivalent_instants():
    b, manifest = fixture()
    manifest['acquisition']['captured_at'] = '2026-10-01T18:00:00+08:00'
    before = deepcopy(manifest)
    record = source_provenance(b, manifest)
    assert record['capture_session_id'] == 'session_1'
    assert record['source']['camera_id'] == 'camera_1'
    assert manifest == before


@pytest.mark.parametrize('section,key,value', [
    ('acquisition', 'capture_session_id', 'different_session'),
    ('acquisition', 'captured_at', '2026-10-02T10:00:00+00:00'),
    ('capture_identity', 'physical_camera_id', 'different_camera'),
    ('capture_identity', 'stable_view_id', 'different_view'),
])
def test_confirmed_metadata_cannot_disagree_with_registered_capture(section, key, value):
    b, manifest = fixture()
    manifest[section][key] = value
    assert_blocked(b, manifest, 'TEMPORAL_PROVENANCE_SCOPE')


def test_changed_camera_and_matching_new_view_still_disagree_with_registry():
    b, manifest = fixture()
    manifest['capture_identity']['physical_camera_id'] = 'different_camera'
    manifest['view_id'] = content_hash({'contract': 'physical-view-v1', 'physical_camera_id': 'different_camera',
        'stable_view_id': manifest['capture_identity']['stable_view_id']})
    assert_blocked(b, manifest, 'TEMPORAL_PROVENANCE_SCOPE')


@pytest.mark.parametrize('section,key,value', [
    ('acquisition', 'capture_session_id', None),
    ('acquisition', 'captured_at', None),
    ('acquisition', 'captured_at', 'not-a-date'),
    ('acquisition', 'captured_at', '2026-10-01T10:00:00'),
    ('capture_identity', 'stable_view_id', None),
    ('capture_identity', 'physical_camera_id', None),
])
def test_confirmed_but_invalid_capture_fails_with_public_error(section, key, value):
    b, manifest = fixture()
    manifest[section][key] = value
    assert_blocked(b, manifest, 'TEMPORAL_CAPTURE_INVALID')


@pytest.mark.parametrize('mutation', ['missing_domain','gap','overlap','outside','unknown_domain','no_reviewer','unreviewed','empty_reason','missing_domains'])
def test_review_must_cover_each_original_time_domain_without_gaps_or_overlap(mutation):
    b, manifest = fixture()
    intervals = manifest['view_review']
    if mutation == 'missing_domain': intervals.pop()
    elif mutation == 'gap': intervals[1]['start_ms'] = 5000
    elif mutation == 'overlap': intervals[1]['start_ms'] = 3000
    elif mutation == 'outside': intervals[1]['end_ms'] = 11000
    elif mutation == 'unknown_domain': intervals[2]['domain_index'] = 2
    elif mutation == 'no_reviewer': intervals[0]['reviewer'] = None
    elif mutation == 'unreviewed': intervals[0]['status'] = 'unreviewed'
    elif mutation == 'empty_reason': intervals[0]['reason'] = '   '
    else: manifest.pop('domains')
    assert_blocked(b, manifest, 'TEMPORAL_VIEW_REVIEW_PENDING')
