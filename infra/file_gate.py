"""Reentrant local file leases; async waiters never occupy I/O worker threads."""
from contextlib import contextmanager,asynccontextmanager
from contextvars import ContextVar
import fcntl,os,time,stat
from pathlib import Path
from threading import RLock,Lock,local,get_ident
from functools import partial
import anyio
from packages.config.errors import ContractError
_registry={};_registry_lock=RLock();_borrowed=ContextVar('borrowed_file_leases',default=());_DEFAULT=object()


def _execution_owner():
    try:task=anyio.get_current_task().id
    except RuntimeError:task=None
    return os.getpid(),get_ident(),task


class _Resource:
    def __init__(self,release,descriptor=None):
        self.release,self.mutex,self.references,self.closed=release,Lock(),0,False
        self.descriptor=descriptor
    def grant(self,lease,parent):
        with self.mutex:
            if self.closed or (parent is not None and not parent.active):
                raise ContractError('LEASE_EXPIRED','父租约已撤销，不能启动借用操作')
            self.references+=1;lease.active=True
    def revoke(self,lease):
        with self.mutex:
            if not lease.active:return
            lease.active=False;self.references-=1;last=self.references==0
            if last:self.closed=True
        if last:self.release()


class _Lease:
    def __init__(self,gate,resource=None,parent=None):
        self.gate,self.owner,self.active=gate,_execution_owner(),False
        self.resource=resource if parent is None else parent.resource
        self.resource.grant(self,parent)
    def revoke(self):self.resource.revoke(self)
    def live(self):return self.active and self.owner[0]==os.getpid() and not self.resource.closed
    def valid_here(self):return self.live() and self.owner==_execution_owner()


class FileGate:
    def __init__(self,path,wait_timeout=None,poll_seconds=.01,error_code='RESOURCE_LOCK_TIMEOUT'):
        self.path=Path(path) if path else None;self.lock=RLock();self.local=local()
        self.wait_timeout,self.poll_seconds,self.error_code=wait_timeout,poll_seconds,error_code
    def _current(self):
        leases=(*_borrowed.get(),getattr(self.local,'lease',None))
        return next((lease for lease in reversed(leases) if lease is not None and lease.gate is self and lease.valid_here()),None)
    def held(self):return self._current() is not None
    def _open(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        try:info=self.path.lstat()
        except FileNotFoundError:pass
        else:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                raise ContractError('RESOURCE_LOCK_IDENTITY_UNSAFE','协调文件必须为独立普通文件')
            if (info.st_mode&0o600)!=0o600:
                raise ContractError('RESOURCE_LOCK_QUARANTINED','协调文件已关闭访问，必须离线处置')
        return os.open(self.path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    def _write_fault_intent(self,payload):
        lease=self._current()
        if lease is None or lease.resource.descriptor is None:raise RuntimeError('A file-backed held gate is required')
        fd=lease.resource.descriptor
        if os.fstat(fd).st_size:return
        position=0
        while position<len(payload):
            written=os.pwrite(fd,payload[position:],position)
            if written<=0:raise OSError('Fault intent write made no progress')
            position+=written
        os.fsync(fd)
        directory=os.open(self.path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    def _retain_failure_lock(self):
        lease=self._current()
        if lease is None:raise RuntimeError('Cannot retain an unheld isolation gate')
        return _Lease(self,parent=lease)
    def _disable_fault_access(self):
        lease=self._current()
        if lease is None or lease.resource.descriptor is None:raise RuntimeError('Held file gate required')
        # A final metadata latch does not require allocating content blocks.
        # No application path re-enables these permissions.
        fd=lease.resource.descriptor;os.fchmod(fd,0);os.fsync(fd)
        directory=os.open(self.path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    def try_acquire_raw(self):
        fd=self._open()
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);return fd
        except BlockingIOError:os.close(fd);return None
        except BaseException:os.close(fd);raise
    def _expired(self,start,timeout):
        if timeout is not None and time.monotonic()-start>=timeout:
            raise ContractError(self.error_code,'资源租约等待超时；未获得授权，未读取媒体，请稍后重试')
    def acquire_raw(self,timeout=_DEFAULT):
        if self.path is None:return None
        timeout=self.wait_timeout if timeout is _DEFAULT else timeout;start=time.monotonic()
        while True:
            fd=self.try_acquire_raw()
            if fd is not None:return fd
            self._expired(start,timeout);time.sleep(self.poll_seconds)
    @staticmethod
    def release_raw(fd):
        if fd is not None:
            try:fcntl.flock(fd,fcntl.LOCK_UN)
            finally:os.close(fd)
    @contextmanager
    def hold(self,timeout=_DEFAULT):
        if self.held():yield;return
        if self.path is None:
            timeout=self.wait_timeout if timeout is _DEFAULT else timeout;start=time.monotonic()
            previous=getattr(self.local,'lease',None)
            if previous is not None and previous.live():
                raise ContractError('LEASE_OWNER_MISMATCH','同步内存租约不得跨异步任务借用')
            while not self.lock.acquire(blocking=False):self._expired(start,timeout);time.sleep(self.poll_seconds)
            lease=self.local.lease=_Lease(self,resource=_Resource(self.lock.release))
            try:yield
            finally:self.local.lease=previous;lease.revoke()
            return
        fd=self.acquire_raw(timeout);previous=getattr(self.local,'lease',None)
        lease=self.local.lease=_Lease(self,resource=_Resource(lambda:self.release_raw(fd),fd))
        try:yield
        finally:self.local.lease=previous;lease.revoke()
    @asynccontextmanager
    async def async_hold(self,timeout=_DEFAULT):
        if self.held():yield;return
        if self.path is None:raise ValueError('Async media leases require a file-backed identity')
        timeout=self.wait_timeout if timeout is _DEFAULT else timeout;start=time.monotonic();fd=None;lease=None
        try:
            while fd is None:
                fd=self.try_acquire_raw()
                if fd is not None:break
                self._expired(start,timeout);await anyio.sleep(self.poll_seconds)
            lease=_Lease(self,resource=_Resource(lambda:self.release_raw(fd),fd));token=_borrowed.set((*_borrowed.get(),lease))
            try:yield
            finally:_borrowed.reset(token)
        finally:
            if lease is None:self.release_raw(fd)
            else:lease.revoke()
    async def run_sync(self,func,*args,**kwargs):
        async with self.async_hold():
            lease=self._current()
            def bridge():
                # Explicit, revocable delegation to this worker only. A copied
                # context in any detached task is neither the owner nor a grant.
                grant=_Lease(self,parent=lease);token=_borrowed.set((*_borrowed.get(),grant))
                try:return func(*args,**kwargs)
                finally:grant.revoke();_borrowed.reset(token)
            # Default cancellation waits for the worker before revoking its parent.
            return await anyio.to_thread.run_sync(bridge)


def file_gate(path,**settings):
    key=str(Path(path).resolve()) if path else None
    with _registry_lock:
        instance=_registry.setdefault((os.getpid(),key),FileGate(path,**settings))
        for name,value in settings.items():setattr(instance,name,value)
        return instance
