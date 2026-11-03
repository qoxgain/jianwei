"""SQLite DBAPI transaction gate: shared quarantine and commit ordering on one host."""
import re,sqlite3
from packages.config.errors import ContractError

WRITE_ACTIONS={getattr(sqlite3,n) for n in ('SQLITE_INSERT','SQLITE_UPDATE','SQLITE_DELETE',
 'SQLITE_CREATE_INDEX','SQLITE_CREATE_TABLE','SQLITE_CREATE_TEMP_INDEX','SQLITE_CREATE_TEMP_TABLE',
 'SQLITE_CREATE_TEMP_TRIGGER','SQLITE_CREATE_TEMP_VIEW','SQLITE_CREATE_TRIGGER','SQLITE_CREATE_VIEW',
 'SQLITE_DROP_INDEX','SQLITE_DROP_TABLE','SQLITE_DROP_TEMP_INDEX','SQLITE_DROP_TEMP_TABLE',
 'SQLITE_DROP_TEMP_TRIGGER','SQLITE_DROP_TEMP_VIEW','SQLITE_DROP_TRIGGER','SQLITE_DROP_VIEW',
 'SQLITE_ALTER_TABLE','SQLITE_REINDEX','SQLITE_ANALYZE')}


class GuardedConnection(sqlite3.Connection):
    def __init__(self,*args,_owner,**kwargs):
        super().__init__(*args,**kwargs);self.owner=_owner;self.authorization_error=None
        self.set_authorizer(self._authorize)
    def __setattr__(self,name,value):
        if name in {'isolation_level','autocommit'} and hasattr(self,'owner'):
            # CPython setters can COMMIT natively, bypassing commit().
            with self.owner.commit_gate.hold():
                try:self.owner._assert_healthy()
                except ContractError:self.rollback();raise
                return super().__setattr__(name,value)
        return super().__setattr__(name,value)
    def blobopen(self,*args,**kwargs):
        raise ContractError('DATABASE_POLICY_UNAVAILABLE','受管理连接不支持绕过SQL事务的blob写入')
    def _authorize(self,action,arg1,arg2,db,trigger):
        try:
            if action in WRITE_ACTIONS or (action==sqlite3.SQLITE_PRAGMA and arg2 is not None and arg1.lower() in
                {'journal_mode','wal_checkpoint','auto_vacuum','page_size','user_version','schema_version','application_id'}):
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
        with self.owner.commit_gate.hold():
            try:
                self.owner._assert_healthy();return super().commit()
            except BaseException as error:
                super().rollback();self.owner._record_sqlite_fault(error);raise
    def rollback(self):return super().rollback()
    def __enter__(self):
        with self.owner.commit_gate.hold():self.owner._assert_healthy()
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
        first=sql.split(None,1)[0].upper().rstrip(';') if sql else ''
        controls={'BEGIN','COMMIT','END','ROLLBACK','SAVEPOINT','RELEASE','PRAGMA','VACUUM'}
        def checked():
            try:c.owner._assert_healthy()
            except ContractError:c.rollback();raise
        def execute():
            c.authorization_error=None
            try:return method(*args)
            except BaseException as error:
                c.owner._record_sqlite_fault(error)
                if c.authorization_error is not None:raise c.authorization_error from None
                raise
        if first in controls and first!='BEGIN':
            with c.owner.commit_gate.hold():
                if first!='ROLLBACK':checked()
                return execute()
        with c.owner.commit_gate.hold():
            checked()
            # Include DDL and autocommit-mode callers in explicit transactions.
            # Never hold the process gate while waiting for another SQLite writer:
            # that writer must be able to enter the commit gate to release its lock.
            if not c.in_transaction and first not in {'SELECT','EXPLAIN','BEGIN'}:super().execute('BEGIN')
        return execute()
    def execute(self,*args):return self._run(super().execute,*args)
    def executemany(self,*args):return self._run(super().executemany,*args)
    def executescript(self,*args):
        raise ContractError('DATABASE_POLICY_UNAVAILABLE','受管理连接禁止隐式提交的executescript')
