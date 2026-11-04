"""Real SQLite locks/barriers, immutable originals and bounded backup publication."""
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,sqlite3,time
from threading import Event,Timer
import pytest
from infra.database import Database
from packages.contracts.database import SQLiteSettings,SQLiteBackupSettings
from packages.config.errors import ContractError


def setup(path,mode='DELETE',timeout=2):
    db=Database('sqlite:///'+str(path),SQLiteSettings(journal_mode=mode,busy_timeout_ms=200,
        backup=SQLiteBackupSettings(timeout_seconds=timeout,pages_per_step=1,retry_seconds=.002)))
    db.migrate();raw=db.engine.raw_connection();c=raw.driver_connection
    c.execute('CREATE TABLE probe(id INTEGER)');c.commit();raw.close();return db


def rows(path):
    with sqlite3.connect(path) as c:return c.execute('SELECT COUNT(*) FROM probe').fetchone()[0]


def receipts(path):return [json.loads(p.read_text()) for p in path.parent.glob(path.name+'.backup-*.json')]


@pytest.mark.parametrize('mode',['DELETE','WAL'])
def test_exclusive_writer_then_online_backup_does_not_hold_commit_gate(tmp_path,mode):
    path=tmp_path/'source.sqlite';db=setup(path,mode);raw=db.engine.raw_connection();c=raw.driver_connection
    # Also exercise an initially empty source pool: read-only backup must not run
    # managed connection PRAGMAs while the EXCLUSIVE writer is waiting to commit.
    c.execute('BEGIN EXCLUSIVE');c.execute('INSERT INTO probe VALUES(1)')
    entered=Event();cancel=Event();target=tmp_path/'copy.sqlite';start=time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        copying=pool.submit(db.backup,target,cancel_event=cancel,progress=lambda *_:entered.set())
        try:
            assert entered.wait(1)
            committed=pool.submit(c.commit);committed.result(1)
            result=copying.result(2);assert result['status']=='ok'
        finally:cancel.set();c.rollback()
    assert time.monotonic()-start<2 and rows(path)==1
    assert rows(target)==1 if mode=='DELETE' else rows(target) in (0,1)
    state=receipts(target)[0];assert state['state']=='published' and not state['source_state_modified']
    raw.close();db.close()


def test_fault_before_start_refuses_valid_output_and_preserves_marker(tmp_path):
    db=setup(tmp_path/'source.sqlite');db.fault={'sqlite_errorname':'SQLITE_IOERR','root_cause':'generated'}
    before=db.quarantine_path.read_bytes();target=tmp_path/'copy.sqlite'
    with pytest.raises(ContractError) as e:db.backup(target)
    assert e.value.code=='DATABASE_UNAVAILABLE' and not target.exists()
    assert receipts(target)[0]['state']=='invalidated' and db.quarantine_path.read_bytes()==before;db.close()


