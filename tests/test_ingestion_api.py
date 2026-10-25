"""Actual durable import API checks; fixtures are explicit synthetic features."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import asyncio
import hashlib
import json
import shutil
import time

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.requests import ClientDisconnect

from apps.api.main import create_app
from infra.models import AuditEvent, ImportJob, SourceSplit, UploadedArtifact
from packages.config.loader import load_bundle
from packages.contracts.ingestion import ImportRequest
from packages.ingestion_examples import build_example
from packages.knowledge.service import content_hash

ROOT = Path(__file__).resolve().parents[1]
SCENES = ('garment_sop', 'electronics_assembly', 'workshop_safety', 'anonymous_observation')


@pytest.fixture
def import_config(tmp_path):
    (tmp_path / 'config').mkdir()
    shutil.copytree(ROOT / 'knowledge', tmp_path / 'knowledge')
    path = tmp_path / 'config/system.yaml'
    path.write_text((ROOT / 'config/system.yaml').read_text())
    return path


@pytest.fixture
def import_api(import_config):
    app = create_app(import_config)
    with TestClient(app) as client:
        yield client, app, import_config


def upload(client, content, kind):
    response = client.post('/api/uploads', params={'kind': kind}, content=content,
                           headers={'content-type': 'application/octet-stream'})
    assert response.status_code == 201, response.text
    return response.json()


def example_request(client, config, tmp_path, scene='anonymous_observation'):
    fixture = build_example(load_bundle(config), scene, tmp_path)
    source = upload(client, fixture['input_path'].read_bytes(), 'npz')
    encoder = upload(client, fixture['encoder_path'].read_bytes(), 'encoder')
    return fixture['request_fields'] | {'upload_id': source['id'], 'encoder_artifact_id': encoder['id']}


def wait_job(client, scene, identifier):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f'/api/scenes/{scene}/imports/{identifier}')
        assert response.status_code == 200, response.text
        job = response.json()
        if job['status'] not in {'queued', 'running'}:
            return job
        time.sleep(0.02)
    pytest.fail(f'import {identifier} did not finish')


def enqueue(client, scene, body):
    result = client.post(f'/api/scenes/{scene}/imports', json=body)
    assert result.status_code == 202, result.text
    return result.json()


@pytest.mark.parametrize('scene', SCENES)
def test_real_worker_imports_examples_in_each_scene_without_labels(import_api, scene):
    client, app, _ = import_api
    result = client.post(f'/api/scenes/{scene}/imports/example')
    assert result.status_code == 202, result.text
    job = wait_job(client, scene, result.json()['id'])
    assert job['status'] == 'succeeded', job['error']
    manifest = job['manifest']
    assert manifest['data_origin'] == 'synthetic_features'
    assert manifest['source_verification'] == 'synthetic'
    assert manifest['split'] == 'unassigned'
    assert manifest['feature_space']['dimension'] == 512
    assert manifest['feature_batches']
    assert all(batch['scene_id'] == scene and batch['tenant_id'] == 'tenant_demo' for batch in manifest['feature_batches'])
    assert manifest['feature_space_id'] and manifest['source_hash']
    assert str(app.state.bundle.root) not in json.dumps(manifest)
    response = client.get(f'/api/scenes/{scene}/imports/{job["id"]}/manifest')
    assert response.status_code == 200
    assert response.json() == manifest
    assert 'attachment' in response.headers['content-disposition']
    assert client.get('/api/health').json()['schema_version'] == '0004_temporal'
    capabilities = client.get(f'/api/scenes/{scene}').json()['capabilities']
    assert capabilities['video_ingest'] is True
    assert capabilities['training'] is True  # P0.3 is explicitly enabled in this fixture
    assert capabilities['authentication'] is False


def test_duplicate_concurrent_import_is_one_job_and_one_queue_event(import_api, tmp_path):
    client, app, config = import_api
    body = example_request(client, config, tmp_path / 'fixture')
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: enqueue(client, 'anonymous_observation', body), range(4)))
    assert len({job['id'] for job in jobs}) == 1
    assert sum(job['created'] for job in jobs) == 1
    job = wait_job(client, 'anonymous_observation', jobs[0]['id'])
    assert job['status'] == 'succeeded', job['error']
    with app.state.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ImportJob)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == 'import.queued')) == 1
        assert session.scalar(select(func.count()).select_from(UploadedArtifact)) == 2
    repeated = client.post('/api/scenes/anonymous_observation/imports/example').json()
    assert repeated['id'] == job['id'] and repeated['created'] is False


def test_upload_identity_actual_bytes_and_stream_limits(import_config):
    raw = yaml.safe_load(import_config.read_text())
    raw['ingestion']['max_upload_bytes'] = 1024
    import_config.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    app = create_app(import_config)
    with TestClient(app) as client:
        data = b'fingerprint-material'
        first = upload(client, data, 'encoder')
        assert first['sha256'] == hashlib.sha256(data).hexdigest()
        assert first['size_bytes'] == len(data)
        assert upload(client, data, 'encoder')['id'] == first['id']
        assert upload(client, data, 'npz')['id'] != first['id']
        result = client.post('/api/uploads?kind=npz', content=(b'x' * 700 for _ in range(2)), headers={'content-length': '1'})
        assert result.status_code == 413
        assert result.json()['detail']['code'] == 'UPLOAD_TOO_LARGE'
        assert list(app.state.imports.upload_root.glob('.upload-*')) == []
        assert client.post('/api/uploads?kind=encoder', content=b'').status_code == 422
        assert client.post('/api/uploads?kind=../../secret', content=b'x').status_code == 422
        assert client.post('/api/config/validate', content=b'x' * 530000).status_code == 413


def test_upload_disconnect_cleans_partial_file(import_api):
    _, app, _ = import_api
    class InterruptedRequest:
        async def stream(self):
            yield b'partial'
            raise ClientDisconnect()
    with pytest.raises(ClientDisconnect):
        asyncio.run(app.state.imports.upload(InterruptedRequest(), 'npz'))
    assert list(app.state.imports.upload_root.glob('.upload-*')) == []
    with app.state.database.session() as session:
        assert session.scalar(select(func.count()).select_from(UploadedArtifact)) == 0


def test_upload_bypasses_only_json_limit_and_preserves_full_file(import_api):
    client, _, _ = import_api
    data = b'ab' * 300000
    response = upload(client, data, 'encoder')
    assert response['size_bytes'] == len(data)
    assert response['sha256'] == hashlib.sha256(data).hexdigest()


def test_scope_isolation_and_input_cannot_supply_tenant(import_api, tmp_path):
    client, _, config = import_api
    body = example_request(client, config, tmp_path / 'fixture')
    job = enqueue(client, 'anonymous_observation', body)
    assert client.get(f'/api/scenes/workshop_safety/imports/{job["id"]}').status_code == 404
    assert client.get('/api/scenes/unknown/imports').status_code == 404
    assert client.get('/api/scenes/workshop_safety/imports').json() == {'items': []}
    assert client.post('/api/scenes/anonymous_observation/imports', json=body | {'tenant_id': 'other'}).status_code == 422
    assert client.post('/api/scenes/anonymous_observation/imports', json=body | {'upload_id': '../private'}).status_code == 422
    bad = body | {'upload_id': body['encoder_artifact_id']}
    response = client.post('/api/scenes/anonymous_observation/imports', json=bad)
    assert response.status_code == 422 and response.json()['detail']['code'] == 'UPLOAD_KIND_MISMATCH'


def test_other_tenant_cannot_reuse_upload_or_job_ids(import_config, tmp_path):
    with TestClient(create_app(import_config)) as client:
        body = example_request(client, import_config, tmp_path / 'fixture')
        job = enqueue(client, 'anonymous_observation', body)
        assert wait_job(client, 'anonymous_observation', job['id'])['status'] == 'succeeded'
    raw = yaml.safe_load(import_config.read_text())
    raw['profiles']['demo']['tenant_id'] = 'other_tenant'
    import_config.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    with TestClient(create_app(import_config)) as client:
        assert client.get('/api/scenes/anonymous_observation/imports').json() == {'items': []}
        assert client.get(f'/api/scenes/anonymous_observation/imports/{job["id"]}').status_code == 404
        denied = client.post('/api/scenes/anonymous_observation/imports', json=body)
        assert denied.status_code == 404
        assert denied.json()['detail']['code'] == 'UPLOAD_NOT_FOUND'
        own = example_request(client, import_config, tmp_path / 'second-fixture')
        assert own['upload_id'] != body['upload_id']


def test_pending_running_job_recovers_and_completed_identity_persists(import_config, tmp_path):
    app = create_app(import_config)
    with TestClient(app) as client:
        app.state.imports.close()
        body = example_request(client, import_config, tmp_path / 'fixture')
        queued = enqueue(client, 'anonymous_observation', body)
        with app.state.database.session() as session:
            session.get(ImportJob, queued['id']).status = 'running'
    with TestClient(create_app(import_config)) as restarted:
        recovered = wait_job(restarted, 'anonymous_observation', queued['id'])
        assert recovered['status'] == 'succeeded', recovered['error']
        assert enqueue(restarted, 'anonymous_observation', body)['id'] == recovered['id']
        events = restarted.get('/api/scenes/anonymous_observation/audit').json()['events']
        assert any(event['event_type'] == 'import.recovered' for event in events)
    with TestClient(create_app(import_config)) as restarted:
        assert restarted.get(f'/api/scenes/anonymous_observation/imports/{queued["id"]}').json() == recovered


def test_restart_with_changed_config_blocks_old_job_and_rejects_retry(import_config, tmp_path):
    app = create_app(import_config)
    with TestClient(app) as client:
        app.state.imports.close()
        body = example_request(client, import_config, tmp_path / 'fixture')
        queued = enqueue(client, 'anonymous_observation', body)
    raw = yaml.safe_load(import_config.read_text())
    raw['video']['quality_min'] = 0.7
    import_config.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    with TestClient(create_app(import_config)) as restarted:
        old = restarted.get(f'/api/scenes/anonymous_observation/imports/{queued["id"]}').json()
        assert old['status'] == 'blocked'
        assert old['error']['code'] == 'IMPORT_CONFIG_CHANGED'
        assert restarted.post(f'/api/scenes/anonymous_observation/imports/{old["id"]}/retry').status_code == 409
        new = enqueue(restarted, 'anonymous_observation', body)
        assert new['id'] != old['id']
        assert wait_job(restarted, 'anonymous_observation', new['id'])['status'] == 'succeeded'


def test_failed_job_keeps_id_and_can_retry_after_restoring_same_upload(import_api, tmp_path):
    client, app, config = import_api
    body = example_request(client, config, tmp_path / 'fixture')
    app.state.imports.close()
    queued = enqueue(client, 'anonymous_observation', body)
    with app.state.database.session() as session:
        artifact = session.get(UploadedArtifact, body['upload_id'])
        path = app.state.imports._path(artifact)
    original = path.read_bytes()
    path.write_bytes(b'corrupt')
    app.state.imports._process(queued['id'])
    failed = client.get(f'/api/scenes/anonymous_observation/imports/{queued["id"]}').json()
    assert failed['status'] == 'failed'
    assert failed['error']['code'] == 'UPLOAD_MISSING'
    path.write_bytes(original)
    retried = client.post(f'/api/scenes/anonymous_observation/imports/{queued["id"]}/retry')
    assert retried.status_code == 202 and retried.json()['id'] == queued['id']
    app.state.imports._process(queued['id'])
    assert client.get(f'/api/scenes/anonymous_observation/imports/{queued["id"]}').json()['status'] == 'succeeded'


def test_same_source_cannot_be_train_and_test_across_scenes(import_api, tmp_path):
    client, _, config = import_api
    body = example_request(client, config, tmp_path / 'fixture') | {'requested_split': 'train'}
    first = enqueue(client, 'anonymous_observation', body)
    trained = wait_job(client, 'anonymous_observation', first['id'])
    assert trained['status'] == 'succeeded', trained['error']
    other = enqueue(client, 'workshop_safety', body | {'requested_split': 'test'})
    conflict = wait_job(client, 'workshop_safety', other['id'])
    assert conflict['status'] == 'failed'
    assert conflict['error']['code'] == 'SOURCE_SPLIT_CONFLICT'
    assert trained['manifest']['split'] == 'train'


def test_declared_source_never_inherits_verified_group_split(import_api, tmp_path):
    client, app, config = import_api
    body = example_request(client, config, tmp_path / 'fixture')
    body.update(source_id='file_01', data_origin='real_video')
    body['feature_space']['roi_policy_hash'] = content_hash({'source_id': 'file_01', 'roi_xyxy': [0.0, 0.0, 1.0, 1.0]})
    digest = body['declared_source_hash']
    with app.state.database.session() as session:
        session.add(SourceSplit(id=content_hash({'tenant_id': 'tenant_demo', 'source_hash': digest}),
                                tenant_id='tenant_demo', source_hash=digest, split='train'))
    queued = enqueue(client, 'anonymous_observation', body)
    completed = wait_job(client, 'anonymous_observation', queued['id'])
    assert completed['status'] == 'succeeded', completed['error']
    assert completed['manifest']['source_verified'] is False
    assert completed['manifest']['split'] == 'unassigned'
    assert all(batch['split'] == 'unassigned' for batch in completed['manifest']['feature_batches'])
    denied = enqueue(client, 'anonymous_observation', body | {'requested_split': 'train'})
    assert wait_job(client, 'anonymous_observation', denied['id'])['status'] == 'failed'


def test_imports_disabled_for_legacy_config(tmp_path):
    fixture = ROOT / 'tests/fixtures/v30'
    shutil.copytree(fixture, tmp_path, dirs_exist_ok=True)
    with TestClient(create_app(tmp_path / 'config/system.yaml')) as client:
        assert client.get('/api/ingestion/capabilities').json()['enabled'] is False
        assert client.post('/api/uploads?kind=npz', content=b'x').json()['detail']['code'] == 'INGESTION_DISABLED'
        assert client.get('/api/bootstrap').json()['capabilities']['video_ingest'] is False
