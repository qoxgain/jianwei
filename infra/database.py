"""Database lifecycle with explicit Alembic migrations and atomic sessions."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import sqlite3
import hashlib
import json
import stat
import time
from functools import partial
from infra.file_gate import file_gate
from infra.sqlite_connection import ManagedConnection
from sqlalchemy.exc import DisconnectionError
from packages.contracts.database import SQLiteSettings
from packages.config.errors import ContractError
from threading import Lock, Event

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


# Alembic uses a process-wide environment proxy while a revision runs.
_MIGRATION_LOCK = Lock()
_ISOLATION_FAILURES = {}
_RETAINED_FAILURE_LOCKS = {}


class Database:
    def __init__(self, database_url: str, sqlite_settings=None):
        self.sqlite_settings = sqlite_settings or SQLiteSettings()
        self.last_integrity_check = None
        self._fault = None
        self._backup_shutdown = Event()
        url = make_url(database_url)
        local_path=Path(url.database).expanduser().resolve() if url.get_backend_name()=='sqlite' and url.database not in (None,'',':memory:') else None
        if url.get_backend_name()=='sqlite' and url.query.get('uri'):
            raise ContractError('DATABASE_POLICY_UNAVAILABLE','受管隔离仅支持普通文件SQLite或本进程独立内存库')
        self.local_path=local_path;self._file_identity=None
        self._business_registration=None;self._registration_identity=None
        self.admission_path=Path(str(local_path)+'.admission.json') if local_path else None
        if local_path is not None:
            url=url.set(database=str(local_path));database_url=url
        self.quarantine_path=Path(str(local_path)+'.fault.json') if local_path else None
        self.fault_intent_path=Path(str(local_path)+'.fault-intent') if local_path else None
        self.commit_gate=file_gate(str(local_path)+'.commit.lock' if local_path else None,
            wait_timeout=self.sqlite_settings.busy_timeout_ms/1000)
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
        from infra.sqlite_connection import _configure_connection
        _configure_connection(dbapi_connection,self)

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
        with self._commit_boundary():pass
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
        key=str(self.local_path) if self.local_path is not None else None
        if key in _ISOLATION_FAILURES:return _ISOLATION_FAILURES[key]
        if self._fault is None and self.quarantine_path is not None and self.quarantine_path.exists():
            try:
                info=self.quarantine_path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('unsafe marker identity')
                self._fault=json.loads(self.quarantine_path.read_text())
            except (OSError,ValueError):self._fault={'code':'DATABASE_UNAVAILABLE','root_cause':'invalid_quarantine_marker'}
            if not isinstance(self._fault,dict):self._fault={'code':'DATABASE_UNAVAILABLE','root_cause':'invalid_quarantine_marker'}
        if self._fault is None:
            from infra.sqlite_isolation import read_intent
            self._fault=read_intent(self.fault_intent_path)
        if self._fault is None and self.commit_gate.path is not None:
            try:
                info=self.commit_gate.path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                    self._fault={'code':'DATABASE_UNAVAILABLE','root_cause':'unsafe_coordination_identity'}
                elif (info.st_mode&0o600)!=0o600:
                    self._fault={'code':'DATABASE_UNAVAILABLE','root_cause':'coordination_access_disabled'}
                elif info.st_size:
                    try:self._fault=json.loads(self.commit_gate.path.read_text())
                    except (OSError,ValueError):self._fault={'code':'DATABASE_UNAVAILABLE','root_cause':'incomplete_fault_intent'}
                    if not isinstance(self._fault,dict):self._fault={'code':'DATABASE_UNAVAILABLE','root_cause':'invalid_fault_intent'}
            except FileNotFoundError:pass
            except OSError:self._fault={'code':'DATABASE_UNAVAILABLE','root_cause':'unreadable_coordination_state'}
        return self._fault

    @fault.setter
    def fault(self,value):
        if value is None:
            if self.fault is not None:raise ContractError('DATABASE_UNAVAILABLE','故障隔离不可由运行进程清除或自动恢复')
            return
        if self.fault is not None:return
        value={**value,'version':'sqlite-isolation-v4','confirmed_ns':time.time_ns(),
            'database_identity':self._identity_record(),'phase':'intent_pending'}
        intent_failures=[]
        if self.fault_intent_path is not None:
            from infra.sqlite_isolation import publish_intent
            try:publish_intent(self.fault_intent_path,value)
            except OSError as error:intent_failures.append('independent_intent:'+type(error).__name__)
        # This intent closes new admission BEFORE any potentially busy gate wait.
        # An already admitted native COMMIT may finish; formal isolation is only
        # acknowledged once the gate drains that earlier admission.
        self._fault=value
        with self._isolation_boundary(value):
            if self.quarantine_path is not None and self.quarantine_path.exists():return
            value={**value,'phase':'isolated','drained_ns':time.time_ns()}
            self._fault=value
            if self.quarantine_path is not None:
                # Existing coordination FD is an independent durable intent.
                # Any partial/nonempty intent fails closed if primary JSON creation fails.
                failures=list(intent_failures);intent_ok=False;primary_ok=False
                try:self.commit_gate._write_fault_intent((json.dumps(value)+'\n').encode())
                except OSError as error:failures.append(type(error).__name__)
                else:intent_ok=True
                # Create the irreversible marker first. A crash during writing
                # leaves a partial marker, which every other process treats as
                # quarantine too; no rename gap can silently clear the fault.
                try:
                    with self.quarantine_path.open('x') as stream:
                        json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
                    fd=os.open(self.quarantine_path.parent,os.O_RDONLY)
                    try:os.fsync(fd)
                    finally:os.close(fd)
                except OSError as error:failures.append(type(error).__name__)
                else:primary_ok=True
                if failures:
                    if not intent_ok and not primary_ok:
                        try:self.commit_gate._disable_fault_access()
                        except OSError as error:failures.append('access_latch:'+type(error).__name__)
                    key=str(self.local_path);_ISOLATION_FAILURES[key]={**value,'persistence_error':failures}
                    _RETAINED_FAILURE_LOCKS.setdefault(key,self.commit_gate._retain_failure_lock())
                    raise ContractError('DATABASE_ISOLATION_PERSISTENCE_FAILED','隔离持久化出现错误；进程停写并保留保护锁，需离线处置，禁止忽略后继续业务')

    @contextmanager
    def _isolation_boundary(self,value):
        try:
            with self.commit_gate.hold():yield
        except (ContractError,OSError) as error:
            if isinstance(error,ContractError) and error.code not in {'RESOURCE_LOCK_TIMEOUT','RESOURCE_LOCK_IDENTITY_UNSAFE','RESOURCE_LOCK_QUARANTINED'}:raise
            from infra.sqlite_isolation import read_intent,close_coordination_access,sync_directory
            if self.fault_intent_path is not None and read_intent(self.fault_intent_path) is not None:
                self._fault={**value,'phase':'intent_pending','coordination':'not_drained'}
                raise ContractError('DATABASE_ISOLATION_PENDING','故障意图已持久化并禁止新提交；提交闸门未排空，不能宣称此前已准入的提交全部结束') from None
            # An unavailable claim must not degrade to process memory. Try a
            # durable primary stop marker and metadata latch without a gate wait.
            failures=['coordination_unavailable']
            if self.quarantine_path is not None:
                try:
                    with self.quarantine_path.open('x') as stream:
                        json.dump({**value,'phase':'intent_pending'},stream);stream.flush();os.fsync(stream.fileno())
                    sync_directory(self.quarantine_path.parent)
                except OSError as failed:failures.append('primary:'+type(failed).__name__)
                try:close_coordination_access(self.commit_gate.path)
                except OSError as failed:failures.append('access_latch:'+type(failed).__name__)
            self._fault={**value,'persistence_error':failures}
            if self.local_path is not None:_ISOLATION_FAILURES[str(self.local_path)]=self._fault
            raise ContractError('DATABASE_ISOLATION_PERSISTENCE_FAILED','故障隔离协调或持久化失败；必须停止应用写入并离线处置，不可忽略后恢复服务') from None

    def _identity_record(self):
        result={'binding':'canonical_path','path_sha256':hashlib.sha256(str(self.local_path).encode()).hexdigest()}
        if self.local_path is not None:
            try:
                info=self.local_path.stat();result.update(device=info.st_dev,inode=info.st_ino)
            except OSError:result['file_identity']='unavailable'
        else:result['binding']='process_local_memory'
        return result

    @contextmanager
    def _commit_boundary(self):
        self._assert_healthy()
        try:
            with self.commit_gate.hold():self._assert_healthy();yield
        except ContractError as error:
            if error.code=='RESOURCE_LOCK_TIMEOUT':
                raise ContractError('DATABASE_COORDINATION_UNAVAILABLE','数据库协调租约不可用，事务未提交') from None
            raise

    def _assert_healthy(self):
        if self.fault is not None:
            raise ContractError('DATABASE_UNAVAILABLE','数据库已隔离；所有在途事务停止提交，保留原库及消费记录，不自动恢复')
        if self.local_path is not None:
            try:info=self.local_path.stat()
            except FileNotFoundError:
                if self._file_identity is not None:raise ContractError('DATABASE_IDENTITY_CHANGED','原数据库文件缺失，禁止继续使用旧连接') from None
                return
            except OSError:raise ContractError('DATABASE_IDENTITY_UNAVAILABLE','无法核查数据库文件身份，暂停访问') from None
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                raise ContractError('DATABASE_IDENTITY_UNSAFE','SQLite必须使用独立普通文件，禁止硬链接别名绕过隔离')
            identity=(info.st_dev,info.st_ino)
            if self._file_identity is None:
                # Published archives and their in-progress targets are not an
                # approved writable restore path. Native read-only inspection is separate.
                if any(self.local_path.parent.glob(self.local_path.name+'.backup-*.json')):
                    raise ContractError('DATABASE_RESTORE_REVIEW_REQUIRED','备份不能直接作为可写业务库；必须离线核对封存消费台账')
                self._file_identity=identity
            elif identity!=self._file_identity:
                raise ContractError('DATABASE_IDENTITY_CHANGED','数据库文件身份已变化；禁止继续使用旧连接，需离线核对封存消费记录')
            from infra.sqlite_admission import fast_check
            fast_check(self)

    def _prepare_business(self):
        from infra.sqlite_admission import prepare
        prepare(self)

    def register_legacy(self,review):
        """Explicit offline business admission; never approves a backup restore."""
        from infra.sqlite_admission import register_legacy
        return register_legacy(self,review)

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

    def _apply_integrity(self,result):
        self.last_integrity_check=result
        category=result['category']
        if result['status']=='ok':return result
        if category=='structural_corruption':
            self.fault={'code':'DATABASE_UNAVAILABLE','reason':'structural_corruption',
                'root_cause':'root_cause_not_established','integrity_evidence':result}
            raise ContractError('DATABASE_INTEGRITY_FAILED','SQLite结构完整性失败，已进入持久隔离；保留原库，不自动修复')
        code={'foreign_keys':'DATABASE_FOREIGN_KEY_FAILED','logical_consistency':'DATABASE_LOGICAL_INTEGRITY_FAILED',
            'busy':'DATABASE_INTEGRITY_BUSY','locked':'DATABASE_INTEGRITY_LOCKED','timeout':'DATABASE_INTEGRITY_TIMEOUT',
            'cancelled':'DATABASE_INTEGRITY_CANCELLED'}.get(category,'DATABASE_INTEGRITY_CHECK_FAILED')
        raise ContractError(code,'完整性检查未通过；未将锁竞争或逻辑一致性问题判为永久物理损坏',
            [{'path':'integrity','message':'category='+category}])

    def _validate_native(self,native,*,foreign_keys=False):
        from infra.sqlite_health import inspect_native
        self._assert_healthy()
        return self._apply_integrity(inspect_native(native,timeout_seconds=self.sqlite_settings.busy_timeout_ms/1000,
            busy_timeout_ms=self.sqlite_settings.busy_timeout_ms,foreign_keys=foreign_keys))

    def check_integrity(self,*,timeout_seconds=None,busy_timeout_ms=None,foreign_keys=True,cancel_event=None):
        if self.engine.dialect.name!='sqlite':return {'status':'not_applicable'}
        self._assert_healthy()
        from infra.sqlite_health import inspect_file
        if self.local_path is None:
            with self.engine.connect() as c:
                from infra.sqlite_connection import _backup_native_source
                return self._validate_native(_backup_native_source(c.connection.driver_connection),foreign_keys=foreign_keys)
        if not self.local_path.exists():return {'status':'ok','category':'new_database','check':'not_created'}
        result=inspect_file(self.local_path,timeout_seconds=timeout_seconds or self.sqlite_settings.busy_timeout_ms/1000,
            busy_timeout_ms=self.sqlite_settings.busy_timeout_ms if busy_timeout_ms is None else busy_timeout_ms,
            foreign_keys=foreign_keys,cancel_event=cancel_event)
        return self._apply_integrity(result)

    def backup(self, target, *, cancel_event=None, progress=None,phase_observer=None):
        """Consistent SQLite online backup to a NEW file; never restore in place."""
        if self.engine.dialect.name!='sqlite':
            raise ContractError('DATABASE_BACKUP_UNAVAILABLE','该备份实现仅支持 SQLite')
        from infra.sqlite_backup import backup
        return backup(self,target,cancel_event=cancel_event,progress=progress,phase_observer=phase_observer)

    def close(self) -> None:
        self._backup_shutdown.set()
        self.engine.dispose()
