"""A DBAPI facade, deliberately not sqlite3.Connection: no native backup target."""
import sqlite3
from .sqlite_guard import GuardedConnection
from packages.config.errors import ContractError


def _row_factory(connection,factory):
    # SQLite passes its native cursor to callbacks. Expose the supported facade
    # instead, so cursor.connection cannot become a native backup target.
    if factory is None or factory is sqlite3.Row:return factory
    def guarded(cursor,row):return factory(ManagedCursor(connection,cursor,factory),row)
    return guarded


class ManagedConnection:
    __slots__=('__native','__row_factory')
    _properties={'isolation_level','autocommit','in_transaction','total_changes','row_factory','text_factory'}
    def __init__(self,*args,_owner,**kwargs):
        _owner._prepare_business()
        object.__setattr__(self,'_ManagedConnection__native',GuardedConnection(*args,_owner=_owner,**kwargs))
        object.__setattr__(self,'_ManagedConnection__row_factory',None)
    def __getattr__(self,name):
        if name=='row_factory':return self.__row_factory
        if name in self._properties:return getattr(self.__native,name)
        raise AttributeError('Unsupported managed SQLite attribute: '+name)
    def __setattr__(self,name,value):
        if name not in self._properties:raise AttributeError('Unsupported managed SQLite attribute: '+name)
        if name=='row_factory':
            self.__native.row_factory=_row_factory(self,value)
            object.__setattr__(self,'_ManagedConnection__row_factory',value);return
        setattr(self.__native,name,value)
    def cursor(self,*args,**kwargs):return ManagedCursor(self,self.__native.cursor(*args,**kwargs),self.__row_factory)
    def execute(self,*args,**kwargs):return self.cursor().execute(*args,**kwargs)
    def executemany(self,*args,**kwargs):return self.cursor().executemany(*args,**kwargs)
    def executescript(self,*args,**kwargs):return self.__native.executescript(*args,**kwargs)
    def commit(self):return self.__native.commit()
    def rollback(self):return self.__native.rollback()
    def close(self):return self.__native.close()
    def create_function(self,*args,**kwargs):return self.__native.create_function(*args,**kwargs)
    def set_trace_callback(self,*args,**kwargs):return self.__native.set_trace_callback(*args,**kwargs)
    def set_authorizer(self,*args,**kwargs):return self.__native.set_authorizer(*args,**kwargs)
    def interrupt(self):return self.__native.interrupt()
    def blobopen(self,*args,**kwargs):return self.__native.blobopen(*args,**kwargs)
    def backup(self,*args,**kwargs):
        raise ContractError('DATABASE_BACKUP_UNAVAILABLE','受管理连接不公开原生backup；仅允许Database.backup创建新备份')
    def __enter__(self):self.__native.__enter__();return self
    def __exit__(self,*args):return self.__native.__exit__(*args)


class ManagedCursor:
    __slots__=('__cursor','__connection','__row_factory')
    _properties={'description','rowcount','lastrowid','arraysize','row_factory'}
    def __init__(self,connection,cursor,row_factory=None):
        object.__setattr__(self,'_ManagedCursor__cursor',cursor);object.__setattr__(self,'_ManagedCursor__connection',connection)
        object.__setattr__(self,'_ManagedCursor__row_factory',row_factory)
    @property
    def connection(self):return self.__connection
    def __getattr__(self,name):
        if name=='row_factory':return self.__row_factory
        if name in self._properties:return getattr(self.__cursor,name)
        raise AttributeError('Unsupported managed SQLite cursor attribute: '+name)
    def __setattr__(self,name,value):
        if name not in self._properties:raise AttributeError('Unsupported managed SQLite cursor attribute: '+name)
        if name=='row_factory':
            self.__cursor.row_factory=_row_factory(self.__connection,value)
            object.__setattr__(self,'_ManagedCursor__row_factory',value);return
        setattr(self.__cursor,name,value)
    def execute(self,*args,**kwargs):self.__cursor.execute(*args,**kwargs);return self
    def executemany(self,*args,**kwargs):self.__cursor.executemany(*args,**kwargs);return self
    def executescript(self,*args,**kwargs):return self.__connection.executescript(*args,**kwargs)
    def fetchone(self):return self.__cursor.fetchone()
    def fetchmany(self,*args):return self.__cursor.fetchmany(*args)
    def fetchall(self):return self.__cursor.fetchall()
    def close(self):return self.__cursor.close()
    def setinputsizes(self,*args):return self.__cursor.setinputsizes(*args)
    def setoutputsize(self,*args):return self.__cursor.setoutputsize(*args)
    def __iter__(self):return self
    def __next__(self):return next(self.__cursor)


def _backup_native_source(connection):
    """Private source capability for in-memory backup; never a supported target."""
    return object.__getattribute__(connection,'_ManagedConnection__native')


def _configure_connection(connection,owner):
    """Private connection-local PRAGMAs; never wait on schema under commit_gate."""
    native=_backup_native_source(connection);owner._assert_healthy()
    cursor=sqlite3.Connection.cursor(native,factory=sqlite3.Cursor)
    try:
        cursor.execute('PRAGMA foreign_keys = ON')
        cursor.execute('PRAGMA busy_timeout = '+str(owner.sqlite_settings.busy_timeout_ms))
        cursor.execute('PRAGMA synchronous = '+owner.sqlite_settings.synchronous)
    finally:cursor.close()
    owner._assert_healthy()
