"""Append durable progress/cancellation without rewriting historical import rows."""
from alembic import op
import sqlalchemy as sa

revision = '0005_ingestion_progress'
down_revision = '0004_temporal'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('import_jobs', sa.Column('progress_json', sa.Text(), nullable=True))
    op.add_column('import_jobs', sa.Column('cancel_requested', sa.Boolean(), nullable=False,
                                         server_default=sa.false()))


def downgrade():
    raise RuntimeError('导入进度和取消记录不可静默丢弃；请恢复备份')
