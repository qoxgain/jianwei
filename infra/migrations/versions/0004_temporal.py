"""Append scoped research runs; preserve all knowledge and ingestion history."""
from alembic import op
import sqlalchemy as sa

revision='0004_temporal'
down_revision='0003_ingestion'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('temporal_runs',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('tenant_id',sa.String(128),nullable=False),
        sa.Column('scene_id',sa.String(128),nullable=False),
        sa.Column('kind',sa.String(16),nullable=False),
        sa.Column('idempotency_key',sa.String(64),nullable=False),
        sa.Column('config_hash',sa.String(64),nullable=False),
        sa.Column('knowledge_hash',sa.String(64),nullable=False),
        sa.Column('engine_hash',sa.String(64),nullable=False),
        sa.Column('request_json',sa.Text(),nullable=False),
        sa.Column('snapshot_json',sa.Text(),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('artifact_key',sa.String(36),nullable=True),
        sa.Column('report_json',sa.Text(),nullable=True),
        sa.Column('error_json',sa.Text(),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('tenant_id','scene_id','idempotency_key',name='uq_temporal_idempotency'),
        sa.CheckConstraint("status IN ('queued','running','succeeded','blocked','failed')",name='ck_temporal_status'),
        sa.CheckConstraint("kind IN ('train','evaluate')",name='ck_temporal_kind'))

def downgrade():
    raise RuntimeError('研究记录不可静默删除；回退请恢复备份')
