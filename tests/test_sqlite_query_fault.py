"""Real SQLite query failures, durable isolation and callback compatibility.

Only generated files are damaged. Independent originals remain unavailable.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Event
from types import SimpleNamespace
import json,multiprocessing,sqlite3,time
import pytest
from sqlalchemy import text
from infra.database import Database
from packages.config.errors import ContractError
from packages.contracts.database import SQLiteSettings
from tests.sqlite_query_fixture import generated_query_case,consume,sqlalchemy_consume,independent_rows,child_write


ROUTES=['execute','executemany','fetchone','fetchmany','fetchall','next','result','orm']


@pytest.mark.parametrize('strategy',['full','fault_state'])
@pytest.mark.parametrize('journal',['DELETE','WAL'])
@pytest.mark.parametrize('route',ROUTES)
def test_native_query_error_isolates_once_and_blocks_inflight_session_and_backup(tmp_path,monkeypatch,strategy,journal,route):
    from infra import sqlite_isolation
    db,path,reader,writer=generated_query_case(tmp_path/'case',strategy,journal);stage=[];published=[]
    publish=sqlite_isolation.publish_intent
    def tracked(*args):published.append(args[0]);return publish(*args)
    monkeypatch.setattr(sqlite_isolation,'publish_intent',tracked)
    try:
        # executemany itself needs the single SQLite writer; all read routes
        # instead exercise an existing uncommitted writer on another connection.
        if route!='executemany':writer.driver_connection.execute("INSERT INTO query_writes VALUES(99,'inflight')")
        with pytest.raises(Exception) as caught:
            if route in {'result','orm'}:
                reader.close();reader=None;sqlalchemy_consume(db,route,stage)
            else:consume(reader.driver_connection,route,stage)
        error=getattr(caught.value,'orig',caught.value)
        assert isinstance(error,sqlite3.DatabaseError) and error.sqlite_errorcode&255==sqlite3.SQLITE_CORRUPT
        assert stage[-1]==route and ('executed' in stage)==(route not in {'execute','executemany'})
        assert db.fault['sqlite_errorname']=='SQLITE_CORRUPT' and db.fault['classification']=='structural_corruption'
        assert db.fault_intent_path.is_dir() and db.quarantine_path.is_file() and len(published)==1
        marker=db.quarantine_path.read_bytes()
        db._record_sqlite_fault(error);db._record_fault(SimpleNamespace(original_exception=error))
        assert len(published)==1 and db.quarantine_path.read_bytes()==marker
        if reader is not None:reader.driver_connection.rollback()
        with pytest.raises(ContractError) as e:writer.driver_connection.commit()
        assert e.value.code=='DATABASE_UNAVAILABLE';writer.driver_connection.rollback()
        with pytest.raises(ContractError):
            with db.session() as session:session.execute(text("INSERT INTO query_writes VALUES(100,'denied')"))
        again=Database('sqlite:///'+str(path),SQLiteSettings(commit_integrity_check=strategy))
        try:
            assert again.fault is not None
            with pytest.raises(ContractError):again.engine.raw_connection()
            target=tmp_path/'no-publish.sqlite'
            with pytest.raises(ContractError):again.backup(target)
            assert not target.exists()
            receipt=json.loads(next(tmp_path.glob('no-publish.sqlite.backup-*.json')).read_text());assert receipt['state']=='invalidated'
        finally:again.close()
        assert independent_rows(path)==0 and db.quarantine_path.read_bytes()==marker
    finally:
        if reader is not None:reader.close()
        writer.close();db.close()


@pytest.mark.parametrize('strategy',['full','fault_state'])
@pytest.mark.parametrize('journal',['DELETE','WAL'])
def test_query_fault_survives_spawn_and_two_restarts(tmp_path,strategy,journal):
    db,path,reader,writer=generated_query_case(tmp_path/'case',strategy,journal)
    try:
        with pytest.raises(sqlite3.DatabaseError):consume(reader.driver_connection,'fetchall',[])
        marker=db.quarantine_path.read_bytes()
    finally:reader.close();writer.close();db.close()
    ctx=multiprocessing.get_context('spawn')
    for _ in range(2):
        result=ctx.Queue();child=ctx.Process(target=child_write,args=(path,result,strategy));child.start();child.join(10)
        if child.is_alive():child.terminate();child.join(2);pytest.fail('spawn writer timed out')
        assert child.exitcode==0 and result.get(timeout=2)=='DATABASE_UNAVAILABLE'
        assert path.with_name(path.name+'.fault.json').read_bytes()==marker and independent_rows(path)==0


def healthy(tmp_path,strategy):
    db=Database('sqlite:///'+str(tmp_path/'healthy.sqlite'),SQLiteSettings(commit_integrity_check=strategy,busy_timeout_ms=100,min_free_bytes=0))
    db.migrate()
    with db.engine.begin() as c:
        c.exec_driver_sql('CREATE TABLE probe(id INTEGER PRIMARY KEY,value TEXT)')
        c.exec_driver_sql('INSERT INTO probe VALUES(?,?)',[(i,'generated-'+str(i)) for i in range(3)])
    return db


def application_error(root,kind):
    if kind=='ordinary':return ValueError('generated application callback')
    if kind=='timeout':return TimeoutError('generated application timeout')
    if kind=='cancel':return KeyboardInterrupt('generated application cancellation')
    if kind=='sqlite_without_code':return sqlite3.DatabaseError('generated callback error, no native code')
    if kind=='sqlite_shaped':
        error=sqlite3.DatabaseError('generated application code metadata');error.sqlite_errorcode=sqlite3.SQLITE_CORRUPT;error.sqlite_errorname='SQLITE_CORRUPT';return error
    path=root/'other-generated-notadb';path.write_bytes(b'generated non-database')
    with closing(sqlite3.connect(path)) as native:
        try:native.execute('SELECT name FROM sqlite_master').fetchall()
        except sqlite3.DatabaseError as error:return error
    raise AssertionError('NOTADB fixture did not fail')


@pytest.mark.parametrize('strategy',['full','fault_state'])
@pytest.mark.parametrize('callback',['row','text','parameters'])
@pytest.mark.parametrize('kind',['ordinary','timeout','cancel','sqlite_without_code','sqlite_shaped','sqlite_from_other_database'])
def test_callback_exceptions_keep_identity_and_do_not_quarantine_this_database(tmp_path,strategy,callback,kind):
    db=healthy(tmp_path,strategy);raw=db.engine.raw_connection();c=raw.driver_connection;error=application_error(tmp_path,kind)
    def fail(*_):raise error
    try:
        with pytest.raises(type(error)) as caught:
            if callback=='row':c.row_factory=fail;c.execute('SELECT * FROM probe').fetchone()
            elif callback=='text':c.text_factory=fail;c.execute('SELECT value FROM probe').fetchall()
            else:
                def parameters():
                    yield (7,'rolled-back')
                    raise error
                c.executemany('INSERT INTO probe VALUES(?,?)',parameters())
        assert caught.value is error
        db._record_fault(SimpleNamespace(original_exception=error)) # SQLAlchemy's second wrapper must agree.
        assert db.fault is None and not db.fault_intent_path.exists()
        c.row_factory=None;c.text_factory=str;c.rollback()
        c.execute("INSERT INTO probe VALUES(8,'healthy-after-callback')");c.commit()
        assert c.execute('SELECT COUNT(*) FROM probe').fetchone()==(4,)
    finally:c.row_factory=None;c.text_factory=str;c.rollback();raw.close();db.close()


@pytest.mark.parametrize('strategy',['full','fault_state'])
def test_sqlalchemy_callback_error_from_other_database_is_not_current_source_corruption(tmp_path,strategy):
    db=healthy(tmp_path,strategy);raw=db.engine.raw_connection();error=application_error(tmp_path,'sqlite_from_other_database')
    def factory(_cursor,_row):raise error
    raw.driver_connection.row_factory=factory;raw.close()
    try:
        with pytest.raises(Exception) as caught:
            with db.engine.connect() as c:c.exec_driver_sql('SELECT value FROM probe').fetchall()
        assert getattr(caught.value,'orig',caught.value) is error and db.fault is None
        raw=db.engine.raw_connection();raw.driver_connection.row_factory=None;raw.close()
        with db.session() as session:session.execute(text("INSERT INTO probe VALUES(9,'healthy')"))
        assert not db.fault_intent_path.exists()
    finally:db.close()


@pytest.mark.parametrize('strategy',['full','fault_state'])
def test_normal_row_factories_arraysize_and_result_iterators_are_compatible(tmp_path,strategy):
    db=healthy(tmp_path,strategy);raw=db.engine.raw_connection();c=raw.driver_connection
    try:
        c.row_factory=sqlite3.Row;cursor=c.execute('SELECT * FROM probe ORDER BY id');cursor.arraysize=2
        assert [r['id'] for r in cursor.fetchmany()]==[0,1] and next(cursor)['id']==2
        with pytest.raises(StopIteration):next(cursor)
        cursor.close();c.row_factory=lambda cursor,row:{cursor.description[i][0]:v for i,v in enumerate(row)}
        assert list(c.execute('SELECT * FROM probe ORDER BY id'))[0]=={'id':0,'value':'generated-0'}
        c.row_factory=None;assert c.execute('SELECT id FROM probe ORDER BY id').fetchall()==[(0,),(1,),(2,)]
        db._record_sqlite_fault(SimpleNamespace(sqlite_errorname='SQLITE_CORRUPT',sqlite_errorcode=11))
        assert db.fault is None
    finally:raw.close();db.close()


@pytest.mark.parametrize('strategy',['full','fault_state'])
def test_real_busy_and_locked_do_not_set_permanent_fault(tmp_path,strategy):
    db=healthy(tmp_path,strategy);a=db.engine.raw_connection();b=db.engine.raw_connection();c=a.driver_connection;other=b.driver_connection
    try:
        c.execute('BEGIN EXCLUSIVE');c.execute("INSERT INTO probe VALUES(10,'uncommitted')")
        with pytest.raises(sqlite3.OperationalError) as busy:other.execute("INSERT INTO probe VALUES(11,'blocked')")
        assert busy.value.sqlite_errorcode&255==sqlite3.SQLITE_BUSY and db.fault is None
        c.rollback();other.rollback()
        active=c.execute('SELECT * FROM probe')
        try:
            with pytest.raises(sqlite3.OperationalError) as locked:c.execute('DROP TABLE probe')
            assert locked.value.sqlite_errorcode&255==sqlite3.SQLITE_LOCKED and db.fault is None
        finally:active.close();c.rollback()
        c.execute("INSERT INTO probe VALUES(12,'healthy-after-contention')");c.commit()
        assert not db.fault_intent_path.exists()
    finally:c.rollback();other.rollback();a.close();b.close();db.close()


@pytest.mark.parametrize('strategy',['full','fault_state'])
def test_real_interrupt_is_not_permanent_corruption(tmp_path,strategy):
    db=healthy(tmp_path,strategy);raw=db.engine.raw_connection();c=raw.driver_connection;entered=Event()
    def slow(value):entered.set();time.sleep(.002);return value
    c.create_function('generated_slow',1,slow)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            query=pool.submit(c.execute,'WITH RECURSIVE ids(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM ids WHERE x<100000) SELECT sum(generated_slow(x)) FROM ids')
            assert entered.wait(2);c.interrupt()
            with pytest.raises(sqlite3.OperationalError) as caught:query.result(2)
        assert caught.value.sqlite_errorcode&255==sqlite3.SQLITE_INTERRUPT and db.fault is None
        c.rollback();assert c.execute('SELECT COUNT(*) FROM probe').fetchone()==(3,)
    finally:c.rollback();raw.close();db.close()


@pytest.mark.parametrize('strategy',['full','fault_state'])
def test_already_held_gate_is_not_reacquired_during_query_isolation(tmp_path,monkeypatch,strategy):
    db,path,reader,writer=generated_query_case(tmp_path/'case',strategy);acquire=db.commit_gate.acquire_raw
    def checked(*args,**kwargs):assert not db.commit_gate.held();return acquire(*args,**kwargs)
    monkeypatch.setattr(db.commit_gate,'acquire_raw',checked)
    def query():
        with db.commit_gate.hold():
            with pytest.raises(sqlite3.DatabaseError):consume(reader.driver_connection,'fetchall',[])
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:pool.submit(query).result(3)
        assert db.fault_intent_path.exists() and independent_rows(path)==0
    finally:reader.close();writer.close();db.close()


@pytest.mark.parametrize('strategy',['full','fault_state'])
def test_native_notadb_is_also_isolated_without_name_only_matching(tmp_path,strategy):
    db,path,reader,writer=generated_query_case(tmp_path/'case',strategy,corruption='header')
    try:
        with pytest.raises(sqlite3.DatabaseError) as caught:consume(reader.driver_connection,'execute',[])
        assert caught.value.sqlite_errorcode&255==sqlite3.SQLITE_NOTADB
        assert db.fault['sqlite_errorcode']==caught.value.sqlite_errorcode and db.fault_intent_path.exists()
        with pytest.raises(ContractError):writer.driver_connection.commit()
    finally:reader.close();writer.close();db.close()


@pytest.mark.parametrize('strategy',['full','fault_state'])
def test_busy_isolation_gate_keeps_original_corrupt_error_and_durable_pending_intent(tmp_path,strategy):
    db,path,reader,writer=generated_query_case(tmp_path/'case',strategy);held,release=Event(),Event()
    cursor=reader.driver_connection.execute('SELECT * FROM query_payload ORDER BY id')
    def holder():
        with db.commit_gate.hold():held.set();assert release.wait(3)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            holding=pool.submit(holder);assert held.wait(1)
            try:
                with pytest.raises(sqlite3.DatabaseError) as caught:cursor.fetchall()
                assert caught.value.sqlite_errorcode&255==sqlite3.SQLITE_CORRUPT
                assert caught.value.__cause__.code=='DATABASE_ISOLATION_PENDING'
                assert any('DATABASE_ISOLATION_PENDING' in note for note in caught.value.__notes__)
                assert db.fault_intent_path.exists() and db.fault['phase']=='intent_pending'
            finally:release.set();holding.result(2)
        with pytest.raises(ContractError):writer.driver_connection.commit()
        assert independent_rows(path)==0
    finally:cursor.close();reader.close();writer.close();db.close()


@pytest.mark.parametrize('strategy',['full','fault_state'])
def test_marker_failure_preserves_native_error_and_exposes_isolation_failure(tmp_path,monkeypatch,strategy):
    from pathlib import Path
    db,path,reader,writer=generated_query_case(tmp_path/'case',strategy);original=Path.open
    def fail_primary(self,*args,**kwargs):
        if self==db.quarantine_path and args and args[0]=='x':raise PermissionError('generated marker fault injection')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'open',fail_primary)
    try:
        with pytest.raises(sqlite3.DatabaseError) as caught:consume(reader.driver_connection,'fetchall',[])
        assert caught.value.sqlite_errorcode&255==sqlite3.SQLITE_CORRUPT
        assert caught.value.__cause__.code=='DATABASE_ISOLATION_PERSISTENCE_FAILED'
        assert db.fault_intent_path.exists() and db.fault is not None
        with pytest.raises(ContractError):writer.driver_connection.commit()
        assert independent_rows(path)==0
    finally:reader.close();writer.close();db.close()


@pytest.mark.parametrize('strategy',['full','fault_state'])
def test_guarded_native_cursor_for_iteration_uses_the_same_exception_entry(tmp_path,strategy):
    from infra.sqlite_connection import _backup_native_source
    db,path,reader,writer=generated_query_case(tmp_path/'case',strategy)
    cursor=_backup_native_source(reader.driver_connection).execute('SELECT * FROM query_payload ORDER BY id')
    try:
        with pytest.raises(sqlite3.DatabaseError):
            for row in cursor:assert row[1].startswith('generated-')
        assert db.fault_intent_path.exists()
    finally:cursor.close();reader.close();writer.close();db.close()


@pytest.mark.parametrize('code',[sqlite3.SQLITE_CORRUPT_INDEX,sqlite3.SQLITE_NOTADB,sqlite3.SQLITE_IOERR_READ,sqlite3.SQLITE_FULL])
@pytest.mark.parametrize('strategy',['full','fault_state'])
def test_severe_extended_code_classifier_unit_preserves_existing_severity_set(tmp_path,strategy,code):
    # Classifier unit only: not evidence of a physical disk I/O or FULL incident.
    db=healthy(tmp_path,strategy);error=sqlite3.DatabaseError('synthetic classifier input');error.sqlite_errorcode=code
    try:
        db._record_sqlite_fault(error)
        assert db.fault['sqlite_errorcode']==code and db.fault_intent_path.exists()
        assert db.fault['classification']==('structural_corruption' if code&255 in {11,26} else 'storage_failure')
    finally:db.close()


@pytest.mark.parametrize('strategy',['full','fault_state'])
def test_sqlalchemy_business_event_is_not_database_corruption(tmp_path,strategy):
    from sqlalchemy import event
    db=healthy(tmp_path,strategy);error=application_error(tmp_path,'sqlite_from_other_database')
    def callback(*_):raise error
    event.listen(db.engine,'before_cursor_execute',callback)
    try:
        with pytest.raises(sqlite3.DatabaseError) as caught:
            with db.engine.connect() as c:c.exec_driver_sql('SELECT * FROM probe')
        assert caught.value is error and db.fault is None
        event.remove(db.engine,'before_cursor_execute',callback)
        with db.session() as session:session.execute(text("INSERT INTO probe VALUES(8,'healthy-after-event')"))
        assert not db.fault_intent_path.exists()
    finally:db.close()
