"""Generated lock-ownership experiment; another PID must not enter an EXCLUSIVE writer."""
import argparse,hashlib,json,multiprocessing,os,sqlite3,sys,time
from pathlib import Path


def contender(path,advance,results):
    def attempt():
        c=sqlite3.connect(path,timeout=.05)
        try:
            try:c.execute('BEGIN IMMEDIATE');return 'acquired'
            except sqlite3.DatabaseError as e:return getattr(e,'sqlite_errorname',type(e).__name__)
        finally:c.rollback();c.close()
    results.put({'stage':'before_forensics','result':attempt(),'pid':os.getpid()})
    assert advance.wait(10)
    results.put({'stage':'after_forensics','result':attempt(),'pid':os.getpid()})


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--expect',choices=['released','retained'],default='retained');a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);path=out/'generated.sqlite'
    from infra.database import Database
    from scripts.inspect_sqlite_copy import inspect
    db=Database('sqlite:///'+str(path));db.migrate();db.close()
    c=sqlite3.connect(path);c.execute('CREATE TABLE probe(id INTEGER)');c.commit();c.close()
    initial=hashlib.sha256(path.read_bytes()).hexdigest() # No live SQLite handles yet.
    db=Database('sqlite:///'+str(path));raw=db.engine.raw_connection();writer=raw.driver_connection
    writer.execute('BEGIN EXCLUSIVE');writer.execute('INSERT INTO probe VALUES(1)')
    ctx=multiprocessing.get_context('spawn');advance=ctx.Event();q=ctx.Queue();child=ctx.Process(target=contender,args=(path,advance,q));child.start()
    before=q.get(timeout=5);assert before['result'].startswith('SQLITE_BUSY'),before
    acquisition=inspect(path,out/'evidence');advance.set();after=q.get(timeout=5);child.join(5)
    rollback_error=None
    try:writer.rollback()
    except sqlite3.Error as e:rollback_error=getattr(e,'sqlite_errorname',type(e).__name__)
    raw.close();db.close()
    # Cold-copy inspection occurs only after all participant SQLite handles close.
    verified=inspect(path,out/'post_closed_evidence')
    result={'generated_only':True,'sqlite_version':sqlite3.sqlite_version,'python_version':sys.version.split()[0],
        'caller_pid':os.getpid(),'before':before,'after':after,'source_integrity_after_close':verified['integrity'],
        'source_bytes_unchanged':initial==hashlib.sha256(path.read_bytes()).hexdigest(),
        'forensic_worker_pid':acquisition.get('worker_pid',os.getpid()),'rollback_error':rollback_error,
        'original_incident_root_cause':'root_cause_not_established','expected_lock_state':a.expect}
    (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    assert child.exitcode==0
    assert (after['result']=='acquired')==(a.expect=='released'),result
    assert verified['integrity']=='ok' and rollback_error is None


if __name__=='__main__':main()
