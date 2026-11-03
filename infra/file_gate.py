"""Reentrant local-process and local-filesystem exclusion; never a cluster lock."""
from contextlib import contextmanager
import fcntl,os
from pathlib import Path
from threading import RLock,local

_registry={};_registry_lock=RLock()


class FileGate:
    def __init__(self,path):self.path=Path(path) if path else None;self.lock=RLock();self.local=local()
    def acquire_raw(self):
        if self.path is None:return None
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR,0o600)
        try:fcntl.flock(fd,fcntl.LOCK_EX)
        except BaseException:os.close(fd);raise
        return fd
    @staticmethod
    def release_raw(fd):
        if fd is not None:
            fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
    @contextmanager
    def hold(self):
        with self.lock:
            depth=getattr(self.local,'depth',0);fd=self.acquire_raw() if not depth else None
            self.local.depth=depth+1
            try:yield
            finally:
                self.local.depth=depth
                if not depth:self.release_raw(fd)


def file_gate(path):
    key=str(Path(path).resolve()) if path else None
    with _registry_lock:return _registry.setdefault((os.getpid(),key),FileGate(path))
