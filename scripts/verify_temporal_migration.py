"""Exercise additive upgrades on an actual P0.2 schema with immutable old rows."""
import argparse
import json
from pathlib import Path
import tempfile
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, inspect, text
from infra.database import Database
from infra.models import ConfigVersion, KnowledgeVersion, UploadedArtifact, SourceSplit, AuditEvent, utc_now
from packages.knowledge.service import content_hash


def verify(directory):
    database = Database(f'sqlite:///{directory / "migration.sqlite"}')
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'infra/migrations'))
    with database.engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0003_ingestion')
    scope = {'tenant_id': 'migration_fixture', 'scene_id': 'anonymous_observation'}
    with database.session() as session:
        session.add(ConfigVersion(**scope, config_hash='a'*64, profile='demo', snapshot_json='{"preserve":true}'))
        session.add(KnowledgeVersion(**scope, content_hash='b'*64, knowledge_version='fixture', status='baseline',
            yaml_text="schema_version: '3.1'\n", data_json='{"schema_version":"3.1"}', diff_json='{}'))
        session.add(UploadedArtifact(tenant_id=scope['tenant_id'],sha256='c'*64,kind='mp4',size_bytes=123))
        # Reflect the *old* schema: current ORM columns do not exist until upgrade.
        old_imports = Table('import_jobs', MetaData(), autoload_with=database.engine)
        session.execute(old_imports.insert().values(**scope, id='original-import',
            idempotency_key='d'*64,config_hash='a'*64,knowledge_hash='b'*64,
            request_json='{"format":"mp4"}',config_snapshot_json='{}',status='blocked',
            error_json='{"code":"ENCODER_ADAPTER_UNAVAILABLE"}',manifest_json='{"old_source":true}',
            created_at=utc_now(), updated_at=utc_now()))
        session.add(SourceSplit(id='e'*64,tenant_id=scope['tenant_id'],source_hash='f'*64,split='test'))
        session.add(AuditEvent(**scope,event_type='fixture.imported',entity_id='original',payload_json='{}'))
    tables = sorted(set(inspect(database.engine).get_table_names()) - {'alembic_version'})
    original_columns = {name: [col['name'] for col in inspect(database.engine).get_columns(name)]
                        for name in tables}
    def snapshot():
        with database.engine.connect() as connection:
            # Compare every historical column while permitting additive columns.
            return {name: [dict(r) for r in connection.execute(text(
                'SELECT '+','.join(original_columns[name])+' FROM '+name+' ORDER BY 1')).mappings()]
                    for name in tables}
    before = snapshot()
    database.migrate(); after = snapshot()
    database.migrate(); repeated = snapshot()
    assert before == after == repeated, 'P0.3 migration changed an existing row'
    with database.engine.connect() as connection:
        revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        new_count = connection.execute(text('SELECT count(*) FROM temporal_runs')).scalar_one()
        progress = connection.execute(text(
            'SELECT progress_json,cancel_requested FROM import_jobs WHERE id=:id'),
            {'id':'original-import'}).mappings().one()
        assert progress['progress_json'] is None and progress['cancel_requested'] == 0
    database.close()
    assert revision == '0005_ingestion_progress' and new_count == 0
    return {'scope': 'Disposable fixture; not a customer database', 'from_revision': '0003_ingestion',
            'to_revision': revision, 'repeated_upgrade_identical': True, 'new_research_rows': new_count,
            'additive_import_defaults': dict(progress),
            'old_tables': {name: {'rows': len(rows), 'before_sha256': content_hash(rows),
                                 'after_sha256': content_hash(after[name])} for name, rows in before.items()},
            'old_ids_hashes_payloads_and_source_splits_preserved': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('docs/delivery/p0.3.3/migration.json'))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='jianwei-p03-migration-') as temporary:
        result = verify(Path(temporary))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print('0003 -> 0005: existing columns unchanged; additive defaults verified; repeated migration unchanged')
