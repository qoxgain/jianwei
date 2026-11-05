"""SQLite DBAPI transaction gate: shared quarantine and commit ordering on one host."""
import re,sqlite3
from contextlib import contextmanager,nullcontext
from packages.config.errors import ContractError

WRITE_ACTIONS={getattr(sqlite3,n) for n in ('SQLITE_INSERT','SQLITE_UPDATE','SQLITE_DELETE',
 'SQLITE_CREATE_INDEX','SQLITE_CREATE_TABLE','SQLITE_CREATE_TEMP_INDEX','SQLITE_CREATE_TEMP_TABLE',
 'SQLITE_CREATE_TEMP_TRIGGER','SQLITE_CREATE_TEMP_VIEW','SQLITE_CREATE_TRIGGER','SQLITE_CREATE_VIEW',
 'SQLITE_DROP_INDEX','SQLITE_DROP_TABLE','SQLITE_DROP_TEMP_INDEX','SQLITE_DROP_TEMP_TABLE',
 'SQLITE_DROP_TEMP_TRIGGER','SQLITE_DROP_TEMP_VIEW','SQLITE_DROP_TRIGGER','SQLITE_DROP_VIEW',
 'SQLITE_ALTER_TABLE','SQLITE_REINDEX','SQLITE_ANALYZE')}


def sqlite_operation(owner,method,*args):
    """Call a native DBAPI method once; retain its exception and provenance.

    All callers pass a bound sqlite3 C method, never a Python adapter. A SQLite
    exception raised by application row/text factories or parameter iterators
    has Python frames *below* this invocation. Such an exception is not evidence
    about this database (it can even originate from another SQLite file).
    SQLAlchemy's later handle_error sees the same exception and provenance.
    """
    try:return method(*args)
    except sqlite3.Error as error:
        if error.__traceback__.tb_next is not None:
            error._jianwei_sqlite_callback=True
        else:
            try:owner._record_sqlite_fault(error)
            except (ContractError,OSError) as isolation_error:
                # Keep DBAPI type/code/identity for both raw and SQLAlchemy users;
                # coordination/persistence failure is explicit, never discarded.
                error.add_note('SQLite isolation failed or pending: '+getattr(isolation_error,'code',type(isolation_error).__name__))
                raise error from isolation_error
        raise


class GuardedConnection(sqlite3.Connection):
    def __init__(self,*args,_owner,**kwargs):
        super().__init__(*args,**kwargs);self.owner=_owner;self.authorization_error=None
        self._write_pending=False;self._statement_writes=False
        super().set_authorizer(self._authorize)
        # Even a new instance that bypasses migrate must validate an existing
        # database before receiving a supported writable connection.
        self.owner._validate_native(self)
    def __setattr__(self,name,value):
        if name in {'isolation_level','autocommit'} and hasattr(self,'owner'):
            # CPython setters can COMMIT natively, bypassing commit().
            with self._healthy_boundary():
                try:self.owner._assert_healthy()
                except ContractError:self.rollback();raise
                self._before_commit()
                return sqlite_operation(self.owner,super().__setattr__,name,value)
        return super().__setattr__(name,value)
    def blobopen(self,*args,**kwargs):
        raise ContractError('DATABASE_POLICY_UNAVAILABLE','受管理连接不支持绕过SQL事务的blob写入')
    def set_authorizer(self,*args):
        raise ContractError('DATABASE_POLICY_UNAVAILABLE','受管理事务authorizer不可替换')
    def _authorize(self,action,arg1,arg2,db,trigger):
        try:
            if action in {sqlite3.SQLITE_TRANSACTION,sqlite3.SQLITE_SAVEPOINT}:
                operation=(arg1 or '').upper()
                if operation in {'COMMIT','RELEASE'}:
                    if not self.owner.commit_gate.held():
                        raise ContractError('DATABASE_POLICY_UNAVAILABLE','SQLite确认的提交操作未持有提交闸门')
                    self.owner._assert_healthy()
                elif operation=='BEGIN':self.owner._assert_healthy()
            if action in WRITE_ACTIONS or (action==sqlite3.SQLITE_PRAGMA and arg2 is not None and arg1.lower() in
                {'journal_mode','wal_checkpoint','auto_vacuum','page_size','user_version','schema_version','application_id'}):
                if '_jianwei_database_identity' in {arg1,arg2}:
                    raise ContractError('DATABASE_ADMISSION_PROTECTED','业务SQL不能改写内部数据库身份')
                self.owner._assert_healthy()
                self._statement_writes=True
                self.owner._check_space()
            if action==sqlite3.SQLITE_ATTACH:
                # VACUUM uses an internal attachment (empty name); no arbitrary managed cross-DB writes.
                if arg1:raise ContractError('DATABASE_POLICY_UNAVAILABLE','受管理连接不允许附加外部数据库')
            return sqlite3.SQLITE_OK
        except ContractError as error:self.authorization_error=error;return sqlite3.SQLITE_DENY
    def cursor(self,*args,**kwargs):
        kwargs['factory']=GuardedCursor
        return super().cursor(*args,**kwargs)
    def execute(self,sql,parameters=()):return self.cursor().execute(sql,parameters)
    def executemany(self,sql,parameters):return self.cursor().executemany(sql,parameters)
    def executescript(self,script):
        raise ContractError('DATABASE_POLICY_UNAVAILABLE','受管理连接禁止隐式提交的executescript；使用显式事务')
    def commit(self):
        with self._healthy_boundary():
            try:
                self._before_commit();result=sqlite_operation(self.owner,super().commit);self._write_pending=False;return result
            except BaseException as error:
                try:self.rollback()
                except sqlite3.Error as rollback_error:
                    error.add_note('SQLite rollback also failed: '+str(getattr(rollback_error,'sqlite_errorname',type(rollback_error).__name__)))
                raise
    def _before_commit(self):
        self.owner._assert_healthy()
        if self.in_transaction and self._write_pending and self.owner.sqlite_settings.commit_integrity_check=='full':
            # This connection already owns SQLite's writer lock. No new SQLite
            # connection or external reader waits inside the app commit gate.
            self.owner._validate_native(self)
    @contextmanager
    def _healthy_boundary(self):
        try:
            with self.owner._commit_boundary():yield
        except ContractError:
            self.rollback();raise
    def rollback(self):
        result=sqlite_operation(self.owner,super().rollback);self._write_pending=False;return result
    def __enter__(self):
        with self._healthy_boundary():pass
        return self
    def __exit__(self,kind,value,traceback):
        if kind is None:self.commit()
        else:self.rollback()
        return False


