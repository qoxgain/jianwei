"""Source integrity and quarantine order at every online-backup boundary."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
import hashlib,json,shutil,sqlite3
import pytest
from infra.database import Database
from packages.config.errors import ContractError
from tests.sqlite_corruption_fixture import generated_corrupt_index
from tests.test_p037_a3_integrity import inject,count


def setup(tmp_path):
    fixture=generated_corrupt_index(tmp_path/'raw');path=tmp_path/'working.sqlite';shutil.copyfile(fixture['clean'],path)
    return Database('sqlite:///'+str(path)),path,fixture


def receipt(target):return json.loads(next(target.parent.glob(target.name+'.backup-*.json')).read_text())


def test_corrupt_source_not_previously_checked_cannot_publish_new_backup(tmp_path):
    fixture=generated_corrupt_index(tmp_path/'raw');path=tmp_path/'working.sqlite';shutil.copyfile(fixture['damaged'],path)
    db=Database('sqlite:///'+str(path));target=tmp_path/'invalid.sqlite';original=path.read_bytes()
    with pytest.raises(ContractError):db.backup(target)
    assert db.fault['reason']=='structural_corruption' and db.quarantine_path.exists() and not target.exists()
    assert receipt(target)['state']=='invalidated' and path.read_bytes()==original;db.close()


@pytest.mark.parametrize('phase',['before_copy','after_copy','after_validation','before_publish'])
def test_structural_confirmation_between_backup_stages_blocks_publication(tmp_path,phase):
    db,path,_=setup(tmp_path);target=tmp_path/'invalid.sqlite';entered,finish=Event(),Event()
    def observe(current,_state):
        if current==phase:entered.set();assert finish.wait(4)
    with ThreadPoolExecutor(max_workers=1) as pool:
        copy=pool.submit(db.backup,target,phase_observer=observe)
        try:
            assert entered.wait(2);inject(path);detector=Database('sqlite:///'+str(path))
            with pytest.raises(ContractError):detector.check_integrity()
            assert detector.quarantine_path.exists();detector.close()
        finally:finish.set()
        with pytest.raises(ContractError):copy.result(3)
    assert not target.exists() and receipt(target)['state']=='invalidated' and count(path)==0;db.close()


def test_source_damage_after_copy_is_detected_without_external_confirmation(tmp_path):
    db,path,_=setup(tmp_path);target=tmp_path/'copy.sqlite'
    def observe(phase,_state):
        if phase=='after_validation':inject(path)
    with pytest.raises(ContractError):db.backup(target,phase_observer=observe)
    assert db.fault is not None and db.quarantine_path.exists() and not target.exists()
    assert receipt(target)['state']=='invalidated';db.close()


def test_bad_destination_does_not_falsely_quarantine_healthy_source(tmp_path):
    db,path,_=setup(tmp_path);target=tmp_path/'copy.sqlite'
    def observe(phase,state):
        if phase=='after_copy':(target.parent/state['staging']).write_bytes(b'generated invalid destination')
    with pytest.raises(ContractError) as e:db.backup(target,phase_observer=observe)
    assert e.value.code=='DATABASE_BACKUP_INTEGRITY_FAILED' and db.fault is None and not target.exists()
    assert db.check_integrity()['status']=='ok' and receipt(target)['state']=='failed';db.close()


def test_completed_archive_and_consumption_protection_survive_later_corruption(tmp_path):
    from infra.models import AuditEvent
    db,path,fixture=setup(tmp_path);db.migrate();target=tmp_path/'older.sqlite';result=db.backup(target)
    archive=target.read_bytes();state=receipt(target);assert result['status']=='ok' and state['state']=='published'
    with db.session() as s:s.add(AuditEvent(tenant_id='generated',scene_id='generated',event_type='temporal.test_consumed',entity_id='generated-study',payload_json='{}'))
    inject(path)
    with pytest.raises(ContractError):db.check_integrity()
    assert target.read_bytes()==archive and receipt(target)==state
    # Creating an ordinary writable Database from a recognized archive is not an approved restore.
    restored=Database('sqlite:///'+str(target))
    with pytest.raises(ContractError) as e:
        with restored.session():pytest.fail('archive restored consumed test state without review')
    assert e.value.code=='DATABASE_RESTORE_REVIEW_REQUIRED'
    # Same-identity external replacement cannot clear durable isolation either.
    marker=db.quarantine_path.read_bytes();db.close();shutil.copyfile(target,path);again=Database('sqlite:///'+str(path))
    with pytest.raises(ContractError):again.migrate()
    assert again.quarantine_path.read_bytes()==marker;again.close();restored.close()
