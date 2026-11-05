"""Real SQLite, commit-gate contention, diagnostic exit and durable stop admission."""
import json,multiprocessing,os,shutil,sqlite3
from pathlib import Path
from contextlib import contextmanager
import pytest
from infra.database import Database
from packages.config.errors import ContractError
from packages.contracts.database import SQLiteSettings
from scripts.reproduce_p037_a4_baseline import holder
from tests.sqlite_corruption_fixture import generated_corrupt_index
from tests.test_p037_a3_integrity import inject,count


def policy():return SQLiteSettings(busy_timeout_ms=100)


def diagnose_and_exit(path,result,crash=False):
    db=Database('sqlite:///'+str(path),policy())
    if crash:
        from infra import sqlite_isolation
        original=sqlite_isolation.sync_directory
        def stop_after_claim(parent):
            original(parent)
            if parent==db.fault_intent_path.parent:os._exit(19)
        sqlite_isolation.sync_directory=stop_after_claim
    try:db.check_integrity();code='unexpected_healthy'
    except ContractError as e:code=e.code
    result.put({'code':code,'fault':db.fault,'structural':db.last_integrity_check['category']=='structural_corruption'})
    db.close()


def new_process_write(path,route,result):
    db=Database('sqlite:///'+str(path),policy())
    try:
        if route=='session':
            with db.session() as s:s.execute(__import__('sqlalchemy').text("INSERT INTO probe(value) VALUES('generated-session')"))
        else:
            raw=db.engine.raw_connection();c=raw.driver_connection
            try:
                if route=='release':c.execute('SAVEPOINT[s]')
                c.execute("INSERT INTO probe(value) VALUES('generated-new-process')")
                if route=='commit':c.commit()
                elif route=='release':c.execute('RELEASE[s]')
                else:c.execute('COMMIT')
            finally:c.rollback();raw.close()
        result.put('committed')
    except ContractError as e:result.put(e.code)
    finally:db.close()


def preexisting_transaction(path,route,entered,finish,result):
    db=Database('sqlite:///'+str(path),policy());raw=db.engine.raw_connection();c=raw.driver_connection
    try:
        c.execute('SAVEPOINT[s]');c.execute("INSERT INTO probe(value) VALUES('generated-inflight')");entered.set();assert finish.wait(10)
        try:
            if route=='commit':c.commit()
            elif route=='release':c.execute('RELEASE[s]')
            else:c.isolation_level=None
            result.put('committed')
        except ContractError as e:result.put(e.code)
    finally:c.rollback();raw.close();db.close()


def damaged(tmp_path):
    fixture=generated_corrupt_index(tmp_path/'raw');path=tmp_path/'working.sqlite'
    shutil.copyfile(fixture['damaged'],path);return path


@pytest.mark.parametrize('route',['commit','sql_commit','release','session'])
def test_gate_timeout_diagnostic_exit_and_two_spawn_restarts_never_reopen(tmp_path,route):
    path=damaged(tmp_path);ctx=multiprocessing.get_context('spawn');entered,release=ctx.Event(),ctx.Event();q=ctx.Queue()
    locked=ctx.Process(target=holder,args=(path,entered,release));locked.start();assert entered.wait(4)
    diagnostic=ctx.Process(target=diagnose_and_exit,args=(path,q));diagnostic.start();diagnostic.join(4)
    try:
        assert diagnostic.exitcode==0;result=q.get(timeout=2)
        assert result['code']=='DATABASE_ISOLATION_PENDING' and result['structural']
        db=Database('sqlite:///'+str(path));assert db.fault_intent_path.is_dir() and not db.quarantine_path.exists()
        assert db.fault is not None;db.close()
        for _ in range(2):
            child=ctx.Process(target=new_process_write,args=(path,route,q));child.start();child.join(4)
            assert child.exitcode==0 and q.get(timeout=2)=='DATABASE_UNAVAILABLE'
        refused=Database('sqlite:///'+str(path),policy())
        with pytest.raises(ContractError):refused.backup(tmp_path/'refused.sqlite')
        assert not (tmp_path/'refused.sqlite').exists();refused.close()
    finally:release.set();locked.join(4)
    assert locked.exitcode==0 and count(path)==0


