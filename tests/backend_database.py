"""Migration and durability checks against a real SQLite database."""

from datetime import timezone
from uuid import UUID

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from infra.database import Database
from infra.models import AuditEvent, ConfigVersion, KnowledgeVersion


def test_migrations_are_idempotent_and_persist_across_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'nested' / 'jianwei.db'}"
    database = Database(url)
    database.migrate()
    database.migrate()
    assert set(inspect(database.engine).get_table_names()) == {
        "alembic_version", "config_versions", "knowledge_versions", "audit_events", "legacy_knowledge_mappings",
        "uploaded_artifacts", "import_jobs", "source_splits", "temporal_runs"
    }
    with database.session() as session:
        version = ConfigVersion(config_hash="a" * 64, profile="cpu", snapshot_json="{}")
        session.add(version)
        session.flush()
        version_id = version.id
        assert str(UUID(version_id)) == version_id
    database.close()

    reopened = Database(url)
    reopened.migrate()
    with reopened.session() as session:
        saved = session.get(ConfigVersion, version_id)
        assert saved.profile == "cpu"
        assert saved.created_at.tzinfo == timezone.utc
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0005_ingestion_progress"
    reopened.close()


def test_failed_unit_of_work_rolls_back_version_and_audit():
    database = Database("sqlite:///:memory:")
    database.migrate()
    with pytest.raises(RuntimeError, match="intentional"):
        with database.session() as session:
            session.add(ConfigVersion(config_hash="a" * 64, profile="cpu", snapshot_json="{}"))
            session.add(AuditEvent(event_type="config.created", entity_id="test", payload_json="{}"))
            session.flush()
            raise RuntimeError("intentional")
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(ConfigVersion)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0
    database.close()


def test_duplicate_content_is_rejected_and_session_remains_usable():
    database = Database("sqlite:///:memory:")
    database.migrate()
    attributes = {
        "content_hash": "b" * 64,
        "knowledge_version": "1.0",
        "status": "baseline",
        "yaml_text": "version: '1.0'\n",
        "data_json": "{}",
        "diff_json": "{}",
    }
    with database.session() as session:
        session.add(KnowledgeVersion(**attributes))
    with pytest.raises(IntegrityError):
        with database.session() as session:
            session.add(KnowledgeVersion(**attributes))
    with pytest.raises(IntegrityError):
        with database.session() as session:
            session.add(KnowledgeVersion(**{**attributes, "content_hash": "c" * 64, "status": "active"}))
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeVersion)) == 1
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert session.execute(text("PRAGMA busy_timeout")).scalar_one() == 10000
    database.close()
