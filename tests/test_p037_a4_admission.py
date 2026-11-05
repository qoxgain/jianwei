"""Lifecycle provenance, renamed/copied artifacts, approved legacy and fresh DBs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json,multiprocessing,os,shutil,sqlite3,time
from pathlib import Path
from threading import Event
import pytest
from infra.database import Database
from infra.sqlite_admission import TABLE,read_identity,lifecycle,request_recovery
from infra.sqlite_health import inspect_file
from packages.config.errors import ContractError
from tests.sqlite_admission_fixtures import approve_generated_legacy


def business(tmp_path):
    path=tmp_path/'business.sqlite';db=Database('sqlite:///'+str(path));db.migrate()
    with db.engine.begin() as c:c.exec_driver_sql('CREATE TABLE probe(id INTEGER PRIMARY KEY)')
    return db,path


def native_rows(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:return c.execute('SELECT COUNT(*) FROM probe').fetchone()[0]


def refuse(path):
    db=Database('sqlite:///'+str(path))
    try:
        with pytest.raises(ContractError) as e:
            with db.session() as s:s.execute(__import__('sqlalchemy').text('INSERT INTO probe VALUES(99)'))
        assert e.value.code in {'DATABASE_RESTORE_REVIEW_REQUIRED','DATABASE_IDENTITY_REBIND_REQUIRED','DATABASE_LEGACY_REVIEW_REQUIRED'}
        return e.value.code
    finally:db.close()


@pytest.mark.parametrize('outcome',['invalidated','cancelled','failed','published'])
@pytest.mark.parametrize('relocation',['direct','rename','copy_without_xattrs'])
def test_any_archive_lifecycle_cannot_become_writable_business_after_relocation(tmp_path,outcome,relocation):
    db,path=business(tmp_path);target=tmp_path/'archive.sqlite';cancel=Event()
    def progress(_status,remaining,_total):
        if remaining:return
        if outcome=='invalidated':db.fault={'reason':'generated_fault_at_last_page'}
        elif outcome=='cancelled':cancel.set()
        elif outcome=='failed':raise RuntimeError('generated callback interruption')
    if outcome=='published':assert db.backup(target)['status']=='ok';artifact=target
    else:
        with pytest.raises(ContractError):db.backup(target,progress=progress,cancel_event=cancel)
        artifact=next(tmp_path.glob('archive.sqlite.partial-*'));assert not target.exists()
    db.close();record=json.loads(next(tmp_path.glob('archive.sqlite.backup-*.json')).read_text());assert record['state']==outcome
    if relocation=='rename':artifact=artifact.rename(tmp_path/'ordinary-business-name.data')
    elif relocation=='copy_without_xattrs':
        copied=tmp_path/'ordinary-copy.data';shutil.copyfile(artifact,copied);artifact=copied
    assert native_rows(artifact)==0;refuse(artifact);assert native_rows(artifact)==0
    if outcome=='published':
        assert inspect_file(artifact,timeout_seconds=2,busy_timeout_ms=100,foreign_keys=True)['status']=='ok'
        assert read_identity(artifact)['role']=='Backup'


def test_copying_stage_rejected_while_backup_waits_without_pool_deadlock(tmp_path):
    db,path=business(tmp_path);target=tmp_path/'archive.sqlite';entered,finish=Event(),Event();partial=[]
    def phase(name,state):
        if name=='before_copy':partial.append(target.parent/state['staging']);entered.set();assert finish.wait(4)
    with ThreadPoolExecutor(max_workers=2) as pool:
        copying=pool.submit(db.backup,target,phase_observer=phase)
        try:assert entered.wait(2);refuse(partial[0])
        finally:finish.set()
        assert copying.result(3)['status']=='ok'
    db.close()


def backup_then_exit(source,target,notice,finish):
    db=Database('sqlite:///'+str(source))
    def phase(name,state):
        if name=='before_copy':notice.put(str(target.parent/state['staging']));assert finish.wait(10);os._exit(23)
    db.backup(target,phase_observer=phase)


def test_crashed_copy_restart_and_unknown_empty_file_require_offline_review(tmp_path):
    db,path=business(tmp_path);db.close();ctx=multiprocessing.get_context('spawn');q=ctx.Queue();finish=ctx.Event()
    child=ctx.Process(target=backup_then_exit,args=(path,tmp_path/'crashed.sqlite',q,finish));child.start()
    partial=Path(q.get(timeout=4));finish.set();child.join(4);assert child.exitcode==23
    refuse(partial)
    moved=tmp_path/'looks-new-but-existing.sqlite';shutil.copyfile(partial,moved);assert refuse(moved)=='DATABASE_LEGACY_REVIEW_REQUIRED'
    assert not Path(str(moved)+'.admission.json').exists()


def test_new_business_initialization_and_reopen_are_registered_and_preserve_rows(tmp_path):
    db,path=business(tmp_path);identity=read_identity(path)
    with db.session() as s:s.execute(__import__('sqlalchemy').text('INSERT INTO probe VALUES(1)'))
    db.close();again=Database('sqlite:///'+str(path));again.migrate()
    assert read_identity(path)==identity and identity['origin']=='new_business_database'
    with again.engine.connect() as c:assert c.exec_driver_sql('SELECT COUNT(*) FROM probe').scalar_one()==1
    again.close()


def test_healthy_legacy_requires_explicit_hash_bound_business_review(tmp_path):
    path=tmp_path/'legacy.sqlite'
    with closing(sqlite3.connect(path)) as c:c.execute('CREATE TABLE probe(id PRIMARY KEY)');c.execute('INSERT INTO probe VALUES(7)');c.commit()
    assert refuse(path)=='DATABASE_LEGACY_REVIEW_REQUIRED'
    db=Database('sqlite:///'+str(path));record=approve_generated_legacy(db);db.migrate()
    assert record['origin']=='reviewed_legacy_business' and native_rows(path)==1
    with db.session() as s:s.execute(__import__('sqlalchemy').text('INSERT INTO probe VALUES(8)'))
    assert native_rows(path)==2;db.close()


def test_legacy_hash_mismatch_and_archive_review_never_approve_restore(tmp_path):
    path=tmp_path/'legacy.sqlite'
    with closing(sqlite3.connect(path)) as c:c.execute('CREATE TABLE probe(id)');c.commit()
    db=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError) as e:db.register_legacy({'reviewer':'generated','reference':'generated','expected_sha256':'0'*64,
        'writers_stopped':True,'consumption_reviewed':True,'legacy_business':True})
    assert e.value.code=='DATABASE_LEGACY_REVIEW_CHANGED' and read_identity(path) is None;db.close()
    source,_=business(tmp_path/'new');target=tmp_path/'archive.sqlite';source.backup(target);source.close()
    archive=Database('sqlite:///'+str(target))
    with pytest.raises(ContractError):approve_generated_legacy(archive)
    pending=request_recovery(target,'generated-reviewer','generated-offline-ticket')
    assert pending['state']=='RecoveryPending' and not pending['writable_approved'];refuse(target);archive.close()


def test_embedded_business_identity_cannot_be_modified_through_managed_sql(tmp_path):
    db,path=business(tmp_path);before=read_identity(path)
    for sql in ['DELETE FROM '+TABLE,'DROP TABLE '+TABLE]:
        with pytest.raises(ContractError) as e:
            with db.engine.begin() as c:c.exec_driver_sql(sql)
        assert e.value.code=='DATABASE_ADMISSION_PROTECTED'
    assert read_identity(path)==before;db.close()


def test_missing_xattr_support_still_denies_empty_copy_and_renamed_archive(tmp_path,monkeypatch):
    from infra import sqlite_admission
    db,path=business(tmp_path);target=tmp_path/'archive.sqlite'
    monkeypatch.setattr(sqlite_admission,'tag',lambda *_:False)
    def phase(name,state):
        if name=='before_copy':assert refuse(target.parent/state['staging'])=='DATABASE_LEGACY_REVIEW_REQUIRED'
    assert db.backup(target,phase_observer=phase)['status']=='ok';db.close()
    moved=target.rename(tmp_path/'unrelated-name.sqlite');assert lifecycle(moved) is None
    assert refuse(moved)=='DATABASE_RESTORE_REVIEW_REQUIRED'


def test_quarantine_still_blocks_copy_over_original_logical_path(tmp_path):
    db,path=business(tmp_path);archive=tmp_path/'archive.sqlite';db.backup(archive)
    db.fault={'reason':'generated-known-fault'};db.close();path.unlink();shutil.copyfile(archive,path)
    again=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError) as e:again.migrate()
    assert e.value.code=='DATABASE_UNAVAILABLE' and again.fault is not None;again.close()


def test_missing_business_registration_is_not_silently_recreated(tmp_path):
    db,path=business(tmp_path);registration=db.admission_path;db.close();registration.unlink()
    again=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError) as e:again.migrate()
    assert e.value.code=='DATABASE_ADMISSION_INVALID' and not registration.exists();again.close()


def test_recovery_request_keeps_published_bytes_and_does_not_grant_business_access(tmp_path):
    from infra.sqlite_admission import digest_separate_process
    db,path=business(tmp_path);archive=tmp_path/'archive.sqlite';db.backup(archive);db.close()
    before=digest_separate_process(archive);record=read_identity(archive)
    assert request_recovery(archive,'generated-reviewer','offline-review')['state']=='RecoveryPending'
    assert digest_separate_process(archive)==before and read_identity(archive)==record;refuse(archive)
