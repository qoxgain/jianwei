"""Reentrant local file leases; async waiters never occupy I/O worker threads."""
from contextlib import contextmanager,asynccontextmanager
from contextvars import ContextVar
import fcntl,os,time
from pathlib import Path
from threading import RLock,local
from functools import partial
import anyio
from packages.config.errors import ContractError
_registry={};_registry_lock=RLock();_borrowed=ContextVar('borrowed_file_leases',default=());_DEFAULT=object()


class FileGate:
    def __init__(self,path,wait_timeout=None,poll_seconds=.01,error_code='RESOURCE_LOCK_TIMEOUT'):
        self.path=Path(path) if path else None;self.lock=RLock();self.local=local()
        self.wait_timeout,self.poll_seconds,self.error_code=wait_timeout,poll_seconds,error_code
    def held(self):return self in _borrowed.get() or bool(getattr(self.local,'depth',0))
    def _open(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        return os.open(self.path,os.O_CREAT|os.O_RDWR,0o600)
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
            with self.lock:
                self.local.depth=1
                try:yield
                finally:self.local.depth=0
            return
        fd=self.acquire_raw(timeout);self.local.depth=1
        try:yield
        finally:self.local.depth=0;self.release_raw(fd)
    @asynccontextmanager
    async def async_hold(self,timeout=_DEFAULT):
        if self.held():yield;return
        if self.path is None:raise ValueError('Async media leases require a file-backed identity')
        timeout=self.wait_timeout if timeout is _DEFAULT else timeout;start=time.monotonic();fd=None
        try:
            while fd is None:
                fd=self.try_acquire_raw()
                if fd is not None:break
                self._expired(start,timeout);await anyio.sleep(self.poll_seconds)
            token=_borrowed.set((*_borrowed.get(),self))
            try:yield
            finally:_borrowed.reset(token)
        finally:self.release_raw(fd)
    async def run_sync(self,func,*args,**kwargs):
        async with self.async_hold():
            # The context grants this already-held lease to the bounded sync call.
            # Cancellation waits for any running call before releasing its lease.
            return await anyio.to_thread.run_sync(partial(func,*args,**kwargs))


def file_gate(path,**settings):
    key=str(Path(path).resolve()) if path else None
    with _registry_lock:
        instance=_registry.setdefault((os.getpid(),key),FileGate(path,**settings))
        for name,value in settings.items():setattr(instance,name,value)
        return instance
