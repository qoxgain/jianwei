"""Business identity is explicit; an archive is never an automatic writable DB.

The embedded record travels with copies. Path/inode registration and optional
filesystem lifecycle tags cover the copy's not-yet-complete stages. Unknown old
files require an offline, hash-bound legacy review; file names are not authority.
"""
from contextlib import closing
import errno,hashlib,json,os,sqlite3,stat,subprocess,sys,time,uuid
from pathlib import Path
from infra.file_gate import file_gate
from infra.sqlite_isolation import sync_directory
from packages.config.errors import ContractError

TABLE='_jianwei_database_identity'
TAG='user.jianwei.lifecycle'
VERSION='sqlite-business-admission-v1'


def binding(path):return hashlib.sha256(str(path.resolve()).encode()).hexdigest()


def atomic_record(path,value):
    temporary=path.with_name(path.name+'.writing-'+uuid.uuid4().hex)
    try:
        with temporary.open('x') as stream:json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
        os.link(temporary,path);sync_directory(path.parent)
    finally:
        if temporary.exists():temporary.unlink()


def lifecycle(path):
    try:
        record=json.loads(os.getxattr(path,TAG))
        if not isinstance(record,dict):raise ValueError('invalid lifecycle')
        return record
    except OSError as e:
        if e.errno in {errno.ENODATA,errno.ENOENT,errno.ENOTSUP,errno.EOPNOTSUPP,errno.ENOSYS}:return None
        raise ContractError('DATABASE_ADMISSION_UNAVAILABLE','无法核查数据库生命周期，禁止业务写入') from None
    except ValueError:raise ContractError('DATABASE_ADMISSION_INVALID','生命周期记录无效，禁止业务写入') from None


def tag(path,value):
    try:os.setxattr(path,TAG,json.dumps(value).encode());return True
    except OSError as e:
        if e.errno in {errno.ENOTSUP,errno.EOPNOTSUPP,errno.ENOSYS}:return False
        raise


def read_identity(path,*,timeout_seconds=0):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=timeout_seconds)) as c:
        exists=c.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',('table',TABLE)).fetchone()
        if not exists:return None
        rows=c.execute('SELECT record_json FROM '+TABLE).fetchall()
        if len(rows)!=1:raise ContractError('DATABASE_ADMISSION_INVALID','数据库身份记录必须唯一')
        try:record=json.loads(rows[0][0])
        except (ValueError,TypeError):raise ContractError('DATABASE_ADMISSION_INVALID','数据库身份JSON无效') from None
        if not isinstance(record,dict) or record.get('version')!=VERSION:
            raise ContractError('DATABASE_ADMISSION_INVALID','数据库身份协议无效')
        return record


def assert_business(record,path):
    if record is None:
        raise ContractError('DATABASE_LEGACY_REVIEW_REQUIRED','未登记的已有SQLite不能自动成为业务库；须停写、完整检查并按SHA离线审核登记')
    if record.get('role')!='Active':
        raise ContractError('DATABASE_RESTORE_REVIEW_REQUIRED','备份/暂存/恢复待审文件不得作为可写业务库')
    if record.get('path_sha256')!=binding(path):
        raise ContractError('DATABASE_IDENTITY_REBIND_REQUIRED','数据库已复制或移动；不得自动重绑定业务身份，须离线审核并保留隔离/消费台账')


def fast_check(owner):
    """No SQLite queries/locks in the commit path; identity was checked on open."""
    path=owner.local_path
    if path is None:return
    cached=owner._business_registration
    if cached is not None:
        try:info=owner.admission_path.lstat()
        except OSError:raise ContractError('DATABASE_ADMISSION_UNAVAILABLE','业务登记丢失，暂停写入') from None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or (info.st_dev,info.st_ino)!=owner._registration_identity:
            raise ContractError('DATABASE_ADMISSION_INVALID','业务登记文件身份已变化，暂停写入')
    else:
        role=lifecycle(path)
        if role is not None and role.get('role')!='Active':
            raise ContractError('DATABASE_RESTORE_REVIEW_REQUIRED','该文件是备份或未批准的暂存产物，禁止业务写入')


def write_identity(c,record):
    c.execute('CREATE TABLE IF NOT EXISTS '+TABLE+'(record_json TEXT NOT NULL)')
    c.execute('DELETE FROM '+TABLE);c.execute('INSERT INTO '+TABLE+' VALUES(?)',(json.dumps(record),))


def register(owner,record,*,new=False):
    path=owner.local_path
    with closing(sqlite3.connect(path,timeout=owner.sqlite_settings.busy_timeout_ms/1000)) as c:
        c.execute('PRAGMA synchronous = '+owner.sqlite_settings.synchronous)
        write_identity(c,record)
        # This connection already owns SQLite's writer lock. Only COMMIT is
        # under the gate; no new SQLite lock wait is introduced inside it.
        with owner._commit_boundary():c.commit()
    info=path.stat()
    registry={**record,'device':info.st_dev,'inode':info.st_ino}
    atomic_record(owner.admission_path,registry)
    tag(path,{'role':'Active','state':'Active','database_uuid':record['database_uuid']})
    cache(owner,record)


