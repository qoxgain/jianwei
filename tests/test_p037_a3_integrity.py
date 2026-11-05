"""Real structural result rows, in-flight transactions, durable identity and process restart."""
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,multiprocessing,os,shutil,sqlite3,time
from pathlib import Path
from threading import Event
import pytest
from infra.database import Database
from infra.file_gate import FileGate
from packages.config.errors import ContractError
from packages.contracts.database import SQLiteSettings
from tests.sqlite_corruption_fixture import generated_corrupt_index
from tests.sqlite_admission_fixtures import approve_generated_legacy


def damaged(tmp_path):
    fixture=generated_corrupt_index(tmp_path/'raw');path=tmp_path/'working.sqlite'
    shutil.copyfile(fixture['damaged'],path);return path,fixture


def count(path):
    with sqlite3.connect(path) as c:return c.execute('SELECT COUNT(*) FROM probe').fetchone()[0]


def child_attempt(path,result):
    db=Database('sqlite:///'+str(path),SQLiteSettings(busy_timeout_ms=200))
    try:
        with db.session() as s:s.execute(__import__('sqlalchemy').text("INSERT INTO probe(value) VALUES('generated-child')"))
        result.put('committed')
    except ContractError as error:result.put(error.code)
    finally:db.close()


def inject_header_from_other_process(path):
    # Fault injection only: separate PID avoids closing a non-SQLite FD for a
    # DB with live SQLite POSIX locks in this process.
    with Path(path).open('r+b') as stream:stream.seek(36);stream.write((1).to_bytes(4,'big'));stream.flush();os.fsync(stream.fileno())


def inject(path):
    ctx=multiprocessing.get_context('spawn');p=ctx.Process(target=inject_header_from_other_process,args=(path,));p.start();p.join(10)
    assert p.exitcode==0 and not p.is_alive()


def test_confirmed_structural_rows_persist_isolation_without_repair(tmp_path):
    path,fixture=damaged(tmp_path);original=path.read_bytes();db=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError) as e:db.check_integrity()
    assert e.value.code=='DATABASE_INTEGRITY_FAILED'
    assert db.last_integrity_check['category']=='structural_corruption'
    marker=json.loads(db.quarantine_path.read_text());assert marker['reason']=='structural_corruption'
    assert marker['database_identity']['path_sha256']==hashlib.sha256(str(path.resolve()).encode()).hexdigest()
    assert db.commit_gate.path.stat().st_size>0 and path.read_bytes()==original
    assert hashlib.sha256(fixture['damaged'].read_bytes()).hexdigest()==fixture['sha256']
    with pytest.raises(ContractError):db.fault=None
    db.close()


@pytest.mark.parametrize('route',['connection','sql_release','isolation_level','session'])
def test_existing_inflight_write_cannot_commit_after_integrity_isolation(tmp_path,route):
    fixture=generated_corrupt_index(tmp_path/'raw');path=tmp_path/'working.sqlite';shutil.copyfile(fixture['clean'],path)
    db=Database('sqlite:///'+str(path));approve_generated_legacy(db);raw=db.engine.raw_connection();c=raw.driver_connection
    c.execute('SAVEPOINT[s]');c.execute("INSERT INTO probe(value) VALUES('generated-inflight')");inject(path)
    detector=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError):detector.check_integrity()
    assert detector.quarantine_path.exists()
    with pytest.raises(ContractError):
        if route=='connection':c.commit()
        elif route=='sql_release':c.execute('RELEASE[s]')
        elif route=='isolation_level':c.isolation_level=None
        else:
            with db.session():pytest.fail('new managed session after isolation')
    c.rollback();assert not c.in_transaction and count(path)==0
    raw.close();detector.close();db.close()


def test_new_native_connection_refuses_corruption_even_without_explicit_check(tmp_path):
    path,_=damaged(tmp_path);db=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError) as e:db.engine.raw_connection()
    assert e.value.code=='DATABASE_INTEGRITY_FAILED' and db.quarantine_path.exists() and count(path)==0
    db.close()


def test_spawn_restart_and_old_backup_restore_do_not_reopen_identity(tmp_path):
    path,fixture=damaged(tmp_path);db=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError):db.check_integrity()
    marker=db.quarantine_path.read_bytes();db.close();ctx=multiprocessing.get_context('spawn')
    for restore in [False,True]:
        if restore:shutil.copyfile(fixture['clean'],path)
        q=ctx.Queue();p=ctx.Process(target=child_attempt,args=(path,q));p.start();p.join(10)
        assert p.exitcode==0 and not p.is_alive() and q.get(timeout=2)=='DATABASE_UNAVAILABLE'
        again=Database('sqlite:///'+str(path))
        with pytest.raises(ContractError):again.migrate()
        assert again.quarantine_path.read_bytes()==marker and count(path)==0;again.close()


