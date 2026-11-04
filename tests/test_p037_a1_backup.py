"""Native source.backup cannot accept any supported managed target handle."""
import sqlite3
from types import SimpleNamespace as N
import pytest
from sqlalchemy import text
from infra.database import Database
from packages.config.errors import ContractError


@pytest.mark.parametrize('faulted',[False,True])
@pytest.mark.parametrize('handle',['driver','cursor','execute','context','connection_factory','cursor_factory'])
def test_native_backup_cannot_write_managed_target(tmp_path,faulted,handle):
 path=tmp_path/'target.sqlite';db=Database('sqlite:///'+str(path));db.migrate()
 with db.session() as s:s.execute(text('CREATE TABLE probe(id INTEGER)'));s.execute(text('INSERT INTO probe VALUES(7)'))
 raw=db.engine.raw_connection();c=raw.driver_connection
 if handle=='cursor':target=c.cursor().connection
 elif handle=='execute':target=c.execute('SELECT 1').connection
 elif handle=='context':target=c.__enter__()
 elif handle=='connection_factory':
  c.row_factory=lambda cursor,row:cursor.connection
  target=c.execute('SELECT 1').fetchone()
 elif handle=='cursor_factory':
  cursor=c.cursor();cursor.row_factory=lambda cursor,row:cursor.connection
  target=cursor.execute('SELECT 1').fetchone()
 else:target=c
 if faulted:db._record_fault(N(original_exception=N(sqlite_errorname='SQLITE_IOERR')))
 assert not isinstance(target,sqlite3.Connection)
 with sqlite3.connect(':memory:') as source:
  source.execute('CREATE TABLE injected(id INTEGER)');source.commit()
  with pytest.raises(TypeError):source.backup(target)
 with sqlite3.connect(path) as check:
  assert check.execute('SELECT * FROM probe').fetchall()==[(7,)]
  assert not check.execute("SELECT name FROM sqlite_master WHERE name='injected'").fetchall()
 raw.rollback();raw.close();db.close()


def test_row_factories_keep_standard_rows_and_supported_callbacks(tmp_path):
 db=Database('sqlite:///'+str(tmp_path/'factory.sqlite'));db.migrate();raw=db.engine.raw_connection();c=raw.driver_connection
 c.row_factory=sqlite3.Row;row=c.execute('SELECT 3 AS number').fetchone();assert row['number']==3
 callback=lambda cursor,row:{cursor.description[0][0]:row[0]}
 c.row_factory=callback;cursor=c.cursor()
 assert c.row_factory is callback and cursor.row_factory is callback
 assert cursor.execute('SELECT 4 AS number').fetchone()=={'number':4}
 cursor.row_factory=None;assert cursor.execute('SELECT 5').fetchone()==(5,)
 c.row_factory=None;assert c.execute('SELECT 6').fetchone()==(6,)
 raw.close();db.close()


def test_only_new_file_backup_remains_verified_and_fault_does_not_restore(tmp_path):
 path=tmp_path/'source.sqlite';db=Database('sqlite:///'+str(path));db.migrate()
 with db.session() as s:s.execute(text('CREATE TABLE probe(id INTEGER)'));s.execute(text('INSERT INTO probe VALUES(4)'))
 target=tmp_path/'new-backup.sqlite';result=db.backup(target);assert result['status']=='ok'
 with sqlite3.connect(target) as check:assert check.execute('SELECT * FROM probe').fetchall()==[(4,)]
 with pytest.raises(FileExistsError):db.backup(target)
 raw=db.engine.raw_connection()
 with pytest.raises(ContractError):raw.driver_connection.backup(sqlite3.connect(':memory:'))
 raw.close();db._record_fault(N(original_exception=N(sqlite_errorname='SQLITE_IOERR')))
 with pytest.raises(ContractError):
  with db.session():pytest.fail('fault cleared')
 db.close();again=Database('sqlite:///'+str(path));assert again.fault is not None;again.close()
