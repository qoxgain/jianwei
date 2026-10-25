"""Acquisition isolation tests use fabricated fixtures, never real-video evidence."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
import yaml

from infra.database import Database
from infra.models import Base, ImportJob, SourceSplit
from packages.config.errors import ContractError
from packages.config.loader import load_bundle
from packages.contracts.dataset import DatasetManifest
from packages.contracts.learning import FeatureSpace
from packages.contracts.temporal import TemporalSettings
from packages.temporal.data import snapshot_imports
from packages.temporal.provenance import source_provenance, assert_evaluation_independent


ROOT = Path(__file__).resolve().parents[1]


def reviewed():
    return {'status': 'confirmed', 'reviewer': 'fixture_reviewer',
            'reviewed_at': '2026-10-25T10:00:00+00:00',
            'method': 'fixture-only capture log and visual review',
            'notes': 'Fabricated fixture evidence; not a real acquisition.'}


def source(number, split='train', session=None):
    digest = f'{number:064x}'
    return {'source_sha256': digest, 'root_source_sha256': digest,
            'relation': 'original', 'capture_session_id': session or f'session_{number}',
            'camera_id': 'camera_1', 'view_id': 'view_1',
            'captured_at': f'2026-10-{number:02d}T10:00:00+00:00', 'split': split,
            'provenance_review': reviewed(), 'near_duplicate_review': reviewed()}


def dataset(sources=None, pairs=None):
    return {'dataset_id': 'fixture_dataset', 'tenant_id': 'tenant_demo',
            'sources': sources or [source(1), source(2), source(3, 'calibration'), source(4, 'test')],
            'duplicate_reviews': pairs or []}


def bundle(raw=None):
    settings = TemporalSettings(dataset_manifest=DatasetManifest.model_validate(raw) if raw else None)
    return SimpleNamespace(config=SimpleNamespace(temporal=settings),
                           profile=SimpleNamespace(tenant_id='tenant_demo', mode='demo'))


def imported(item):
    space = FeatureSpace(contract_version='3.1', encoder_sha='a'*64, preprocessing_sha='b'*64,
                         sampling_sha='c'*64, roi_policy_hash='d'*64, pooling='test-only', dimension=8)
    return {'manifest_version': '3.1', 'source_hash': item['source_sha256'],
            'source_group_id': item['source_sha256'], 'split': item['split'],
            'view_id': item['view_id'], 'tenant_id': 'tenant_demo', 'scene_id': 'anonymous_observation',
            'data_origin': 'real_video', 'source_verification': 'verified', 'total_rows': 30,
            'eligible_rows': 30, 'feature_cache': {'sha256': 'e'*64},
            'feature_space': space.model_dump(), 'feature_space_id': space.identifier(),
            'episodes': [{'complete': True}], 'encoder_evidence': {'scope': 'test_fixture'}}


def insert_sources(database, sources):
    with database.session() as session:
        for index, item in enumerate(sources):
            manifest = imported(item)
            session.add(SourceSplit(id=item['source_sha256'], tenant_id='tenant_demo',
                                    source_hash=item['source_sha256'], split=item['split']))
            session.add(ImportJob(id=f'import_{index}', tenant_id='tenant_demo',
                scene_id='anonymous_observation', idempotency_key=item['source_sha256'],
                config_hash='f'*64, knowledge_hash='a'*64, request_json='{}', config_snapshot_json='{}',
                status='succeeded', manifest_json=json.dumps(manifest)))


@pytest.fixture
def database():
    database = Database('sqlite:///:memory:')
    Base.metadata.create_all(database.engine)
    yield database
    database.close()


def test_four_independent_sessions_pin_reviewed_lineage_in_snapshots(database):
    raw = dataset(); b = bundle(raw); insert_sources(database, raw['sources'])
    train = snapshot_imports(b, database, 'anonymous_observation',
                             ['import_0', 'import_1', 'import_2'], {'train', 'calibration'})
    test = snapshot_imports(b, database, 'anonymous_observation', ['import_3'], {'test'})
    assert train['source_counts'] == {'train': 2, 'calibration': 1}
    assert {e['provenance']['capture_session_id'] for e in train['entries']} == {'session_1', 'session_2', 'session_3'}
    assert len({e['provenance']['dataset_manifest_sha256'] for e in [*train['entries'], *test['entries']]}) == 1
    assert_evaluation_independent(test, train)


def test_transcoded_and_original_imports_cannot_manufacture_independent_sources(database):
    original = source(1); derived = deepcopy(original)
    derived.update(source_sha256=f'{2:064x}', parent_source_sha256=original['source_sha256'], relation='transcode')
    raw = dataset([original, derived, source(3, 'calibration')]); insert_sources(database, raw['sources'])
    with pytest.raises(ContractError) as error:
        snapshot_imports(bundle(raw), database, 'anonymous_observation',
                         ['import_0', 'import_1', 'import_2'], {'train', 'calibration'})
    assert error.value.code == 'TEMPORAL_DUPLICATE_SESSION'


def test_separate_files_from_one_session_are_not_independent(database):
    raw = dataset([source(1), source(2, session='session_1'), source(3, 'calibration')])
    insert_sources(database, raw['sources'])
    with pytest.raises(ContractError) as error:
        snapshot_imports(bundle(raw), database, 'anonymous_observation',
                         ['import_0', 'import_1', 'import_2'], {'train', 'calibration'})
    assert error.value.code == 'TEMPORAL_DUPLICATE_SESSION'


@pytest.mark.parametrize('mutation', ['cross_session_split', 'derived_split', 'missing_parent',
                                      'wrong_root', 'cycle', 'derived_camera', 'naive_time', 'unknown_session'])
def test_invalid_lineage_and_acquisition_is_rejected(mutation):
    original = source(1); derived = deepcopy(original)
    derived.update(source_sha256=f'{2:064x}', parent_source_sha256=original['source_sha256'], relation='clip')
    raw = dataset([original, derived, source(3, 'test')])
    if mutation == 'cross_session_split': raw['sources'][2]['capture_session_id'] = 'session_1'
    if mutation == 'derived_split': derived['split'] = 'calibration'
    if mutation == 'missing_parent': derived['parent_source_sha256'] = 'f'*64
    if mutation == 'wrong_root': derived['root_source_sha256'] = 'e'*64
    if mutation == 'cycle': derived['parent_source_sha256'] = derived['source_sha256']
    if mutation == 'derived_camera': derived['camera_id'] = 'different_camera'
    if mutation == 'naive_time': original['captured_at'] = '2026-10-01T10:00:00'
    if mutation == 'unknown_session': original['capture_session_id'] = None
    with pytest.raises(ValidationError): DatasetManifest.model_validate(raw)


@pytest.mark.parametrize('mutation,code', [
    ('missing', 'TEMPORAL_PROVENANCE_REQUIRED'), ('unknown', 'TEMPORAL_PROVENANCE_REQUIRED'),
    ('pending', 'TEMPORAL_PROVENANCE_PENDING'), ('duplicate_pending', 'TEMPORAL_PROVENANCE_PENDING'),
    ('tenant', 'TEMPORAL_PROVENANCE_SCOPE'), ('view', 'TEMPORAL_PROVENANCE_SCOPE'),
    ('split', 'TEMPORAL_PROVENANCE_SPLIT'), ('pair_pending', 'TEMPORAL_DUPLICATE_REVIEW_PENDING')])
def test_real_research_blocks_unreviewed_or_mismatched_evidence(mutation, code):
    raw = dataset(); m = imported(raw['sources'][0])
    if mutation == 'unknown': m['source_hash'] = 'f'*64
    if mutation == 'pending': raw['sources'][0]['provenance_review'] = {'status': 'pending'}
    if mutation == 'duplicate_pending': raw['sources'][0]['near_duplicate_review'] = {'status': 'pending'}
    if mutation == 'tenant': raw['tenant_id'] = 'another_tenant'
    if mutation == 'view': m['view_id'] = 'another_view'
    if mutation == 'split': m['split'] = 'test'
    if mutation == 'pair_pending': raw['duplicate_reviews'] = [{
        'left_sha256': raw['sources'][0]['source_sha256'], 'right_sha256': raw['sources'][3]['source_sha256']}]
    with pytest.raises(ContractError) as error:
        source_provenance(bundle(None if mutation == 'missing' else raw), m)
    assert error.value.code == code


def test_reviewed_child_does_not_hide_pending_original():
    original = source(1); child = deepcopy(original)
    child.update(source_sha256=f'{2:064x}', parent_source_sha256=original['source_sha256'], relation='clip')
    original['near_duplicate_review'] = {'status': 'pending'}
    with pytest.raises(ContractError) as error:
        source_provenance(bundle(dataset([original, child])), imported(child))
    assert error.value.code == 'TEMPORAL_PROVENANCE_PENDING'


@pytest.mark.parametrize('key', ['capture_session_id', 'root_source_sha256'])
def test_evaluation_rechecks_root_and_session_against_frozen_model(key):
    raw = dataset(); b = bundle(raw)
    fitted = {'data_origin': 'real_video', 'entries': [{'source_hash': raw['sources'][0]['source_sha256'],
               'provenance': source_provenance(b, imported(raw['sources'][0]))}]}
    test = {'data_origin': 'real_video', 'entries': [{'source_hash': raw['sources'][3]['source_sha256'],
             'provenance': source_provenance(b, imported(raw['sources'][3]))}]}
    test['entries'][0]['provenance'][key] = fitted['entries'][0]['provenance'][key]
    with pytest.raises(ContractError) as error: assert_evaluation_independent(test, fitted)
    assert error.value.code == 'TEMPORAL_TEST_LEAKAGE'


def test_dataset_cannot_change_after_training_before_sealed_evaluation():
    raw = dataset(); b = bundle(raw)
    fitted = {'data_origin': 'real_video', 'entries': [{'source_hash': raw['sources'][0]['source_sha256'],
               'provenance': source_provenance(b, imported(raw['sources'][0]))}]}
    raw['sources'][3]['provenance_review']['notes'] += ' Changed review.'
    test = {'data_origin': 'real_video', 'entries': [{'source_hash': raw['sources'][3]['source_sha256'],
             'provenance': source_provenance(bundle(raw), imported(raw['sources'][3]))}]}
    with pytest.raises(ContractError) as error: assert_evaluation_independent(test, fitted)
    assert error.value.code == 'TEMPORAL_DATASET_CHANGED'


def test_optional_manifest_preserves_config_hash_and_pins_review_when_present(tmp_path):
    shutil.copytree(ROOT/'config', tmp_path/'config'); shutil.copytree(ROOT/'knowledge', tmp_path/'knowledge')
    path = tmp_path/'config/system.yaml'; raw = yaml.safe_load(path.read_text())
    old = load_bundle(path)
    raw['temporal']['dataset_manifest'] = None; path.write_text(yaml.safe_dump(raw, allow_unicode=True))
    assert load_bundle(path).config_hash == old.config_hash
    raw['temporal']['dataset_manifest'] = dataset(); path.write_text(yaml.safe_dump(raw, allow_unicode=True))
    new = load_bundle(path)
    assert new.config_hash != old.config_hash
    assert isinstance(new.snapshot['temporal']['dataset_manifest']['sources'][0]['captured_at'], str)


def test_single_video_template_stays_unassigned_and_pending():
    raw = yaml.safe_load((ROOT/'examples/test-source-dataset.yaml').read_text())
    registry = DatasetManifest.model_validate(raw)
    assert len(registry.sources) == 1 and registry.sources[0].split == 'unassigned'
    assert registry.sources[0].capture_session_id is None
    assert registry.sources[0].provenance_review.status == 'pending'
    assert registry.sources[0].near_duplicate_review.status == 'pending'