@pytest.mark.parametrize('route',['commit','release','implicit'])
def test_pending_durable_intent_rolls_back_preexisting_other_process_transaction(tmp_path,route):
    fixture=generated_corrupt_index(tmp_path/'raw');path=tmp_path/'working.sqlite';shutil.copyfile(fixture['clean'],path)
    ctx=multiprocessing.get_context('spawn');written,finish,locked,release=[ctx.Event() for _ in range(4)];q=ctx.Queue()
    writer=ctx.Process(target=preexisting_transaction,args=(path,route,written,finish,q));writer.start();assert written.wait(4)
    gate=ctx.Process(target=holder,args=(path,locked,release));gate.start();assert locked.wait(4)
    try:
        inject(path);diagnostic=ctx.Process(target=diagnose_and_exit,args=(path,q));diagnostic.start();diagnostic.join(4)
        assert diagnostic.exitcode==0 and q.get(timeout=2)['code']=='DATABASE_ISOLATION_PENDING'
        finish.set();writer.join(4);assert writer.exitcode==0 and q.get(timeout=2)=='DATABASE_UNAVAILABLE'
    finally:
        finish.set();release.set();writer.join(4);gate.join(4)
    assert count(path)==0


def test_exit_after_atomic_claim_before_metadata_still_blocks_spawn(tmp_path):
    path=damaged(tmp_path);ctx=multiprocessing.get_context('spawn');q=ctx.Queue()
    child=ctx.Process(target=diagnose_and_exit,args=(path,q,True));child.start();child.join(4);assert child.exitcode==19
    db=Database('sqlite:///'+str(path));assert db.fault['reason']=='incomplete_fault_intent';db.close()
    writer=ctx.Process(target=new_process_write,args=(path,'session',q));writer.start();writer.join(4)
    assert writer.exitcode==0 and q.get(timeout=2)=='DATABASE_UNAVAILABLE' and count(path)==0


def test_plain_busy_gate_without_structural_confirmation_does_not_poison(tmp_path):
    path=tmp_path/'healthy.sqlite';db=Database('sqlite:///'+str(path),policy());db.migrate()
    with db.engine.begin() as c:c.exec_driver_sql('CREATE TABLE probe(id INTEGER PRIMARY KEY,value TEXT)')
    ctx=multiprocessing.get_context('spawn');entered,release=ctx.Event(),ctx.Event()
    locked=ctx.Process(target=holder,args=(path,entered,release));locked.start();assert entered.wait(4)
    try:
        with pytest.raises(ContractError) as e:
            with db.session():pytest.fail('busy gate admitted')
        assert e.value.code=='DATABASE_COORDINATION_UNAVAILABLE' and db.fault is None
        assert not db.fault_intent_path.exists() and not db.quarantine_path.exists()
    finally:release.set();locked.join(4)
    with db.session() as s:s.execute(__import__('sqlalchemy').text("INSERT INTO probe(value) VALUES('healthy')"))
    assert count(path)==1;db.close()


def test_interruption_during_gate_wait_keeps_independent_intent(tmp_path,monkeypatch):
    path=damaged(tmp_path);db=Database('sqlite:///'+str(path),policy())
    @contextmanager
    def interrupted(*_,**__):raise KeyboardInterrupt('generated cancel');yield
    monkeypatch.setattr(db.commit_gate,'hold',interrupted)
    with pytest.raises(KeyboardInterrupt):db.check_integrity()
    assert db.fault_intent_path.exists();again=Database('sqlite:///'+str(path));assert again.fault is not None;again.close();db.close()


def test_failed_independent_claim_with_busy_gate_persists_emergency_stop(tmp_path,monkeypatch):
    from infra import sqlite_isolation
    path=damaged(tmp_path);db=Database('sqlite:///'+str(path),policy())
    ctx=multiprocessing.get_context('spawn');entered,release=ctx.Event(),ctx.Event();q=ctx.Queue()
    locked=ctx.Process(target=holder,args=(path,entered,release));locked.start();assert entered.wait(4)
    monkeypatch.setattr(sqlite_isolation,'publish_intent',lambda *_:(_ for _ in ()).throw(OSError('generated no directory capacity')))
    try:
        with pytest.raises(ContractError) as e:db.check_integrity()
        assert e.value.code=='DATABASE_ISOLATION_PERSISTENCE_FAILED' and db.quarantine_path.exists()
        child=ctx.Process(target=new_process_write,args=(path,'session',q));child.start();child.join(4)
        assert child.exitcode==0 and q.get(timeout=2)=='DATABASE_UNAVAILABLE'
    finally:release.set();locked.join(4);db.close()
    assert count(path)==0