def test_primary_marker_failure_retains_durable_intent_and_stops_all_instances(tmp_path,monkeypatch):
    path,_=damaged(tmp_path);db=Database('sqlite:///'+str(path));original=Path.open
    def fail_primary(self,*args,**kwargs):
        if self==db.quarantine_path and args and args[0]=='x':raise PermissionError('generated marker failure')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'open',fail_primary)
    with pytest.raises(ContractError) as e:db.check_integrity()
    assert e.value.code=='DATABASE_ISOLATION_PERSISTENCE_FAILED' and not db.quarantine_path.exists()
    assert db.commit_gate.path.stat().st_size>0
    other=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError):
        with other.session():pytest.fail('persistence failure ignored')
    ctx=multiprocessing.get_context('spawn');q=ctx.Queue();p=ctx.Process(target=child_attempt,args=(path,q));p.start();p.join(10)
    assert p.exitcode==0 and q.get(timeout=2)=='DATABASE_UNAVAILABLE' and count(path)==0
    other.close();db.close()


def test_all_marker_writes_fail_process_stops_and_keeps_coordination_lock(tmp_path,monkeypatch):
    path,fixture=damaged(tmp_path);db=Database('sqlite:///'+str(path),SQLiteSettings(busy_timeout_ms=100));original=Path.open
    def fail_primary(self,*args,**kwargs):
        if self==db.quarantine_path and args and args[0]=='x':raise OSError('generated storage failure')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'open',fail_primary)
    monkeypatch.setattr(FileGate,'_write_fault_intent',lambda *_:(_ for _ in ()).throw(OSError('generated latch failure')))
    with pytest.raises(ContractError) as e:db.check_integrity()
    assert e.value.code=='DATABASE_ISOLATION_PERSISTENCE_FAILED' and (db.commit_gate.path.stat().st_mode&0o600)==0
    with pytest.raises(ContractError):db.commit_gate.try_acquire_raw()
    with pytest.raises(ContractError):
        with db.session():pytest.fail('storage failure resumed writes')
    ctx=multiprocessing.get_context('spawn');q=ctx.Queue();p=ctx.Process(target=child_attempt,args=(path,q));p.start();p.join(10)
    assert p.exitcode==0 and q.get(timeout=2)=='DATABASE_UNAVAILABLE' and count(path)==0
    db.close() # A normal close cannot release the retained safety latch.
    # Even an old healthy copy cannot reopen the same logical identity after restart.
    shutil.copyfile(fixture['clean'],path)
    q=ctx.Queue();p=ctx.Process(target=child_attempt,args=(path,q));p.start();p.join(10)
    assert p.exitcode==0 and q.get(timeout=2)=='DATABASE_UNAVAILABLE'


def test_busy_and_timeout_checks_are_not_permanent_physical_faults(tmp_path):
    fixture=generated_corrupt_index(tmp_path/'raw');path=tmp_path/'working.sqlite';shutil.copyfile(fixture['clean'],path)
    db=Database('sqlite:///'+str(path),SQLiteSettings(busy_timeout_ms=100));approve_generated_legacy(db);raw=db.engine.raw_connection();c=raw.driver_connection
    c.execute('BEGIN EXCLUSIVE');other=Database('sqlite:///'+str(path))
    try:
        with pytest.raises(ContractError) as e:other.check_integrity(timeout_seconds=.03,busy_timeout_ms=10)
        assert e.value.code=='DATABASE_INTEGRITY_BUSY' and other.fault is None
    finally:c.rollback();raw.close()
    with pytest.raises(ContractError) as e:other.check_integrity(timeout_seconds=1e-9)
    assert e.value.code=='DATABASE_INTEGRITY_TIMEOUT' and other.fault is None
    assert other.check_integrity()['status']=='ok';other.close();db.close()


def test_foreign_key_problem_is_reported_without_physical_corruption_label(tmp_path):
    path=tmp_path/'fk.sqlite'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE parent(id PRIMARY KEY)');c.execute('CREATE TABLE child(id REFERENCES parent(id))');c.execute('INSERT INTO child VALUES(2)')
    db=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError) as e:db.check_integrity()
    assert e.value.code=='DATABASE_FOREIGN_KEY_FAILED' and db.last_integrity_check['category']=='foreign_keys' and db.fault is None
    db.close()


def test_database_hardlink_alias_cannot_escape_isolation_identity(tmp_path):
    path,_=damaged(tmp_path);db=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError):db.check_integrity()
    alias=tmp_path/'alias.sqlite';os.link(path,alias);other=Database('sqlite:///'+str(alias))
    with pytest.raises(ContractError) as e:
        with other.session():pytest.fail('hardlink alias bypassed identity')
    assert e.value.code=='DATABASE_IDENTITY_UNSAFE';other.close();db.close()


def shared_cache_locked(path,result):
    from infra.sqlite_health import inspect_native
    writer=sqlite3.connect(Path(path).as_uri()+'?cache=shared',uri=True)
    reader=sqlite3.connect(Path(path).as_uri()+'?mode=ro&cache=shared',uri=True)
    writer.execute('BEGIN IMMEDIATE');writer.execute('CREATE TABLE generated_pending(id)')
    db=Database('sqlite:///'+str(path),SQLiteSettings(busy_timeout_ms=100))
    try:
        try:
            db._apply_integrity(inspect_native(reader,timeout_seconds=.1,busy_timeout_ms=100))
            result.put({'status':'unexpected_ok'})
        except ContractError as e:result.put({'status':e.code,'category':db.last_integrity_check['category'],'fault':db.fault})
    finally:reader.close();writer.rollback();writer.close();db.close()


