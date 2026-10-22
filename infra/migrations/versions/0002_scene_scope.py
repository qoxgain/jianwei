"""Scope history and preserve legacy payloads, identifiers and hashes."""
from alembic import op
import sqlalchemy as sa
import json

revision = '0002_scene_scope'
down_revision = '0001_foundation'
branch_labels = None
depends_on = None

def upgrade():
    connection = op.get_bind()
    snapshots = list(connection.execute(sa.text('SELECT * FROM config_versions')).mappings())
    scopes, tenants = {}, set()
    for row in snapshots:
        data = json.loads(row['snapshot_json'])
        profile = data.get('profiles', {}).get(row['profile'], {})
        if profile.get('tenant_id'):
            tenants.add(profile['tenant_id'])
            scopes[row['id']] = (profile['tenant_id'], data.get('active_scene', '__legacy_unassigned__'))
    if len(tenants) > 1:
        raise RuntimeError('旧单作用域数据库存在多个租户，须人工提供逐记录归属，禁止猜测迁移')
    supplied = op.get_context().config.attributes.get('tenant_id')
    tenant = next(iter(tenants), supplied or '__legacy_unassigned__')
    fallback_scene = op.get_context().config.attributes.get('scene_id') or '__legacy_unassigned__'
    for table in ('config_versions', 'knowledge_versions', 'audit_events'):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('tenant_id', sa.String(128), nullable=False, server_default='__legacy_unassigned__'))
            batch.add_column(sa.Column('scene_id', sa.String(128), nullable=False, server_default='__legacy_unassigned__'))
            if table == 'config_versions':
                batch.drop_constraint('uq_config_versions_config_hash', type_='unique')
                batch.create_unique_constraint('uq_config_scope_hash', ['tenant_id', 'scene_id', 'config_hash'])
            if table == 'knowledge_versions':
                batch.drop_constraint('uq_knowledge_versions_content_hash', type_='unique')
                batch.create_unique_constraint('uq_knowledge_scope_hash', ['tenant_id', 'scene_id', 'content_hash'])
    for row in snapshots:
        t, s = scopes.get(row['id'], (tenant, fallback_scene))
        connection.execute(sa.text('UPDATE config_versions SET tenant_id=:t, scene_id=:s WHERE id=:id'), {'t':t,'s':s,'id':row['id']})
    for row in connection.execute(sa.text('SELECT id, data_json FROM knowledge_versions')).mappings():
        scope = (tenant, json.loads(row['data_json']).get('scene_id', fallback_scene))
        scopes[row['id']] = scope
        connection.execute(sa.text('UPDATE knowledge_versions SET tenant_id=:t, scene_id=:s WHERE id=:id'), {'t':scope[0],'s':scope[1],'id':row['id']})
    for row in connection.execute(sa.text('SELECT id, entity_id FROM audit_events')).mappings():
        t, s = scopes.get(row['entity_id'], (tenant, fallback_scene))
        connection.execute(sa.text('UPDATE audit_events SET tenant_id=:t, scene_id=:s WHERE id=:id'), {'t':t,'s':s,'id':row['id']})
    op.create_table('legacy_knowledge_mappings',
        sa.Column('version_id', sa.String(36), primary_key=True),
        sa.Column('old_hash', sa.String(64), nullable=False),
        sa.Column('normalized_hash', sa.String(64), nullable=False),
        sa.Column('normalized_json', sa.Text(), nullable=False),
        sa.Column('normalized_yaml', sa.Text(), nullable=False),
        sa.Column('node_map_json', sa.Text(), nullable=False))
    from infra.legacy_data import record_legacy_mappings
    record_legacy_mappings(connection)

def downgrade():
    raise RuntimeError('无损回退请恢复迁移前SQLite备份；不能静默合并不同场景版本')
