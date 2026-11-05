"""SQLite's own authorizer guards commits; identifiers need no whitespace."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import multiprocessing,sqlite3
import pytest
from sqlalchemy import text
from infra.database import Database
from packages.config.errors import ContractError

CLAUSES=['RELEASE[s]','RELEASE"s"','RELEASE`s`',"RELEASE's'",'release/*comment*/[s]',
 '\ufeff; -- comment\n ReLeAsE\tSAVEPOINT "s"', 'RELEASE\ns','RELEASE/**/SAVEPOINT[s]']

def setup(path):
 db=Database('sqlite:///'+str(path));db.migrate()
 with db.session() as s:s.execute(text('CREATE TABLE probe(id INTEGER)'))
 return db

def fault(db):db.fault={'sqlite_errorname':'SQLITE_IOERR','root_cause':'explicit_generated_fault_injection'}
def count(path):
 with sqlite3.connect(path) as c:return c.execute('SELECT COUNT(*) FROM probe').fetchone()[0]


@pytest.mark.parametrize('clause',CLAUSES)
def test_fault_persisted_before_release_blocks_every_quoted_form(tmp_path,clause):
 path=tmp_path/'release.sqlite';db=setup(path);raw=db.engine.raw_connection();c=raw.driver_connection
 c.execute('SAVEPOINT[s]');c.execute('INSERT INTO probe VALUES(1)');fault(db)
 with pytest.raises(ContractError) as e:c.execute(clause)
 assert e.value.code=='DATABASE_UNAVAILABLE' and count(path)==0
 raw.close();db.close()


def test_release_enters_gate_before_fault_so_completed_commit_stays(tmp_path):
 path=tmp_path/'ordered.sqlite';db=setup(path);raw=db.engine.raw_connection();c=raw.driver_connection
 c.execute('SAVEPOINT"s"');c.execute('INSERT INTO probe VALUES(1)');entered,release,recorded=Event(),Event(),Event()
 def trace(sql):
  if sql.startswith('RELEASE'):entered.set();assert release.wait(5)
 c.set_trace_callback(trace)
 def record():fault(db);recorded.set()
 with ThreadPoolExecutor(max_workers=2) as pool:
  f=pool.submit(c.execute,'RELEASE[s]');assert entered.wait(5);r=pool.submit(record)
  try:assert not recorded.wait(.1)
  finally:release.set()
  f.result(5);r.result(5)
 assert count(path)==1;c.set_trace_callback(None);raw.close();db.close()


def child(path,ready,finish,result):
 db=Database('sqlite:///'+str(path));raw=db.engine.raw_connection();c=raw.driver_connection
 try:
  c.execute('SAVEPOINT`s`');c.execute('INSERT INTO probe VALUES(1)');ready.set();assert finish.wait(10)
  c.execute('RELEASE"s"');result.put('committed')
 except ContractError as e:result.put(e.code)
 finally:raw.close();db.close()


def test_spawn_fault_before_outer_savepoint_release(tmp_path):
 path=tmp_path/'spawn.sqlite';db=setup(path);ctx=multiprocessing.get_context('spawn');ready,finish=ctx.Event(),ctx.Event();result=ctx.Queue()
 process=ctx.Process(target=child,args=(path,ready,finish,result));process.start();assert ready.wait(10)
 try:fault(db)
 finally:finish.set()
 process.join(10);assert not process.is_alive() and process.exitcode==0
 assert result.get(timeout=2)=='DATABASE_UNAVAILABLE' and count(path)==0;db.close()


def test_nested_savepoints_rollback_to_and_release_and_cancellation(tmp_path):
 path=tmp_path/'nested.sqlite';db=setup(path);raw=db.engine.raw_connection();c=raw.driver_connection
 c.execute('SAVEPOINT[outer]');c.execute('INSERT INTO probe VALUES(1)');c.execute('SAVEPOINT"inner"');c.execute('INSERT INTO probe VALUES(2)')
 c.execute('ROLLBACK/**/TO[inner]');c.execute('RELEASE"inner"');c.execute('RELEASE[outer]');assert count(path)==1
 try:
  with c:
   c.execute('SAVEPOINT[s]');c.execute('INSERT INTO probe VALUES(3)');raise KeyboardInterrupt('generated cancellation')
 except KeyboardInterrupt:pass
 assert count(path)==1
 with pytest.raises(ContractError):c.set_authorizer(None)
 raw.close();db.close()
