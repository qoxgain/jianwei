"""Real transaction counterexamples against generated disposable databases only."""
import multiprocessing,sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as N
from threading import Event
import pytest
from sqlalchemy import text
from infra.database import Database
from infra.models import AuditEvent
from packages.config.errors import ContractError


def inject(db):db._record_fault(N(original_exception=N(sqlite_errorname='SQLITE_IOERR')))
def make(path):
    db=Database('sqlite:///'+str(path));db.migrate()
    with db.session() as s:s.execute(text('CREATE TABLE probe(id INTEGER)'))
    return db

def rows(path):
    with sqlite3.connect(path) as c:return c.execute('SELECT COUNT(*) FROM probe').fetchone()[0]


@pytest.mark.parametrize('route',['core','orm','engine','raw','raw_context','sql_commit'])
def test_recorded_fault_precedes_commit_so_every_managed_route_rolls_back(tmp_path,route):
    path=tmp_path/'fault.sqlite';db=make(path)
    with pytest.raises(ContractError) as e:
        if route in ('core','orm'):
            with db.session() as s:
                if route=='core':s.execute(text('INSERT INTO probe VALUES(1)'))
                else:
                    s.add(AuditEvent(tenant_id='fixture',scene_id='fixture',event_type='fixture',entity_id='fixture',payload_json='{}'));s.flush()
                inject(db)
        elif route=='engine':
            with db.engine.begin() as c:c.exec_driver_sql('INSERT INTO probe VALUES(1)');inject(db)
        else:
            raw=db.engine.raw_connection()
            try:
                if route=='raw_context':
                    with raw.driver_connection as c:c.execute('INSERT INTO probe VALUES(1)');inject(db)
                else:
                    raw.cursor().execute('INSERT INTO probe VALUES(1)');inject(db)
                    if route=='raw':raw.commit()
                    else:raw.cursor().execute('/* prefix */ COMMIT')
            finally:raw.rollback();raw.close()
    assert e.value.code=='DATABASE_UNAVAILABLE' and rows(path)==0
    with sqlite3.connect(path) as c:assert c.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]==0
    with pytest.raises(ContractError):
        with db.session():pytest.fail('new session accepted fault')
    db.close()


def test_core_cte_and_comment_writes_check_disk_capacity(tmp_path,monkeypatch):
    from infra import database
    db=make(tmp_path/'low.sqlite')
    monkeypatch.setattr(database.shutil,'disk_usage',lambda _:N(free=0))
    for statement in ['/* comment */ INSERT INTO probe VALUES(1)',
        'WITH ids(x) AS (VALUES(2)) INSERT INTO probe SELECT x FROM ids']:
        with pytest.raises(ContractError) as e:
            with db.session() as s:s.execute(text(statement))
        assert e.value.code=='DATABASE_SPACE_LOW'
    assert rows(tmp_path/'low.sqlite')==0;db.close()


def child_transaction(path,started,finish,result):
    db=Database('sqlite:///'+str(path))
    try:
        with db.session() as s:
            s.execute(text('INSERT INTO probe VALUES(1)'));started.set();assert finish.wait(15)
        result.put('committed')
    except ContractError as e:result.put(e.code)
    finally:db.close()


def test_cross_process_fault_quarantines_inflight_transaction_and_survives_restart_restore(tmp_path):
    path=tmp_path/'process.sqlite';db=make(path);backup=tmp_path/'older-backup.sqlite';db.backup(backup)
    with db.session() as s:s.add(AuditEvent(tenant_id='fixture',scene_id='fixture',event_type='temporal.test_consumed',entity_id='fixture-study',payload_json='{}'))
    ctx=multiprocessing.get_context('spawn');started,finish=ctx.Event(),ctx.Event();result=ctx.Queue()
    p=ctx.Process(target=child_transaction,args=(path,started,finish,result));p.start();assert started.wait(15)
    try:inject(db)
    finally:finish.set()
    p.join(15);assert not p.is_alive() and p.exitcode==0
    assert result.get(timeout=2)=='DATABASE_UNAVAILABLE' and rows(path)==0
    db.close()
    # Replacing only the DB with an older copy cannot clear the independent marker.
    path.write_bytes(backup.read_bytes());reopened=Database('sqlite:///'+str(path))
    assert reopened.fault is not None
    with pytest.raises(ContractError):reopened.migrate()
    with pytest.raises(ContractError):reopened.fault=None
    reopened.close()


def test_commit_linearizes_before_fault_and_completed_rows_stay_committed(tmp_path):
    path=tmp_path/'ordering.sqlite';db=make(path);committing,release,recorded=Event(),Event(),Event()
    def write():
        with db.session() as s:
            s.execute(text('INSERT INTO probe VALUES(1)'))
            c=s.connection().connection.driver_connection
            def trace(statement):
                if statement=='COMMIT':committing.set();assert release.wait(10)
            c.set_trace_callback(trace)
        c.set_trace_callback(None)
    def fault():inject(db);recorded.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        w=pool.submit(write);assert committing.wait(5);f=pool.submit(fault)
        try:assert not recorded.wait(.1)
        finally:release.set()
        w.result(10);f.result(10)
    assert rows(path)==1 and db.fault is not None;db.close()


def test_second_instance_observes_marker_and_rollback_remains_available(tmp_path):
    path=tmp_path/'instances.sqlite';a=make(path);b=Database('sqlite:///'+str(path))
    c=b.engine.raw_connection();c.cursor().execute('INSERT INTO probe VALUES(1)');inject(a)
    with pytest.raises(ContractError):c.cursor().execute('SELECT 1')
    c.rollback();c.close();assert rows(path)==0 and b.fault is not None
    a.close();b.close()


from tests.test_ingestion_api import import_config


def test_async_worker_fault_stops_without_claiming_success_or_writing_terminal_state(import_config,monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from apps.api.main import create_app
    from packages import ingestion
    entered,release=Event(),Event();app=create_app(import_config)
    def fail(*a,**kw):
        entered.set();assert release.wait(5);inject(app.state.database)
        raise ContractError('DATABASE_UNAVAILABLE','generated fault injection')
    monkeypatch.setattr(ingestion,'process_import',fail)
    with TestClient(app) as client:
        try:
            r=client.post('/api/scenes/anonymous_observation/imports/example');assert r.status_code==202
            assert entered.wait(5)
        finally:release.set()
        deadline=time.monotonic()+5
        while app.state.imports.thread.is_alive() and time.monotonic()<deadline:app.state.imports.thread.join(.05)
        assert not app.state.imports.thread.is_alive()
        assert client.get('/api/health').status_code==503
        path=app.state.database.engine.url.database
        with sqlite3.connect(path) as c:
            assert c.execute('SELECT status FROM import_jobs WHERE id=?',(r.json()['id'],)).fetchone()[0]!='succeeded'
    reopened=Database('sqlite:///'+path)
    with pytest.raises(ContractError):reopened.migrate()
    reopened.close()
