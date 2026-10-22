from pathlib import Path
from copy import deepcopy
import shutil
import yaml
import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.config.yaml import parse_yaml

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def files(tmp_path):
    shutil.copytree(ROOT/'config', tmp_path/'config')
    shutil.copytree(ROOT/'knowledge', tmp_path/'knowledge')
    return tmp_path/'config/system.yaml'

@pytest.mark.parametrize('scene', ['garment_sop','electronics_assembly','workshop_safety','anonymous_observation'])
def test_scene_roundtrip_with_immutable_baseline(files, scene):
    source = files.parent.parent/f'knowledge/scenes/{scene}.yaml'
    original = source.read_bytes()
    with TestClient(create_app(files)) as client:
        base = client.get(f'/api/scenes/{scene}').json()
        candidate = deepcopy(base['knowledge']['data']); candidate['knowledge_version'] += '-reviewed'
        content = yaml.safe_dump(candidate,allow_unicode=True)
        assert client.post(f'/api/scenes/{scene}/knowledge/preview',json={'yaml':content}).json()['valid']
        payload = {'yaml':content,'base_hash':base['knowledge']['hash']}
        save = client.post(f'/api/scenes/{scene}/knowledge/versions',json=payload)
        assert save.status_code == 201, save.text
        assert client.post(f'/api/scenes/{scene}/knowledge/versions',json=payload).status_code == 200
        record = save.json()['version']
        assert source.read_bytes() == original
        wrong_scene = 'anonymous_observation' if scene != 'anonymous_observation' else 'garment_sop'
        assert client.get(f'/api/scenes/{wrong_scene}/knowledge/versions/{record["id"]}').status_code == 404
        assert client.get(f'/api/scenes/{scene}').json()['knowledge']['hash'] == base['knowledge']['hash']
        assert client.post(f'/api/scenes/{scene}/knowledge/versions',json={**payload,'tenant_id':'another'}).status_code == 422
    with TestClient(create_app(files)) as client:
        assert record in client.get(f'/api/scenes/{scene}/knowledge/versions').json()['versions']

def test_scope_and_compatibility_routes(files):
    with TestClient(create_app(files)) as client:
        assert len(client.get('/api/scenes').json()['scenes']) == 4
        assert client.get('/api/bootstrap').json()['active_scene'] == 'garment_sop'
        assert client.get('/api/scenes/missing').status_code == 404
        assembly = client.get('/api/scenes/electronics_assembly').json()['knowledge']
        content = assembly['data']; content['workflow']['nodes'][2]['min_count'] = 0
        result = client.post('/api/scenes/electronics_assembly/knowledge/preview',json={'yaml':yaml.safe_dump(content)}).json()
        assert result['diff']['workflow_nodes']['changed'][0]['id'] == 'node_3'
        assert not client.post('/api/scenes/garment_sop/knowledge/preview',json={'yaml':assembly['yaml']}).json()['valid']
        assert 'rtsp://' not in client.get('/api/scenes').text
        assert 'knowledge_file' not in client.get('/api/scenes').json().get('sources', {})
        api = client.get('/openapi.json').json()
        assert api['paths']['/api/bootstrap']['get']['deprecated']

def test_tenant_context_is_server_owned(files):
    with TestClient(create_app(files)) as client:
        old = client.get('/api/scenes/workshop_safety').json()['versions'][0]['id']
    raw = parse_yaml(files.read_text()); raw['profiles']['demo']['tenant_id'] = 'another_tenant'
    files.write_text(yaml.safe_dump(raw,allow_unicode=True))
    with TestClient(create_app(files)) as client:
        assert client.get(f'/api/scenes/workshop_safety/knowledge/versions/{old}').status_code == 404
        versions = client.get('/api/scenes/workshop_safety/knowledge/versions').json()['versions']
        assert len(versions) == 1 and versions[0]['id'] != old

def test_modes_and_future_rules_fail_closed(files):
    with TestClient(create_app(files)) as client:
        raw = client.get('/api/scenes/workshop_safety').json()['knowledge']['data']
        for mutation in [
            lambda v: v.update(task_modes=['procedure_compliance']),
            lambda v: v['events'][0]['trigger'].update(type='execute_script',script='not allowed'),
            lambda v: v['events'][0]['trigger'].update(to_state='another_scene.state'),
        ]:
            invalid = deepcopy(raw); mutation(invalid)
            r = client.post('/api/scenes/workshop_safety/knowledge/preview',json={'yaml':yaml.safe_dump(invalid)})
            assert not r.json()['valid']