def test_real_shared_cache_locked_is_not_permanent_corruption(tmp_path):
    fixture=generated_corrupt_index(tmp_path/'raw');path=tmp_path/'working.sqlite';shutil.copyfile(fixture['clean'],path)
    ctx=multiprocessing.get_context('spawn');q=ctx.Queue();p=ctx.Process(target=shared_cache_locked,args=(path,q));p.start();p.join(10)
    assert p.exitcode==0;result=q.get(timeout=2)
    assert result=={'status':'DATABASE_INTEGRITY_LOCKED','category':'locked','fault':None}


def test_logical_null_constraint_failure_is_not_physical_corruption(tmp_path):
    path=tmp_path/'logical.sqlite'
    c=sqlite3.connect(path)
    try:
        c.execute('CREATE TABLE probe(id)');c.execute('INSERT INTO probe VALUES(NULL)');c.commit()
        c.execute('PRAGMA writable_schema=ON')
        c.execute("UPDATE sqlite_master SET sql='CREATE TABLE probe(id NOT NULL)' WHERE name='probe'");c.commit()
    finally:c.close()
    db=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError) as e:db.check_integrity()
    assert e.value.code=='DATABASE_LOGICAL_INTEGRITY_FAILED' and db.fault is None;db.close()


def test_healthy_migration_commit_backup_close_and_restart(tmp_path):
    path=tmp_path/'healthy.sqlite';db=Database('sqlite:///'+str(path));db.migrate()
    with db.session() as s:s.execute(__import__('sqlalchemy').text('CREATE TABLE probe(id)'))
    with db.session() as s:s.execute(__import__('sqlalchemy').text('INSERT INTO probe VALUES(1)'))
    assert db.check_integrity()['status']=='ok';assert db.backup(tmp_path/'copy.sqlite')['status']=='ok'
    db.close();again=Database('sqlite:///'+str(path));again.migrate()
    assert again.fault is None and again.check_integrity()['status']=='ok' and count(path)==1;again.close()


def test_commit_already_in_gate_finishes_before_later_fault_persistence(tmp_path):
    fixture=generated_corrupt_index(tmp_path/'raw');path=tmp_path/'working.sqlite';shutil.copyfile(fixture['clean'],path)
    db=Database('sqlite:///'+str(path));approve_generated_legacy(db);raw=db.engine.raw_connection();c=raw.driver_connection
    c.execute("INSERT INTO probe(value) VALUES('generated-commit-first')");inject(path)
    detector=Database('sqlite:///'+str(path));checked,committing,finish=Event(),Event(),Event();original=detector._apply_integrity
    def apply(result):
        assert result['category']=='structural_corruption';checked.set();assert committing.wait(3);return original(result)
    detector._apply_integrity=apply
    def trace(sql):
        if sql=='COMMIT':committing.set();assert finish.wait(3)
    c.set_trace_callback(trace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        health=pool.submit(detector.check_integrity);assert checked.wait(2);write=pool.submit(c.commit)
        try:
            assert committing.wait(2);assert not detector.quarantine_path.exists()
        finally:finish.set()
        write.result(3)
        with pytest.raises(ContractError):health.result(3)
    assert count(path)==1 and detector.quarantine_path.exists()
    c.set_trace_callback(None);raw.close();db.close();detector.close()


@pytest.mark.parametrize('factory',['row_factory','text_factory'])
def test_app_result_factory_cannot_hide_real_structural_check_rows(tmp_path,factory):
    from infra.sqlite_connection import _backup_native_source
    fixture=generated_corrupt_index(tmp_path/'raw');path=tmp_path/'working.sqlite';shutil.copyfile(fixture['clean'],path)
    db=Database('sqlite:///'+str(path));approve_generated_legacy(db);raw=db.engine.raw_connection();c=raw.driver_connection
    # Ordinary application factories remain supported, but must not alter the
    # private native check used by connection validation and commit protection.
    setattr(c,factory,(lambda *_:('ok',)) if factory=='row_factory' else (lambda *_:'ok'))
    inject(path)
    with pytest.raises(ContractError) as e:db._validate_native(_backup_native_source(c))
    assert e.value.code=='DATABASE_INTEGRITY_FAILED' and db.quarantine_path.exists()
    assert getattr(c,factory) is not None;raw.close();db.close()


def test_healthy_private_check_does_not_call_application_factories(tmp_path):
    from infra.sqlite_connection import _backup_native_source
    db=Database('sqlite:///'+str(tmp_path/'healthy.sqlite'));db.migrate();raw=db.engine.raw_connection();c=raw.driver_connection
    def application_factory(*_):raise AssertionError('application callback entered private health check')
    c.row_factory=c.text_factory=application_factory
    assert db._validate_native(_backup_native_source(c))['status']=='ok'
    assert c.row_factory is application_factory and c.text_factory is application_factory
    c.row_factory=None;c.text_factory=str;raw.close();db.close()
