"""Database lifecycle with explicit Alembic migrations and atomic sessions."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
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

    def migrate(self) -> None:
        """Apply committed schema revisions, preserving an in-memory connection."""
        configuration = Config()
        configuration.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with _MIGRATION_LOCK, self.engine.begin() as connection:
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "head")

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