class GuardedCursor(sqlite3.Cursor):
    def _run(self,method,*args):
        c=self.connection
        # SQLite transaction control cannot hide behind CTEs; strip comments so
        # direct SQL COMMIT/END/RELEASE cannot evade the same commit ordering.
        sql=re.sub(r'/\*.*?\*/|--[^\n]*',' ',args[0],flags=re.S).lstrip('\ufeff \t\r\n;')
        keyword=re.match(r'[A-Za-z]+',sql)
        first=keyword[0].upper() if keyword else ''
        controls={'BEGIN','COMMIT','END','ROLLBACK','SAVEPOINT','RELEASE','PRAGMA','VACUUM'}
        def checked():
            try:c.owner._assert_healthy()
            except ContractError:c.rollback();raise
        def execute():
            c.authorization_error=None;c._statement_writes=False;changes=c.total_changes
            try:
                result=sqlite_operation(c.owner,method,*args)
                if c._statement_writes:c._write_pending=True
                if not c.in_transaction:c._write_pending=False
                return result
            except BaseException as error:
                if c.total_changes>changes:c._write_pending=True
                if c.authorization_error is not None:raise c.authorization_error from None
                raise
        if first in controls and first!='BEGIN':
            # ROLLBACK must remain possible after isolation to release SQLite locks.
            boundary=nullcontext if first=='ROLLBACK' else c._healthy_boundary
            with boundary():
                if first!='ROLLBACK':checked()
                if first in {'COMMIT','END','RELEASE'}:c._before_commit()
                return execute()
        with c._healthy_boundary():
            checked()
            # Include DDL and autocommit-mode callers in explicit transactions.
            # Never hold the process gate while waiting for another SQLite writer:
            # that writer must be able to enter the commit gate to release its lock.
            if not c.in_transaction and first not in {'SELECT','EXPLAIN','BEGIN'}:sqlite_operation(c.owner,super().execute,'BEGIN')
        return execute()
    def execute(self,*args):return self._run(super().execute,*args)
    def executemany(self,*args):return self._run(super().executemany,*args)
    def fetchone(self):return sqlite_operation(self.connection.owner,super().fetchone)
    def fetchmany(self,*args):return sqlite_operation(self.connection.owner,super().fetchmany,*args)
    def fetchall(self):return sqlite_operation(self.connection.owner,super().fetchall)
    def __next__(self):return sqlite_operation(self.connection.owner,super().__next__)
    def executescript(self,*args):
        raise ContractError('DATABASE_POLICY_UNAVAILABLE','受管理连接禁止隐式提交的executescript')
