"""A DBAPI facade, deliberately not sqlite3.Connection: no native backup target."""
from .sqlite_guard import GuardedConnection
from packages.config.errors import ContractError


class ManagedConnection:
    __slots__=('__native',)
    _properties={'isolation_level','autocommit','in_transaction','total_changes','row_factory','text_factory'}
    def __init__(self,*args,_owner,**kwargs):
        object.__setattr__(self,'_ManagedConnection__native',GuardedConnection(*args,_owner=_owner,**kwargs))
    def __getattr__(self,name):
        if name in self._properties:return getattr(self.__native,name)
        raise AttributeError('Unsupported managed SQLite attribute: '+name)
    def __setattr__(self,name,value):
        if name not in self._properties:raise AttributeError('Unsupported managed SQLite attribute: '+name)
        setattr(self.__native,name,value)
    def cursor(self,*args,**kwargs):return ManagedCursor(self,self.__native.cursor(*args,**kwargs))
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
    __slots__=('__cursor','__connection')
    _properties={'description','rowcount','lastrowid','arraysize','row_factory'}
    def __init__(self,connection,cursor):
        object.__setattr__(self,'_ManagedCursor__cursor',cursor);object.__setattr__(self,'_ManagedCursor__connection',connection)
    @property
    def connection(self):return self.__connection
    def __getattr__(self,name):
        if name in self._properties:return getattr(self.__cursor,name)
        raise AttributeError('Unsupported managed SQLite cursor attribute: '+name)
    def __setattr__(self,name,value):
        if name not in self._properties:raise AttributeError('Unsupported managed SQLite cursor attribute: '+name)
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


def _backup_to_new_file(connection,target):
    """Internal capability only used after Database.backup exclusively reserves target."""
    native=object.__getattribute__(connection,'_ManagedConnection__native')
    with native.owner.commit_gate.hold():
        native.owner._assert_healthy();native.backup(target)
