"""Disposable local databases; no historical failure or formal record is modified."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from infra.database import Database
from packages.contracts.database import SQLiteSettings
from packages.config.errors import ContractError
from scripts.inspect_sqlite_copy import inspect as inspect_copy


def policy(mode):return SQLiteSettings(journal_mode=mode)


def setup(path,mode):
    db=Database(f'sqlite:///{path}',sqlite_settings=policy(mode))
    db.migrate()
    with db.engine.begin() as con:con.exec_driver_sql('CREATE TABLE probe (id INTEGER PRIMARY KEY, value INTEGER)')
    return db


def writer(path,start,count,mode):
    db=Database(f'sqlite:///{path}',sqlite_settings=policy(mode))
    for i in range(count):
        with db.session() as session:
            session.execute(text('INSERT INTO probe VALUES (:i,:v)'),{'i':start+i,'v':i})
    db.close()


def interrupted(path,mode):
    db=Database(f'sqlite:///{path}',sqlite_settings=policy(mode))
    with db.session() as session:
        session.execute(text('INSERT INTO probe VALUES (9999,1)'))
        os._exit(9)


def inherited(db,queue):
    with db.engine.connect() as con:
        queue.put(con.connection._connection_record.info['owner_pid'])


@pytest.mark.parametrize('mode',['DELETE','WAL'])
def test_concurrent_threads_processes_cancelled_transactions_and_backup(tmp_path,mode):
    path=tmp_path/'concurrent.sqlite';db=setup(path,mode)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda n:writer(path,n*100,20,mode),range(6)))
    ctx=multiprocessing.get_context('spawn')
    processes=[ctx.Process(target=writer,args=(path,1000+n*100,15,mode)) for n in range(3)]
    for process in processes:process.start()
    for process in processes:
        process.join(25);assert not process.is_alive() and process.exitcode==0
    with pytest.raises(RuntimeError):
        with db.session() as session:
            session.execute(text('INSERT INTO probe VALUES (8888,1)'))
            raise RuntimeError('Injected cancellation before commit')
    assert db.check_integrity()['status']=='ok'
    with db.engine.connect() as con:
        assert con.exec_driver_sql('SELECT COUNT(*) FROM probe').scalar_one()==165
        assert con.exec_driver_sql('PRAGMA synchronous').scalar_one()==2
        assert con.exec_driver_sql('PRAGMA journal_mode').scalar_one().upper()==mode
    result=db.backup(tmp_path/'verified-backup.sqlite')
    assert result['status']=='ok'
    with pytest.raises(FileExistsError):db.backup(tmp_path/'verified-backup.sqlite')
    with sqlite3.connect(tmp_path/'verified-backup.sqlite') as copy:
        assert copy.execute('SELECT COUNT(*) FROM probe').fetchone()==(165,)
    db.close()


@pytest.mark.parametrize('mode',['DELETE','WAL'])
def test_abrupt_process_exit_recovers_without_committing_unfinished_work(tmp_path,mode):
    path=tmp_path/'crash.sqlite';db=setup(path,mode)
    writer(path,1,1,mode)
    ctx=multiprocessing.get_context('spawn');process=ctx.Process(target=interrupted,args=(path,mode))
    process.start();process.join(25);assert not process.is_alive() and process.exitcode==9
    assert db.check_integrity()['status']=='ok'
    with db.engine.connect() as con:
        assert con.exec_driver_sql('SELECT id FROM probe').scalars().all()==[1]
    db.close()


def test_fork_rejects_parent_connection_and_parent_remains_usable(tmp_path):
    db=setup(tmp_path/'fork.sqlite','WAL')
    ctx=multiprocessing.get_context('fork');queue=ctx.Queue()
    process=ctx.Process(target=inherited,args=(db,queue));process.start();process.join(15)
    assert not process.is_alive() and process.exitcode==0
    assert queue.get(timeout=2)==process.pid
    with db.engine.connect() as con:assert con.exec_driver_sql('SELECT COUNT(*) FROM probe').scalar_one()==0
    db.close()


def test_injected_corruption_is_detected_without_repair_or_original_mutation(tmp_path):
    path=tmp_path/'damaged.sqlite';path.write_bytes(b'not a sqlite database'+b'\0'*10000)
    original=path.read_bytes();db=Database(f'sqlite:///{path}')
    with pytest.raises(ContractError) as error:db.migrate()
    assert error.value.code=='DATABASE_INTEGRITY_FAILED'
    assert path.read_bytes()==original
    report=inspect_copy(path,tmp_path/'evidence')
    assert report['integrity']=='failed' and not report['original_files_written']
    assert path.read_bytes()==original
    with pytest.raises(FileExistsError):inspect_copy(path,tmp_path/'evidence')
    db.close()


def test_low_space_guard_and_transaction_exception_preserve_existing_rows(tmp_path,monkeypatch):
    db=setup(tmp_path/'full.sqlite','DELETE')
    from infra import database
    monkeypatch.setattr(database.shutil,'disk_usage',lambda p:SimpleNamespace(free=0))
    with pytest.raises(ContractError) as error:
        with db.session() as session:
            # Explicit ORM mutations exercise the pre-commit guard.
            from infra.models import AuditEvent
            session.add(AuditEvent(tenant_id='fixture',scene_id='fixture',event_type='fixture',entity_id='fixture',payload_json='{}'))
    assert error.value.code=='DATABASE_SPACE_LOW'
    monkeypatch.undo()
    assert db.check_integrity()['status']=='ok'
    db.close()


def test_direct_sql_low_disk_guard_precedes_insert_and_read_still_works(tmp_path,monkeypatch):
    db=setup(tmp_path/'direct-full.sqlite','DELETE')
    from infra import database
    monkeypatch.setattr(database.shutil,'disk_usage',lambda p:SimpleNamespace(free=0))
    with pytest.raises(ContractError) as error:
        with db.session() as session:session.execute(text('INSERT INTO probe VALUES (1,1)'))
    assert error.value.code=='DATABASE_SPACE_LOW'
    with db.engine.connect() as con:assert con.exec_driver_sql('SELECT COUNT(*) FROM probe').scalar_one()==0
    db.close()


def test_recorded_fault_pauses_task_sessions_without_automatic_restore(tmp_path):
    db=setup(tmp_path/'fault.sqlite','DELETE')
    db.fault={'sqlite_errorname':'SQLITE_CORRUPT','root_cause':'not_established'}
    with pytest.raises(ContractError) as error:
        with db.session():pytest.fail('faulted database accepted task session')
    assert error.value.code=='DATABASE_UNAVAILABLE'
    assert (tmp_path/'fault.sqlite').is_file()
    db.close()
