"""Durable imports and source-global split assignments; old records untouched."""
from alembic import op
import sqlalchemy as sa

revision = '0003_ingestion'
down_revision = '0002_scene_scope'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('uploaded_artifacts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(128), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'sha256', 'kind', name='uq_upload_content_kind'))
    op.create_table('import_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(128), nullable=False),
        sa.Column('scene_id', sa.String(128), nullable=False),
        sa.Column('idempotency_key', sa.String(64), nullable=False),
        sa.Column('config_hash', sa.String(64), nullable=False),
        sa.Column('knowledge_hash', sa.String(64), nullable=False),
        sa.Column('request_json', sa.Text(), nullable=False),
        sa.Column('config_snapshot_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('error_json', sa.Text(), nullable=True),
        sa.Column('manifest_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'scene_id', 'idempotency_key', name='uq_import_idempotency'),
        sa.CheckConstraint("status IN ('queued','running','succeeded','blocked','failed')", name='ck_import_status'))
    op.create_index('ix_import_scope_created', 'import_jobs', ['tenant_id', 'scene_id', 'created_at'])
    op.create_table('source_splits',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(128), nullable=False),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('split', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'source_hash', name='uq_source_split'),
        sa.CheckConstraint("split IN ('unassigned','train','calibration','test')", name='ck_source_split'))


def downgrade():
    raise RuntimeError('导入数据不可静默丢弃；请从迁移前备份恢复')
