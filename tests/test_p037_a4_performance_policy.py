"""Detection is explicit; every strategy still enforces durable stop admission."""
from contextlib import closing
import sqlite3,time
from threading import Lock
import pytest
from infra.database import Database
from infra.sqlite_connection import _backup_native_source
from packages.contracts.database import SQLiteSettings
from packages.config.errors import ContractError
from packages.temporal_jobs.service import TemporalService
from tests.test_p037_a3_integrity import inject


def setup(tmp_path,mode='fault_state',interval=0):
    path=tmp_path/'generated.sqlite';db=Database('sqlite:///'+str(path),SQLiteSettings(commit_integrity_check=mode,
        integrity_check_interval_seconds=interval,busy_timeout_ms=200))
    db.migrate();raw=db.engine.raw_connection();c=raw.driver_connection
    c.execute('CREATE TABLE probe(id INTEGER PRIMARY KEY)');c.commit();return db,path,raw,c


@pytest.mark.parametrize('strategy',['full','fault_state'])
@pytest.mark.parametrize('route',['commit','sql_commit','release','implicit'])
def test_both_strategies_block_inflight_commit_after_persistent_intent(tmp_path,strategy,route):
    db,path,raw,c=setup(tmp_path,strategy);c.execute('SAVEPOINT[s]');c.execute('INSERT INTO probe VALUES(1)')
    other=Database('sqlite:///'+str(path));other.fault={'reason':'generated-known-fault'}
    with pytest.raises(ContractError) as e:
        if route=='commit':c.commit()
        elif route=='sql_commit':c.execute('COMMIT')
        elif route=='release':c.execute('RELEASE[s]')
        else:c.isolation_level=None
    assert e.value.code=='DATABASE_UNAVAILABLE';c.rollback();raw.close();db.close();other.close()
    with closing(sqlite3.connect(path)) as native:assert native.execute('SELECT COUNT(*) FROM probe').fetchone()[0]==0


@pytest.mark.parametrize('strategy,expected',[('full',1),('fault_state',0)])
def test_full_scans_removed_only_from_explicit_lightweight_commit(tmp_path,strategy,expected,monkeypatch):
    db,path,raw,c=setup(tmp_path,strategy);calls=[];validate=db._validate_native
    def tracked(*args,**kwargs):calls.append(True);return validate(*args,**kwargs)
    monkeypatch.setattr(db,'_validate_native',tracked)
    c.execute('INSERT INTO probe VALUES(1)');c.commit();assert len(calls)==expected
    assert db.check_integrity()['check']=='integrity_check_and_foreign_keys'
    raw.close();db.close()


def test_lightweight_mode_discloses_undetected_damage_until_full_boundary(tmp_path):
    db,path,raw,c=setup(tmp_path);inject(path)
    assert db.fault is None and db.integrity_policy()['unobserved_corruption_risk']
    assert db.integrity_policy()['detection_delay_bound_seconds'] is None
    # Do not claim a clean cached write proves physical health. An explicit
    # full check is the required detection boundary and must quarantine it.
    with pytest.raises(ContractError):db.full_check_boundary('generated-critical-boundary')
    assert db.fault is not None
    with pytest.raises(ContractError):c.execute('INSERT INTO probe VALUES(1)')
    c.rollback();raw.close();db.close()


def test_real_sqlite_corrupt_error_triggers_immediate_lightweight_isolation(tmp_path):
    db,path,raw,c=setup(tmp_path)
    # Get a genuine SQLite NOTADB exception from an unrelated generated file.
    bad=tmp_path/'generated-not-sqlite';bad.write_bytes(b'generated NOTADB fixture')
    with closing(sqlite3.connect(bad)) as native:
        try:native.execute('SELECT name FROM sqlite_master').fetchall()
        except sqlite3.DatabaseError as error:db._record_sqlite_fault(error)
    assert db.fault['sqlite_errorname']=='SQLITE_NOTADB' and db.fault_intent_path.exists()
    with pytest.raises(ContractError):c.execute('INSERT INTO probe VALUES(1)')
    raw.close();db.close()


def test_configured_background_full_check_detects_real_damage_and_stops(tmp_path):
    db,path,raw,c=setup(tmp_path,interval=.02);db.start_integrity_monitor();inject(path)
    deadline=time.monotonic()+3
    while db.fault is None and time.monotonic()<deadline:time.sleep(.01)
    assert db.fault is not None and db.fault_intent_path.exists()
    with pytest.raises(ContractError):c.execute('INSERT INTO probe VALUES(1)')
    raw.close();db.close();assert not db._integrity_thread.is_alive()


def test_busy_background_check_is_not_permanent_corruption_and_closes(tmp_path):
    db,path,raw,c=setup(tmp_path,interval=.02);c.execute('BEGIN EXCLUSIVE');c.execute('INSERT INTO probe VALUES(1)')
    db.start_integrity_monitor();deadline=time.monotonic()+2
    while db.last_monitor_error is None and time.monotonic()<deadline:time.sleep(.01)
    assert db.last_monitor_error['code']=='DATABASE_INTEGRITY_BUSY' and db.fault is None
    c.rollback();raw.close();db.close();assert not db._integrity_thread.is_alive()


def test_formal_consumption_forces_full_check_before_markers_or_feature_read(tmp_path):
    db,path,raw,c=setup(tmp_path);inject(path)
    service=TemporalService.__new__(TemporalService);service.database=db;service.lock=Lock()
    with pytest.raises(ContractError) as e:service._consume_test('generated-unused-study',{'mechanism_fixture_only':True})
    assert e.value.code=='DATABASE_INTEGRITY_FAILED' and db.fault is not None
    with closing(sqlite3.connect(path)) as native:
        assert native.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='temporal.test_consumed'").fetchone()[0]==0
    raw.close();db.close()


def test_default_keeps_full_check_and_monitor_requires_explicit_start(tmp_path):
    assert SQLiteSettings().commit_integrity_check=='full' and SQLiteSettings().integrity_check_interval_seconds==0
    db=Database('sqlite:///'+str(tmp_path/'fresh.sqlite'));db.migrate();db.start_integrity_monitor()
    assert db._integrity_thread is None and not db.integrity_policy()['unobserved_corruption_risk'];db.close()
