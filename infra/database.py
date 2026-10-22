"""Database lifecycle with explicit Alembic migrations and atomic sessions."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


# Alembic uses a process-wide environment proxy while a revision runs.
_MIGRATION_LOCK = Lock()


class Database:
    def __init__(self, database_url: str):
        url = make_url(database_url)
        options = {"pool_pre_ping": True}
        if url.get_backend_name() == "sqlite":
            options["connect_args"] = {"check_same_thread": False, "timeout": 10.0}
            if url.database in (None, "", ":memory:"):
                # Reuse the same connection when a test or local app selects memory.
                options["poolclass"] = StaticPool
            elif not url.query.get("uri"):
                Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, **options)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._configure_sqlite)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @staticmethod
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA busy_timeout = 10000")
        finally:
            cursor.close()

    def migrate(self, tenant_id: str | None = None, scene_id: str | None = None) -> None:
        """Apply committed schema revisions, preserving an in-memory connection."""
        configuration = Config()
        configuration.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with _MIGRATION_LOCK:
            # A physical SQLite backup is made before touching the legacy revision.
            with self.engine.connect() as connection:
                tables = inspect(connection).get_table_names()
                revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar() if 'alembic_version' in tables else None
            db_path = self.engine.url.database
            if revision == '0001_foundation' and db_path and db_path != ':memory:':
                import sqlite3
                backup_path = Path(db_path + '.pre-p011.sqlite')
                if not backup_path.exists():
                    with sqlite3.connect(db_path) as source, sqlite3.connect(backup_path) as target:
                        source.backup(target)
            with self.engine.begin() as connection:
                configuration.attributes.update(connection=connection, tenant_id=tenant_id, scene_id=scene_id)
                command.upgrade(configuration, "head")
                from infra.legacy_data import record_legacy_mappings
                record_legacy_mappings(connection)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Commit all writes together; roll back the unit of work on any error."""
        with self._sessions() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def close(self) -> None:
        self.engine.dispose()
