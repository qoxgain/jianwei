import json
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from infra.database import Database
from packages.config.loader import load_bundle
from packages.config.yaml import parse_yaml
from packages.knowledge.service import content_hash, parse_knowledge

ROOT=Path(__file__).resolve().parents[1]

def migrate_fixture(db_path):
    db=Database(f'sqlite:///{db_path}')
    config=Config();config.set_main_option('script_location',str(ROOT/'infra/migrations'))
    bundle=load_bundle(ROOT/'tests/fixtures/v30/config/system.yaml')
    yaml_text=(ROOT/'tests/fixtures/v30/knowledge/labels.yaml').read_text()
    with db.engine.begin() as conn:
        config.attributes['connection']=conn;command.upgrade(config,'0001_foundation')
        conn.execute(text("INSERT INTO config_versions VALUES ('config',:hash,'demo',:data,'2026-10-16 00:00:00')"),{'hash':bundle.config_hash,'data':json.dumps(bundle.snapshot)})
        for id,status,content in [('legacy-base','baseline',yaml_text),('legacy-candidate','candidate',yaml_text.replace('检查裤脚','复核裤脚'))]:
            raw=parse_knowledge(content).model_dump()
            conn.execute(text("INSERT INTO knowledge_versions VALUES (:id,:hash,:version,NULL,:status,:yaml,:data,'{}','2026-10-16 00:00:00')"),dict(id=id,hash=content_hash(raw),version=raw['knowledge_version'],status=status,yaml=content,data=json.dumps(raw,ensure_ascii=False)))
        conn.execute(text("INSERT INTO audit_events VALUES ('audit','knowledge.candidate.created','legacy-candidate','{}','2026-10-16 00:00:00')"))
        before=[dict(r) for r in conn.execute(text('SELECT * FROM knowledge_versions ORDER BY id')).mappings()]
    db.migrate('tenant_demo','garment_sop')
    with db.engine.connect() as conn:
        after=[dict(r) for r in conn.execute(text('SELECT * FROM knowledge_versions ORDER BY id')).mappings()]
        mappings=[dict(r) for r in conn.execute(text('SELECT * FROM legacy_knowledge_mappings ORDER BY version_id')).mappings()]
        first=conn.execute(text('SELECT count(*) FROM audit_events')).scalar()
    db.migrate('tenant_demo','garment_sop')
    with db.engine.connect() as conn:
        repeated=[dict(r) for r in conn.execute(text('SELECT * FROM knowledge_versions ORDER BY id')).mappings()]
        mapping_repeat=[dict(r) for r in conn.execute(text('SELECT * FROM legacy_knowledge_mappings ORDER BY version_id')).mappings()]
        assert conn.execute(text('SELECT count(*) FROM audit_events')).scalar()==first
    db.close()
    assert [{k:r[k] for k in before[0]} for r in after]==before
    assert all(r['tenant_id']=='tenant_demo' and r['scene_id']=='garment_sop' for r in after)
    assert after==repeated and mappings==mapping_repeat and len(mappings)==2
    assert Path(str(db_path)+'.pre-p011.sqlite').is_file()
    return {'before':before,'after':after,'mappings':mappings,'repeat_identical':True,'backup_created':True}

def test_lossless_legacy_migration_and_repeat(tmp_path):
    report=migrate_fixture(tmp_path/'legacy.db')
    assert all(m['old_hash'] != m['normalized_hash'] for m in report['mappings'])

def test_export_preserves_resource_locations_and_is_idempotent(tmp_path):
    from scripts.migrate_v30 import export
    source=ROOT/'tests/fixtures/v30/config/system.yaml'
    before=source.read_bytes()
    report=export(source,tmp_path/'export')
    assert export(source,tmp_path/'export')==report
    assert source.read_bytes()==before
    old,new=load_bundle(source),load_bundle(tmp_path/'export/config/system.yaml')
    assert old.paths==new.paths
    assert new.config.active_scene==old.config.active_scene
    assert new.knowledge.schema_version=='3.1'

def test_legacy_config_has_normalized_scene_ui_and_unchanged_old_api(tmp_path):
    import shutil
    from fastapi.testclient import TestClient
    from apps.api.main import create_app
    shutil.copytree(ROOT/'tests/fixtures/v30',tmp_path/'legacy')
    path=tmp_path/'legacy/config/system.yaml'
    with TestClient(create_app(path)) as client:
        original=client.get('/api/bootstrap').json()['knowledge']
        current=client.get('/api/scenes/garment_sop').json()['knowledge']
        assert original['data']['schema_version']=='3.0'
        assert current['data']['schema_version']=='3.1'
        assert client.get('/api/scenes').json()['scenes'][0]['knowledge_hash']==current['hash']
        assert client.get('/api/scenes/garment_sop').json()['scene']['knowledge_hash']==current['hash']
        assert client.get('/api/bootstrap').json()['scene']['knowledge_hash']==original['hash']
        assert current['data']['workflow']['nodes'][0]['node_id']
        result=client.post('/api/scenes/garment_sop/knowledge/preview',json={'yaml':original['yaml']})
        assert result.json()['valid'] and result.json()['hash']==current['hash']
        assert not client.post('/api/knowledge/preview',json={'yaml':current['yaml']}).json()['valid']
        saved=client.post('/api/knowledge/versions',json={'yaml':original['yaml'].replace('检查裤脚','复核裤脚'),'base_hash':original['hash']})
        assert saved.status_code==201
        record=client.get('/api/knowledge/versions/'+saved.json()['version']['id']).json()['version']
        assert record['migration']['old_hash']==record['hash']
        assert record['migration']['data']['schema_version']=='3.1'
        assert client.get('/api/bootstrap').json()['knowledge']==original
