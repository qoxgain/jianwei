"""Persist validated configuration, knowledge candidates, and their audit trail.

Revision ID: 0001_foundation
Revises: None
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_versions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("profile", sa.String(64), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("config_hash", name="uq_config_versions_config_hash"),
    )
    op.create_table(
        "knowledge_versions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("knowledge_version", sa.String(128), nullable=False),
        sa.Column("base_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("yaml_text", sa.Text(), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("diff_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_knowledge_versions_content_hash"),
        sa.CheckConstraint("status IN ('baseline', 'candidate')", name="ck_knowledge_versions_status"),
    )
    op.create_index("ix_knowledge_versions_created_at", "knowledge_versions", ["created_at"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_entity_created", "audit_events", ["entity_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_entity_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_knowledge_versions_created_at", table_name="knowledge_versions")
    op.drop_table("knowledge_versions")
    op.drop_table("config_versions")