@pytest.mark.parametrize('mode',['DELETE','WAL'])
@pytest.mark.parametrize('phase',['first_page','last_page'])
def test_fault_persisted_during_real_backup_invalidates_partial_and_retains_evidence(tmp_path,mode,phase):
    source=tmp_path/'source.sqlite';db=setup(source,mode);entered,resume=Event(),Event();target=tmp_path/'copy.sqlite'
    def observe(status,remaining,total):
        if phase=='last_page' and remaining:return
        entered.set();assert resume.wait(2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        copying=pool.submit(db.backup,target,progress=observe)
        try:
            assert entered.wait(1)
            other=Database('sqlite:///'+str(source));other.fault={'sqlite_errorname':'SQLITE_IOERR','root_cause':'generated'}
            assert other.quarantine_path.exists();marker=other.quarantine_path.read_bytes();other.close()
        finally:resume.set()
        with pytest.raises(ContractError) as e:copying.result(2)
    assert e.value.code=='DATABASE_UNAVAILABLE' and not target.exists()
    assert receipts(target)[0]['state']=='invalidated' and list(tmp_path.glob('*.partial-*'))
    assert rows(source)==0 and db.quarantine_path.read_bytes()==marker;db.close()


def test_completed_backup_remains_historical_when_fault_follows(tmp_path):
    db=setup(tmp_path/'source.sqlite');target=tmp_path/'copy.sqlite';result=db.backup(target)
    receipt=next(tmp_path.glob('*.backup-*.json'));before=receipt.read_bytes();content=target.read_bytes()
    db.fault={'sqlite_errorname':'SQLITE_IOERR','root_cause':'generated'}
    assert result['status']=='ok' and json.loads(before)['state']=='published'
    assert receipt.read_bytes()==before and target.read_bytes()==content and rows(target)==0
    with pytest.raises(ContractError):db.backup(tmp_path/'later.sqlite')
    db.close()


def test_exclusive_writer_wait_exceeds_deadline_without_deadlock(tmp_path):
    db=setup(tmp_path/'source.sqlite',timeout=.06);raw=db.engine.raw_connection();c=raw.driver_connection
    c.execute('BEGIN EXCLUSIVE');c.execute('INSERT INTO probe VALUES(1)');target=tmp_path/'copy.sqlite';start=time.monotonic()
    try:
        with pytest.raises(ContractError) as e:db.backup(target)
        assert e.value.code=='DATABASE_BACKUP_TIMEOUT' and time.monotonic()-start<1
        assert receipts(target)[0]['state']=='timeout' and not target.exists()
    finally:c.rollback();raw.close();db.close()


@pytest.mark.parametrize('action',['cancel','close','exception','interrupt'])
def test_real_copy_cancellation_close_or_observer_error_never_publishes(tmp_path,action):
    db=setup(tmp_path/'source.sqlite');target=tmp_path/'copy.sqlite';cancel=Event()
    def observe(*_):
        if action=='cancel':cancel.set()
        elif action=='close':db.close()
        elif action=='interrupt':raise KeyboardInterrupt('generated')
        else:raise RuntimeError('generated observer error')
    with pytest.raises(KeyboardInterrupt if action=='interrupt' else ContractError) as e:
        db.backup(target,cancel_event=cancel,progress=observe)
    assert not target.exists() and list(tmp_path.glob('*.partial-*'))
    assert receipts(target)[0]['state']==('failed' if action=='exception' else 'cancelled')
    assert db.fault is None;db.close()


def test_cancel_waiting_for_commit_boundary_does_not_wait_for_entire_deadline(tmp_path):
    db=setup(tmp_path/'source.sqlite');fd=db.commit_gate.try_acquire_raw();cancel=Event();timer=Timer(.03,cancel.set);timer.start()
    start=time.monotonic()
    try:
        with pytest.raises(ContractError) as e:db.backup(tmp_path/'copy.sqlite',cancel_event=cancel)
        assert e.value.code=='DATABASE_BACKUP_CANCELLED' and time.monotonic()-start<.5
    finally:timer.join();db.commit_gate.release_raw(fd);db.close()


def test_multiple_managed_writers_and_backups_remain_consistent(tmp_path):
    path=tmp_path/'source.sqlite';db=setup(path,'WAL',timeout=4)
    def write(i):
        other=Database('sqlite:///'+str(path))
        with other.session() as s:s.execute(__import__('sqlalchemy').text('INSERT INTO probe VALUES(:n)'),{'n':i})
        other.close()
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks=[pool.submit(write,i) for i in range(6)]+[pool.submit(db.backup,tmp_path/f'copy-{i}.sqlite') for i in range(4)]
        for t in tasks:t.result(5)
    assert rows(path)==6
    for i in range(4):
        target=tmp_path/f'copy-{i}.sqlite';assert rows(target) in range(7) and receipts(target)[0]['state']=='published'
    db.close()


def test_only_new_target_and_failure_integrity_evidence(tmp_path):
    path=tmp_path/'source.sqlite';db=setup(path);target=tmp_path/'existing.sqlite';target.write_bytes(b'generated existing')
    with pytest.raises(FileExistsError):db.backup(target)
    assert target.read_bytes()==b'generated existing'
    alias=tmp_path/'dangling.sqlite';alias.symlink_to(tmp_path/'absent.sqlite')
    with pytest.raises(FileExistsError):db.backup(alias)
    assert alias.is_symlink() and not (tmp_path/'absent.sqlite').exists()
    # A real invalid foreign-key source is copied, then rejected by copy validation.
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE parent(id PRIMARY KEY)');c.execute('CREATE TABLE child(id REFERENCES parent(id))');c.execute('INSERT INTO child VALUES(99)')
    target=tmp_path/'invalid.sqlite'
    with pytest.raises(ContractError) as e:db.backup(target)
    assert e.value.code=='DATABASE_BACKUP_INTEGRITY_FAILED' and not target.exists()
    assert receipts(target)[0]['state']=='failed' and db.fault is None;db.close()


def test_in_memory_copy_remains_supported(tmp_path):
    db=Database('sqlite:///:memory:');db.migrate();target=tmp_path/'memory.sqlite'
    assert db.backup(target)['status']=='ok'
    with sqlite3.connect(target) as c:assert c.execute('PRAGMA integrity_check').fetchone()==('ok',)
    db.close()


def test_concurrent_publication_to_same_name_never_replaces_winner(tmp_path):
    from threading import Barrier
    db=setup(tmp_path/'source.sqlite');target=tmp_path/'copy.sqlite';barrier=Barrier(2)
    def observe(*_):
        if not getattr(__import__('threading').current_thread(),'seen_backup_page',False):
            __import__('threading').current_thread().seen_backup_page=True;barrier.wait(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks=[pool.submit(db.backup,target,progress=observe) for _ in range(2)];results=[]
        for task in tasks:
            try:results.append(task.result(3)['status'])
            except FileExistsError:results.append('already_exists')
    assert sorted(results)==['already_exists','ok'] and rows(target)==0
    assert sorted(r['state'] for r in receipts(target))==['failed','published']
    db.close()
