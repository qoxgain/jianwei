"""Database lifecycle with explicit Alembic migrations and atomic sessions."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import sqlite3
import hashlib
import json
from functools import partial
from infra.file_gate import file_gate
from infra.sqlite_connection import ManagedConnection, _backup_to_new_file
from sqlalchemy.exc import DisconnectionError
from packages.contracts.database import SQLiteSettings
from packages.config.errors import ContractError
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
    def __init__(self, database_url: str, sqlite_settings=None):
        self.sqlite_settings = sqlite_settings or SQLiteSettings()
        self.last_integrity_check = None
        self._fault = None
        url = make_url(database_url)
        local_path=Path(url.database).expanduser().resolve() if url.get_backend_name()=='sqlite' and url.database not in (None,'',':memory:') else None
        self.quarantine_path=Path(str(local_path)+'.fault.json') if local_path else None
        self.commit_gate=file_gate(str(local_path)+'.commit.lock' if local_path else None)
        options = {"pool_pre_ping": True}
        if url.get_backend_name() == "sqlite":
            options["connect_args"] = {"check_same_thread": False, "timeout": self.sqlite_settings.busy_timeout_ms/1000,
                "factory":partial(ManagedConnection,_owner=self),"cached_statements":0}
            if url.database in (None, "", ":memory:"):
                # Reuse the same connection when a test or local app selects memory.
                options["poolclass"] = StaticPool
            elif not url.query.get("uri"):
                Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, **options)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._configure_sqlite)
        event.listen(self.engine, 'handle_error', self._record_fault)
        event.listen(self.engine, 'before_cursor_execute', self._before_write)
        event.listen(self.engine, 'connect', self._record_process)
        event.listen(self.engine, 'checkout', self._check_process)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def _configure_sqlite(self, dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute(f"PRAGMA busy_timeout = {self.sqlite_settings.busy_timeout_ms}")
            cursor.execute(f"PRAGMA synchronous = {self.sqlite_settings.synchronous}")
        finally:
            cursor.close()

    def migrate(self, tenant_id: str | None = None, scene_id: str | None = None) -> None:
        """Apply committed schema revisions, preserving an in-memory connection."""
        configuration = Config()
        configuration.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with _MIGRATION_LOCK, self._migration_file_lock():
            if self.engine.dialect.name == 'sqlite':
                if self.sqlite_settings.integrity_check_on_start:
                    self.check_integrity()
                with self.engine.connect() as connection:
                    if self.engine.url.database not in (None, '', ':memory:'):
                        mode = connection.exec_driver_sql('PRAGMA journal_mode = '+self.sqlite_settings.journal_mode).scalar_one()
                        if mode.upper()!=self.sqlite_settings.journal_mode:
                            raise ContractError('DATABASE_POLICY_UNAVAILABLE','文件系统未接受配置的 SQLite 日志模式')
            # A physical SQLite backup is made before touching the legacy revision.
            with self.engine.connect() as connection:
                tables = inspect(connection).get_table_names()
                revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar() if 'alembic_version' in tables else None
            db_path = self.engine.url.database
            if revision == '0001_foundation' and db_path and db_path != ':memory:':
                import sqlite3
                backup_path = Path(db_path + '.pre-p011.sqlite')
                if not backup_path.exists():
                    self.backup(backup_path)
            with self.engine.begin() as connection:
                configuration.attributes.update(connection=connection, tenant_id=tenant_id, scene_id=scene_id)
                command.upgrade(configuration, "head")
                from infra.legacy_data import record_legacy_mappings
                record_legacy_mappings(connection)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Commit all writes together; roll back the unit of work on any error."""
        with self.commit_gate.hold():self._assert_healthy()
        with self._sessions() as session:
            try:
                yield session
                if session.new or session.dirty or session.deleted:
                    self._check_space()
                session.commit()
            except BaseException:
                session.rollback()
                raise

    @property
    def fault(self):
        if self._fault is None and self.quarantine_path is not None and self.quarantine_path.exists():
            try:self._fault=json.loads(self.quarantine_path.read_text())
            except (OSError,ValueError):self._fault={'code':'DATABASE_UNAVAILABLE','root_cause':'invalid_quarantine_marker'}
        return self._fault

    @fault.setter
    def fault(self,value):
        with self.commit_gate.hold():
            if value is None:
                if self.fault is not None:raise ContractError('DATABASE_UNAVAILABLE','故障隔离不可由运行进程清除或自动恢复')
                return
            self._fault=value
            if self.quarantine_path is not None and not self.quarantine_path.exists():
                # Create the irreversible marker first. A crash during writing
                # leaves a partial marker, which every other process treats as
                # quarantine too; no rename gap can silently clear the fault.
                with self.quarantine_path.open('x') as stream:
                    json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
                fd=os.open(self.quarantine_path.parent,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)

    def _assert_healthy(self):
        if self.fault is not None:
            raise ContractError('DATABASE_UNAVAILABLE','数据库已隔离；所有在途事务停止提交，保留原库及消费记录，不自动恢复')

    def _record_sqlite_fault(self,error):
        name=getattr(error,'sqlite_errorname','')
        if name.startswith(('SQLITE_CORRUPT','SQLITE_NOTADB','SQLITE_IOERR','SQLITE_FULL')):
            self.fault={'code':'DATABASE_UNAVAILABLE','sqlite_errorname':name,'root_cause':'not_established'}

    def _record_fault(self, context):
        self._record_sqlite_fault(context.original_exception)

    def _before_write(self, connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split(None,1)[0].upper() in {'INSERT','UPDATE','DELETE','CREATE','ALTER','DROP','REPLACE','VACUUM','REINDEX'}:
            if self.fault is not None:
                raise ContractError('DATABASE_UNAVAILABLE','数据库故障已记录，暂停写入，不自动修复原库')
            self._check_space()

    @staticmethod
    def _record_process(connection, record):
        record.info['owner_pid'] = os.getpid()

    @staticmethod
    def _check_process(connection, record, proxy):
        if record.info.get('owner_pid') != os.getpid():
            # Never rollback/close a parent's live connection after fork.
            record.dbapi_connection = proxy.dbapi_connection = None
            raise DisconnectionError('Inherited database connection; open in child process')

    @contextmanager
    def _migration_file_lock(self):
        path = self.engine.url.database
        if self.engine.dialect.name!='sqlite' or path in (None,'',':memory:'):
            yield;return
        import fcntl
        with Path(path+'.migrate.lock').open('a') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

    def _check_space(self):
        path = self.engine.url.database
        if self.engine.dialect.name=='sqlite' and path not in (None,'',':memory:'):
            if shutil.disk_usage(Path(path).resolve().parent).free < self.sqlite_settings.min_free_bytes:
                raise ContractError('DATABASE_SPACE_LOW','数据库所在文件系统可用空间不足，事务已停止；不自动删除数据')

    def check_integrity(self):
        if self.engine.dialect.name!='sqlite':return {'status':'not_applicable'}
        try:
            with self.engine.connect() as connection:
                rows = connection.exec_driver_sql('PRAGMA quick_check').scalars().all()
                foreign = connection.exec_driver_sql('PRAGMA foreign_key_check').first()
            if rows!=['ok'] or foreign is not None:raise ValueError
        except Exception as error:
            self.last_integrity_check={'status':'failed','exception_type':type(error).__name__}
            raise ContractError('DATABASE_INTEGRITY_FAILED','SQLite 完整性检查未通过；保留原库并在副本调查，禁止自动修复或继续迁移') from None
        self.last_integrity_check={'status':'ok','check':'quick_check_and_foreign_keys'}
        return self.last_integrity_check

    def backup(self, target):
        """Consistent SQLite online backup to a NEW file; never restore in place."""
        if self.engine.dialect.name!='sqlite':
            raise ContractError('DATABASE_BACKUP_UNAVAILABLE','该备份实现仅支持 SQLite')
        target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
        # Reserve exclusively; an existing backup or database is never replaced.
        with target.open('xb'):pass
        try:
            with self.engine.connect() as connection, sqlite3.connect(target) as output:
                _backup_to_new_file(connection.connection.driver_connection,output)
                if output.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError
            with target.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
            return {'status':'ok','sha256':sha,'bytes':target.stat().st_size,'original_overwritten':False}
        except Exception:
            # Retain the failed backup for investigation; never retry over it.
            raise ContractError('DATABASE_BACKUP_FAILED','备份未通过校验；保留失败输出及原库，不自动覆盖或恢复') from None

    def close(self) -> None:
        self.engine.dispose()
