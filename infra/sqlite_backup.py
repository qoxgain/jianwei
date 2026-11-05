"""Bounded online copy, isolated staging and health-ordered publication on one host."""
from contextlib import contextmanager,ExitStack
import hashlib,json,os,sqlite3,time,uuid
from pathlib import Path
from packages.config.errors import ContractError


@contextmanager
def source_connection(owner):
    path=owner.engine.url.database
    if path not in (None,'',':memory:'):
        # No managed connection setup/PRAGMA may wait for SQLite while holding
        # commit_gate. This capability is private, read-only and never a target.
        source=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=0)
        try:yield source
        finally:source.close()
    else:
        from infra.sqlite_connection import _backup_native_source
        with owner.engine.connect() as connection:
            yield _backup_native_source(connection.connection.driver_connection)


def backup(owner,target,*,cancel_event=None,progress=None,phase_observer=None):
    target=Path(target).absolute();target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() or target.is_symlink():raise FileExistsError(target)
    operation=uuid.uuid4().hex
    staging=target.with_name(target.name+'.partial-'+operation)
    receipt=target.with_name(target.name+'.backup-'+operation+'.json')
    settings=owner.sqlite_settings.backup
    deadline=time.monotonic()+settings.timeout_seconds
    state={'version':'sqlite-online-backup-v2','operation_id':operation,'state':'started',
        'target':target.name,'staging':staging.name,'original_overwritten':False,
        'source_state_modified':False,'started_ns':time.time_ns()}
    published=False

    def record():
        if staging.exists():
            from infra.sqlite_admission import tag
            state['lifecycle_tag_supported']=tag(staging,{'role':'Backup','state':state['state'],
                'operation_id':operation})
        temp=receipt.with_suffix('.writing')
        with temp.open('x') as stream:
            json.dump(state,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,receipt)
        fd=os.open(receipt.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)

    def check():
        if owner._backup_shutdown.is_set() or (cancel_event is not None and cancel_event.is_set()):
            raise ContractError('DATABASE_BACKUP_CANCELLED','备份已取消；未发布有效备份，保留诊断产物')
        if time.monotonic()>=deadline:
            raise ContractError('DATABASE_BACKUP_TIMEOUT','在线备份超过截止时间，未发布有效备份')
        owner._assert_healthy()

    @contextmanager
    def boundary():
        with ExitStack() as stack:
            while True:
                check()
                try:stack.enter_context(owner.commit_gate.hold(timeout=min(.02,max(.001,deadline-time.monotonic()))))
                except ContractError as error:
                    if error.code!='RESOURCE_LOCK_TIMEOUT':raise
                else:break
            yield

    def step(status,remaining,total):
        check()
        if progress is not None:progress(status,remaining,total)
        check()

    def phase(name):
        state['phase']=name;record()
        # Observers run with no SQLite connection/commit gate held.
        if phase_observer is not None:phase_observer(name,dict(state))
        check()

    def source_check(name):
        while True:
            check()
            try:
                result=owner.check_integrity(timeout_seconds=max(.001,deadline-time.monotonic()),
                    busy_timeout_ms=0,foreign_keys=False,cancel_event=cancel_event)
            except ContractError as error:
                if error.code not in {'DATABASE_INTEGRITY_BUSY','DATABASE_INTEGRITY_LOCKED'}:raise
                status=sqlite3.SQLITE_BUSY if error.code.endswith('BUSY') else sqlite3.SQLITE_LOCKED
                if progress is not None:progress(status,0,0)
                check();time.sleep(settings.retry_seconds)
            else:
                state.setdefault('source_integrity',[]).append({'phase':name,'status':result['status'],
                    'category':result['category'],'checked_ns':result.get('checked_ns')})
                return

    try:
        record()
        with boundary():check()
        source_check('before_copy')
        owner._prepare_business()
        descriptor=os.open(staging,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600);os.close(descriptor)
        state['state']='copying';record()
        phase('before_copy')
        with source_connection(owner) as source:
            output=sqlite3.connect(staging,timeout=0)
            try:
                source.backup(output,pages=settings.pages_per_step,progress=step,sleep=settings.retry_seconds)
            finally:output.close()
        from infra.sqlite_admission import mark_archive
        state['artifact_identity']=mark_archive(staging,operation)
        phase('after_copy');state['state']='validating';record()
        from infra.sqlite_health import inspect_file
        validation=inspect_file(staging,timeout_seconds=max(.001,deadline-time.monotonic()),
            busy_timeout_ms=0,foreign_keys=True,cancel_event=cancel_event)
        phase('after_validation');source_check('after_validation')
        if validation['status']!='ok':
            raise ContractError('DATABASE_BACKUP_INTEGRITY_FAILED','备份副本完整性校验失败，源库状态已独立复核')
        check()
        digest=hashlib.sha256()
        with staging.open('rb') as stream:
            while chunk:=stream.read(1024*1024):check();digest.update(chunk)
            os.fsync(stream.fileno())
        state.update(sha256=digest.hexdigest(),bytes=staging.stat().st_size)
        source_check('before_publish');phase('before_publish')
        with boundary():
            check()
            # Atomic no-replace publication. Only the small final state transition
            # holds the gate; no SQLite lock or copy/hash/integrity wait is inside.
            os.link(staging,target);published=True
            state.update(state='published',completed_ns=time.time_ns());record()
            staging.unlink()
        return {'status':'ok','sha256':state['sha256'],'bytes':state['bytes'],
            'original_overwritten':False,'receipt':str(receipt),'operation_id':operation}
    except BaseException as error:
        original_failure=type(error).__name__;copy_error=isinstance(error,sqlite3.DatabaseError)
        # Remove only this operation's not-yet-acknowledged publication BEFORE
        # changing staging provenance; never mutate another/historical target.
        if published and staging.exists() and target.exists() and target.stat().st_ino==staging.stat().st_ino:
            target.unlink();published=False
        # A DONE progress callback may cancel/raise after SQLite has copied a
        # completely valid Active source identity. Close/unwind first, then seal
        # ALL leftover artifacts before returning failure to an ordinary caller.
        if staging.exists():
            from infra.sqlite_backup_artifacts import seal_failed
            try:state['failed_artifact']=seal_failed(staging,operation,settings.timeout_seconds)
            except BaseException as sealing_error:
                state['artifact_seal_error']=type(sealing_error).__name__
                try:owner.fault={'reason':'backup_artifact_sealing_failed','classification':'admission_safety_stop',
                    'root_cause':'not_established','operation_id':operation}
                except ContractError:pass # Persistent intent may be pending; never resume normal writes.
                error=ContractError('DATABASE_BACKUP_SEAL_FAILED','失败备份无法安全封装；已关闭产物访问并停止源业务准入，须停止服务离线处置')
        # A corrupt copy alone does not prove a corrupt source. Confirm source
        # separately after SQLite resources have unwound, never under commit_gate.
        if copy_error:
            state['copy_exception_type']=original_failure
            try:source_check('after_copy_error')
            except ContractError as diagnosed:
                if getattr(error,'code',None)!='DATABASE_BACKUP_SEAL_FAILED':error=diagnosed
        # Undo only this operation's link if final receipt failed. Existing files
        # and earlier valid backups are never overwritten or removed.
        if published and staging.exists() and target.exists() and target.stat().st_ino==staging.stat().st_ino:target.unlink()
        code=getattr(error,'code',None)
        if code=='RESOURCE_LOCK_TIMEOUT':code='DATABASE_BACKUP_TIMEOUT'
        if isinstance(error,(KeyboardInterrupt,SystemExit)):code='DATABASE_BACKUP_CANCELLED'
        if code is None:
            try:check()
            except ContractError as controlled:code=controlled.code
        code=code or 'DATABASE_BACKUP_FAILED'
        state.update(state='invalidated' if owner.fault is not None or code=='DATABASE_UNAVAILABLE' else 'cancelled' if code=='DATABASE_BACKUP_CANCELLED'
            else 'timeout' if code=='DATABASE_BACKUP_TIMEOUT' else 'failed',error_code=code,
            exception_type=type(error).__name__,completed_ns=time.time_ns())
        try:record()
        except OSError:pass # If storage itself fails, retain any existing receipt/partial for offline diagnosis.
        if isinstance(error,(KeyboardInterrupt,SystemExit,FileExistsError)):raise
        raise ContractError(code,'备份未发布为有效成果；原库/历史备份不变，保留失败副本及诊断记录',
            [{'path':'backup','message':'operation='+operation+'; state='+state['state']}]) from None
