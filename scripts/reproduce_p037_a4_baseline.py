"""Fixed A.3 counterexamples, ONLY new generated media-free SQLite fixtures.

The missing independent damaged originals are not replaced by this fixture. In
this freelist fixture a fresh full check redetects corruption; record that fact.
"""
import argparse,json,multiprocessing,shutil,sqlite3,subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from infra.database import Database
from packages.contracts.database import SQLiteSettings
from packages.config.errors import ContractError
from tests.sqlite_corruption_fixture import generated_corrupt_index


def holder(path,entered,release):
    db=Database('sqlite:///'+str(path))
    with db.commit_gate.hold():entered.set();assert release.wait(10)
    db.close()


def diagnose(path,result):
    db=Database('sqlite:///'+str(path),SQLiteSettings(busy_timeout_ms=100))
    try:db.check_integrity();code='unexpected_success'
    except ContractError as e:code=e.code
    result.put({'code':code,'confirmed_structure':db.last_integrity_check['category']=='structural_corruption',
        'primary_marker':db.quarantine_path.exists(),'process_fault':db.fault is not None})
    db.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    assert sha=='fb69646336eb6346ea3cec498a08856a80a34e60'
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    fixture=generated_corrupt_index(out/'raw');source=out/'working.sqlite';shutil.copyfile(fixture['damaged'],source)
    ctx=multiprocessing.get_context('spawn');entered,release=ctx.Event(),ctx.Event();q=ctx.Queue()
    lock=ctx.Process(target=holder,args=(source,entered,release));lock.start();assert entered.wait(5)
    child=ctx.Process(target=diagnose,args=(source,q));child.start();child.join(5)
    try:assert child.exitcode==0;diagnostic=q.get(timeout=2)
    finally:release.set();lock.join(5)
    fresh=Database('sqlite:///'+str(source));after_exit_fault=fresh.fault
    try:
        with fresh.session() as s:s.execute(__import__('sqlalchemy').text("INSERT INTO probe(value) VALUES('generated-after-exit')"))
        fresh_write='committed'
    except ContractError as e:fresh_write=e.code
    fresh.close()
    # This generated fixture is reliably redetected by RW full validation. It
    # demonstrates lost durable state, not the missing historical RW bypass.
    result={'baseline':sha,'generated_only':True,'independent_original_available':False,
        'gate_timeout':diagnostic,'fault_after_diagnostic_exit':after_exit_fault,
        'fresh_write_on_this_damaged_fixture':fresh_write,'historical_corrupt_write_reproduction':'blocked_missing_original',
        'original_corruption_root_cause':'root_cause_not_established'}
    healthy=out/'backup-source.sqlite';db=Database('sqlite:///'+str(healthy));db.migrate()
    with db.engine.begin() as c:c.exec_driver_sql('CREATE TABLE generated_probe(id INTEGER)')
    target=out/'unpublished.sqlite'
    def progress(_status,remaining,_total):
        if remaining==0:db.fault={'sqlite_errorname':'SQLITE_IOERR','reason':'generated_fault_during_backup'}
    try:db.backup(target,progress=progress)
    except ContractError:pass
    finally:db.close()
    receipt=json.loads(next(out.glob('unpublished.sqlite.backup-*.json')).read_text())
    partial=next(out.glob('unpublished.sqlite.partial-*'))
    saved=out/'raw-invalidated';saved.mkdir();shutil.copyfile(partial,saved/'generated-invalidated.sqlite')
    writes={}
    for label,path in [('partial',partial),('renamed',out/'ordinary.sqlite')]:
        if label=='renamed':partial.rename(path)
        owner=Database('sqlite:///'+str(path));owner.migrate()
        with owner.engine.begin() as c:c.exec_driver_sql('INSERT INTO generated_probe VALUES(1)')
        owner.close()
        with sqlite3.connect(path) as c:writes[label]=c.execute('SELECT COUNT(*) FROM generated_probe').fetchone()[0]
    result['backup_admission']={'receipt_state':receipt['state'],'published':target.exists(),
        'unapproved_managed_writes':writes,'invalidated_original_copy_preserved':True}
    (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    assert diagnostic['confirmed_structure'] and not diagnostic['primary_marker'] and after_exit_fault is None
    assert receipt['state']=='invalidated' and writes=={'partial':1,'renamed':2}


if __name__=='__main__':main()
