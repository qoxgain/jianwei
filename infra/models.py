"""Persistence models for the first configuration and knowledge milestone."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator):
    """Keep UTC offsets on reads, including SQLite's naive datetime storage."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Persisted timestamps must include a timezone")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class ConfigVersion(Base):
    __tablename__ = "config_versions"
    __table_args__ = (UniqueConstraint('tenant_id', 'scene_id', 'config_hash', name='uq_config_scope_hash'),)

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default='__legacy_unassigned__')
    scene_id: Mapped[str] = mapped_column(String(128), nullable=False, default='__legacy_unassigned__')
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"
    __table_args__ = (
        UniqueConstraint('tenant_id', 'scene_id', 'content_hash', name='uq_knowledge_scope_hash'),
        CheckConstraint("status IN ('baseline', 'candidate')", name="ck_knowledge_versions_status"),
        Index("ix_knowledge_versions_created_at", "created_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default='__legacy_unassigned__')
    scene_id: Mapped[str] = mapped_column(String(128), nullable=False, default='__legacy_unassigned__')
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_version: Mapped[str] = mapped_column(String(128), nullable=False)
    base_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    yaml_text: Mapped[str] = mapped_column(Text, nullable=False)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    diff_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_entity_created", "entity_id", "created_at"),)

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default='__legacy_unassigned__')
    scene_id: Mapped[str] = mapped_column(String(128), nullable=False, default='__legacy_unassigned__')
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class LegacyKnowledgeMapping(Base):
    __tablename__ = 'legacy_knowledge_mappings'
    version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    old_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_json: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    node_map_json: Mapped[str] = mapped_column(Text, nullable=False)


class UploadedArtifact(Base):
    __tablename__ = 'uploaded_artifacts'
    __table_args__ = (UniqueConstraint('tenant_id', 'sha256', 'kind', name='uq_upload_content_kind'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class ImportJob(Base):
    __tablename__ = 'import_jobs'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'scene_id', 'idempotency_key', name='uq_import_idempotency'),
        CheckConstraint("status IN ('queued','running','succeeded','blocked','failed')", name='ck_import_status'),
        Index('ix_import_scope_created', 'tenant_id', 'scene_id', 'created_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scene_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='queued')
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class SourceSplit(Base):
    __tablename__ = 'source_splits'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'source_hash', name='uq_source_split'),
        CheckConstraint("split IN ('unassigned','train','calibration','test')", name='ck_source_split'),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    split: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
