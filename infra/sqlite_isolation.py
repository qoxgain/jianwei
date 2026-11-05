"""Durable stop-admission intent, independent of a busy SQLite commit gate.

Creating the directory is the atomic fail-closed claim; even missing/partial
metadata means STOP. Completed metadata is published atomically, never cleared.
No original SQLite database file is opened through ordinary file descriptors.
"""
import json,os,stat,uuid


def sync_directory(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def read_intent(path):
    if path is None:return None
    try:info=path.lstat()
    except FileNotFoundError:return None
    except OSError:return {'code':'DATABASE_UNAVAILABLE','phase':'intent_pending','reason':'unreadable_fault_intent'}
    if not stat.S_ISDIR(info.st_mode):
        return {'code':'DATABASE_UNAVAILABLE','phase':'intent_pending','reason':'unsafe_fault_intent'}
    try:
        record=path/'record.json';info=record.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('unsafe metadata')
        value=json.loads(record.read_text())
        if not isinstance(value,dict):raise ValueError('invalid metadata')
        return value
    except (OSError,ValueError):
        return {'code':'DATABASE_UNAVAILABLE','phase':'intent_pending','reason':'incomplete_fault_intent'}


def publish_intent(path,value):
    """No commit gate or SQLite lock. A crash after claim cannot reopen admission."""
    try:path.mkdir(mode=0o700)
    except FileExistsError:return read_intent(path)
    sync_directory(path.parent)
    temporary=path/('record-'+uuid.uuid4().hex+'.writing')
    try:
        with temporary.open('x') as stream:
            json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
        os.link(temporary,path/'record.json');sync_directory(path)
    finally:
        if temporary.exists():temporary.unlink()
    return value


def close_coordination_access(path):
    """Emergency metadata latch without waiting on flock; never acquire SQLite."""
    descriptor=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise OSError('unsafe coordination file')
        os.fchmod(descriptor,0);os.fsync(descriptor);sync_directory(path.parent)
    finally:os.close(descriptor)
