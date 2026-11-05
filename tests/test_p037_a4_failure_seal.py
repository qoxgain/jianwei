"""A caught last-page failure cannot return a clean Active snapshot as an artifact."""
from contextlib import closing
from pathlib import Path
from threading import Event
import hashlib,json,shutil,sqlite3,zipfile
import pytest
from infra.database import Database
from infra.sqlite_admission import read_identity
from packages.config.errors import ContractError
from tests.test_p037_a4_admission import business,refuse


@pytest.mark.parametrize('action',['cancel','exception','interrupt'])
def test_last_page_failure_seals_active_identity_before_return_and_inplace_copy(tmp_path,action):
    db,path=business(tmp_path);target=tmp_path/'archive.sqlite';cancel=Event()
    def progress(_status,remaining,_total):
        if remaining:return
        if action=='cancel':cancel.set()
        elif action=='interrupt':raise KeyboardInterrupt('generated')
        else:raise RuntimeError('generated')
    with pytest.raises(KeyboardInterrupt if action=='interrupt' else ContractError):db.backup(target,cancel_event=cancel,progress=progress)
    partial=next(tmp_path.glob('archive.sqlite.partial-*'));assert read_identity(partial)['role']=='Backup'
    receipt=json.loads(next(tmp_path.glob('archive.sqlite.backup-*.json')).read_text());sealed=receipt['failed_artifact']
    assert hashlib.sha256(partial.read_bytes()).hexdigest()==sealed['sealed_sha256']
    evidence=partial.parent/sealed['raw_evidence']
    with zipfile.ZipFile(evidence) as z:
        raw=z.read('database.sqlite');assert hashlib.sha256(raw).hexdigest()==sealed['raw_files']['database.sqlite']['sha256']
        raw_path=tmp_path/'diagnostic-copy.sqlite';raw_path.write_bytes(raw)
        assert read_identity(raw_path)['role']=='Active' # Original diagnosis bytes are unchanged inside the envelope.
    with db.engine.begin() as c:c.exec_driver_sql('INSERT INTO probe VALUES(7)')
    db.close();shutil.copyfile(partial,path) # Same inode/path/xattr of the former business; exactly the discovered boundary.
    assert refuse(path)=='DATABASE_RESTORE_REVIEW_REQUIRED'
    with closing(sqlite3.connect(path)) as c:assert c.execute('SELECT COUNT(*) FROM probe WHERE id=99').fetchone()[0]==0


def test_malformed_failed_staging_becomes_non_database_envelope_without_losing_bytes(tmp_path):
    db,path=business(tmp_path);target=tmp_path/'archive.sqlite';body=b'generated deliberately invalid staging image'
    def phase(name,state):
        if name=='after_copy':(target.parent/state['staging']).write_bytes(body)
    with pytest.raises(ContractError):db.backup(target,phase_observer=phase)
    partial=next(tmp_path.glob('archive.sqlite.partial-*'));assert zipfile.is_zipfile(partial)
    with zipfile.ZipFile(partial) as z:assert z.read('database.sqlite')==body
    assert refuse(partial)=='DATABASE_RESTORE_REVIEW_REQUIRED' and db.fault is None;db.close()


def test_cleanup_storage_failure_stops_source_and_never_exposes_readable_active_staging(tmp_path,monkeypatch):
    from infra import sqlite_backup_artifacts
    db,path=business(tmp_path);target=tmp_path/'archive.sqlite';original=sqlite_backup_artifacts.zipfile.ZipFile
    def fail(*args,**kwargs):raise OSError('generated evidence storage failure')
    monkeypatch.setattr(sqlite_backup_artifacts.zipfile,'ZipFile',fail)
    with pytest.raises(ContractError) as e:db.backup(target,progress=lambda *_:(_ for _ in ()).throw(RuntimeError('generated callback failure')))
    assert e.value.code=='DATABASE_BACKUP_SEAL_FAILED' and db.fault['classification']=='admission_safety_stop'
    partial=next(tmp_path.glob('archive.sqlite.partial-*'));assert partial.stat().st_mode&0o600==0
    with pytest.raises(ContractError):
        with db.session():pytest.fail('unsealed artifact storage failure resumed writes')
    db.close()