def cache(owner,record):
    try:
        info=owner.admission_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('unsafe registry')
        registry=json.loads(owner.admission_path.read_text());physical=owner.local_path.stat()
        if not isinstance(registry,dict):raise ValueError('invalid registry')
        if registry.get('database_uuid')!=record.get('database_uuid') or registry.get('role')!='Active' or \
            (registry.get('device'),registry.get('inode'))!=(physical.st_dev,physical.st_ino) or registry.get('path_sha256')!=binding(owner.local_path):
            raise ValueError('mismatched registration')
    except (OSError,ValueError,TypeError):
        raise ContractError('DATABASE_ADMISSION_INVALID','业务登记缺失或与物理数据库不匹配，须离线处理，禁止自动修复') from None
    owner._business_registration=record;owner._registration_identity=(info.st_dev,info.st_ino)


def prepare(owner):
    path=owner.local_path
    if path is None:return
    owner._assert_healthy()
    if owner._business_registration is not None:return
    if owner.commit_gate.held():
        raise ContractError('DATABASE_ADMISSION_CHECK_REQUIRED','新业务连接须在提交闸门外完成准入检查')
    with file_gate(str(path)+'.admission.lock',wait_timeout=owner.sqlite_settings.busy_timeout_ms/1000).hold():
        owner._assert_healthy()
        if not path.exists():
            # An interrupted initialization leaves an unregistered file, which
            # cannot be mistaken for a new approved DB on the next process.
            descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600);os.close(descriptor)
            record={'version':VERSION,'role':'Active','database_uuid':uuid.uuid4().hex,'path_sha256':binding(path),
                'origin':'new_business_database','registered_ns':time.time_ns()}
            register(owner,record,new=True);return
        role=lifecycle(path)
        if role is not None and role.get('role')!='Active':assert_business({'role':'Backup'},path)
        try:record=read_identity(path,timeout_seconds=owner.sqlite_settings.busy_timeout_ms/1000)
        except sqlite3.Error as e:
            owner._record_sqlite_fault(e)
            if getattr(e,'sqlite_errorcode',0)&255 in {sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED}:
                raise ContractError('DATABASE_ADMISSION_BUSY','业务身份检查遇到锁竞争，未永久隔离，可稍后重试') from None
            owner.check_integrity();raise ContractError('DATABASE_ADMISSION_UNAVAILABLE','无法读取数据库身份') from None
        if record is None:
            # Preserve actual corruption/foreign-key classification before an
            # unregistered legacy file is offered to an offline reviewer.
            owner.check_integrity()
        assert_business(record,path);cache(owner,record)


def digest_separate_process(path):
    code='import hashlib,sys; f=open(sys.argv[1],"rb"); print(hashlib.file_digest(f,"sha256").hexdigest()); f.close()'
    # Never close a bare DB FD in the application's SQLite PID (A.3 lock fix).
    return subprocess.check_output([sys.executable,'-c',code,str(path)],text=True,timeout=60).strip()


def register_legacy(owner,review):
    path=owner.local_path
    required={'reviewer','reference','expected_sha256','writers_stopped','consumption_reviewed','legacy_business'}
    if path is None or not path.is_file() or not isinstance(review,dict) or not required<=review.keys() or \
        not all(review.get(k) for k in required):
        raise ContractError('DATABASE_LEGACY_REVIEW_REQUIRED','旧业务库须具名审核、精确SHA及停写/消费台账复核；不批准备份恢复')
    owner._assert_healthy()
    with file_gate(str(path)+'.admission.lock',wait_timeout=owner.sqlite_settings.busy_timeout_ms/1000).hold():
        role=lifecycle(path)
        if role is not None and role.get('role')!='Active':assert_business({'role':'Backup'},path)
        try:existing=read_identity(path,timeout_seconds=owner.sqlite_settings.busy_timeout_ms/1000)
        except sqlite3.Error as error:
            owner._record_sqlite_fault(error);owner.check_integrity()
            raise ContractError('DATABASE_ADMISSION_UNAVAILABLE','无法检查旧库身份，拒绝登记') from None
        if existing is not None:
            assert_business(existing,path);cache(owner,existing);return existing
        owner.check_integrity()
        if digest_separate_process(path)!=review['expected_sha256']:
            raise ContractError('DATABASE_LEGACY_REVIEW_CHANGED','旧库SHA与离线审核材料不符，拒绝登记')
        record={'version':VERSION,'role':'Active','database_uuid':uuid.uuid4().hex,'path_sha256':binding(path),
            'origin':'reviewed_legacy_business','registered_ns':time.time_ns(),'legacy_review':dict(review)}
        register(owner,record);return record


def mark_archive(path,operation):
    """Private capability after copy closes, before validation/hash/publication."""
    with closing(sqlite3.connect(path,timeout=0)) as c:
        previous=None
        if c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(TABLE,)).fetchone():
            previous=json.loads(c.execute('SELECT record_json FROM '+TABLE).fetchone()[0])
        record={'version':VERSION,'role':'Backup','database_uuid':uuid.uuid4().hex,
            'source_uuid':previous.get('database_uuid') if previous else None,'operation_id':operation}
        write_identity(c,record);c.commit()
    return record


def request_recovery(path,reviewer,reference):
    if not reviewer or not reference:raise ValueError('Named offline reviewer and reference required')
    identity=read_identity(path)
    if identity is None or identity.get('role')!='Backup':raise ValueError('An identified archive is required')
    record={'state':'RecoveryPending','reviewer':reviewer,'reference':reference,
        'archive_uuid':identity['database_uuid'],'requested_ns':time.time_ns(),'writable_approved':False}
    target=Path(str(path)+'.recovery-request-'+uuid.uuid4().hex+'.json');atomic_record(target,record)
    return record
