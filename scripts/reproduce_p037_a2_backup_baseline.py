"""Run in an isolated 5f150e7 worktree: real EXCLUSIVE/backup commit-order counterexample."""
import argparse,json,os,sqlite3,subprocess,sys,threading,time
from pathlib import Path

BASE='5f150e7daf982c3cb0c9066691f25e7b34961bc8'


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--mode',choices=['DELETE','WAL']);a=p.parse_args()
 assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==BASE
 a.output.mkdir(parents=True,exist_ok=False)
 if a.mode is None:
  for mode in ['DELETE','WAL']:
   subprocess.run([sys.executable,__file__,'--output',str(a.output/mode),'--mode',mode],check=True,timeout=8)
  print(json.dumps({mode:json.loads((a.output/mode/'result.json').read_text()) for mode in ['DELETE','WAL']}));return
 from infra.database import Database
 from infra.sqlite_guard import GuardedConnection
 from packages.contracts.database import SQLiteSettings
 path=(a.output/'generated.sqlite').resolve();db=Database('sqlite:///'+str(path),SQLiteSettings(journal_mode=a.mode,busy_timeout_ms=200));db.migrate()
 raw=db.engine.raw_connection();writer=raw.driver_connection
 writer.execute('CREATE TABLE probe(id)');writer.commit()
 spare=db.engine.connect();spare.close() # Ensure source checkout does not hide native backup behind connection setup.
 writer.execute('BEGIN EXCLUSIVE');writer.execute('INSERT INTO probe VALUES(1)')
 started,backup_done,commit_done=threading.Event(),threading.Event(),threading.Event();errors=[]
 original=GuardedConnection.backup
 def observed(native,*args,**kwargs):started.set();return original(native,*args,**kwargs)
 GuardedConnection.backup=observed
 def backup():
  try:db.backup(a.output/'copy.sqlite')
  except BaseException as e:errors.append(type(e).__name__)
  finally:backup_done.set()
 def commit():
  try:writer.commit()
  except BaseException as e:errors.append(type(e).__name__)
  finally:commit_done.set()
 threading.Thread(target=backup,daemon=True).start();assert started.wait(2)
 threading.Thread(target=commit,daemon=True).start()
 completed=commit_done.wait(1) and backup_done.wait(1)
 result={'mode':a.mode,'busy_timeout_ms':200,'native_backup_started':True,'commit_completed':commit_done.is_set(),
  'backup_completed':backup_done.is_set(),'resource_wait_beyond_busy_timeout':not completed,'errors':errors}
 if completed:
  with sqlite3.connect(path) as c:result['independent_source_rows']=c.execute('SELECT COUNT(*) FROM probe').fetchone()[0]
 (a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
 # A failed baseline can leave daemon threads holding SQLite; only this disposable process exits.
 os._exit(0)


if __name__=='__main__':main()
