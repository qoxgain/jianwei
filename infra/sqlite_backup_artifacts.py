"""Finish FAILED staging ownership before returning it to an ordinary caller.

The untouched SQLite image/sidecars are retained inside a non-database envelope.
The normal staging path gets an embedded Backup identity; if it cannot be edited,
that path itself becomes a lossless evidence envelope, never a writable SQLite.
Only the current operation's NEW, closed staging files are transformed.
"""
import hashlib,json,os,sqlite3,stat,time,uuid,zipfile
from pathlib import Path
from infra.sqlite_admission import mark_archive,read_identity
from infra.sqlite_isolation import sync_directory


def seal_failed(staging,operation,timeout_seconds):
    deadline=time.monotonic()+timeout_seconds
    evidence=staging.parent/('sqlite-failure-evidence-'+operation+'.zip')
    temporary=staging.with_name(staging.name+'.sealed-'+uuid.uuid4().hex)
    files=[p for p in [staging,Path(str(staging)+'-wal'),Path(str(staging)+'-shm'),Path(str(staging)+'-journal')] if p.exists()]
    contents={}
    def check():
        if time.monotonic()>=deadline:raise TimeoutError('failed-artifact seal deadline exceeded')
    try:
        with zipfile.ZipFile(evidence,'x',zipfile.ZIP_DEFLATED) as z:
            for index,path in enumerate(files):
                check();name='database.sqlite' if index==0 else 'database.sqlite'+str(path)[len(str(staging)):]
                info=path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise OSError('unsafe staging identity')
                digest=hashlib.sha256();size=0
                with path.open('rb') as original,z.open(name,'w') as output:
                    while chunk:=original.read(1024*1024):check();digest.update(chunk);size+=len(chunk);output.write(chunk)
                contents[name]={'sha256':digest.hexdigest(),'bytes':size}
            z.writestr('manifest.json',json.dumps({'format':'failed-sqlite-evidence-v1','operation_id':operation,'files':contents}))
        with evidence.open('rb') as stream:os.fsync(stream.fileno())
        sync_directory(evidence.parent);check()
        try:
            identity=read_identity(staging)
            if identity is None or identity.get('role')!='Backup':identity=mark_archive(staging,operation)
        except (sqlite3.Error,ValueError,TypeError,IndexError) as error:
            # No unsafe in-place repair of a malformed/locked partial. Original
            # bytes remain inside evidence; the visible path is non-database.
            with evidence.open('rb') as source,temporary.open('xb') as output:
                while chunk:=source.read(1024*1024):check();output.write(chunk)
                output.flush();os.fsync(output.fileno())
            os.replace(temporary,staging);sync_directory(staging.parent)
            identity=None;artifact_format='failed-sqlite-evidence-v1'
            identity_error={'type':type(error).__name__,'sqlite_errorname':getattr(error,'sqlite_errorname',None)}
        else:artifact_format='sqlite-with-backup-identity';identity_error=None
        with staging.open('rb') as stream:
            digest=hashlib.sha256()
            while chunk:=stream.read(1024*1024):check();digest.update(chunk)
        return {'format':artifact_format,'raw_evidence':evidence.name,'raw_files':contents,'identity':identity,
            'sealed_sha256':digest.hexdigest(),'identity_seal_error':identity_error}
    except BaseException:
        # No silent fallback to readable unsealed Active bytes after storage or
        # finalization failure. The caller also persists a source stop intent.
        for path in files:
            if path.exists():path.chmod(0)
        sync_directory(staging.parent)
        raise
    finally:
        if temporary.exists():temporary.unlink()
