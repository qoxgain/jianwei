"""Generated-only late B-tree damage; raw file descriptors live in another PID.

Never accepts an existing source to damage. Every case creates its own new
business file, closes it, locates a later payload leaf, then opens clean managed
connections before the isolated child modifies that generated leaf.
"""
from contextlib import closing
from pathlib import Path
import multiprocessing,sqlite3
from sqlalchemy import Integer,String,select,text
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column
from infra.database import Database
from packages.contracts.database import SQLiteSettings
from packages.config.errors import ContractError
from tests.sqlite_corruption_fixture import damage_generated_leaf,damage_generated_header


class Base(DeclarativeBase):pass
class QueryPayload(Base):
    __tablename__='query_payload'
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    value:Mapped[str]=mapped_column(String)


def generated_query_case(root,strategy='fault_state',journal='DELETE',corruption='leaf'):
    root=Path(root);root.mkdir(parents=True,exist_ok=False);path=root/'generated.sqlite'
    settings=SQLiteSettings(commit_integrity_check=strategy,journal_mode=journal,busy_timeout_ms=500,min_free_bytes=0)
    db=Database('sqlite:///'+str(path),settings);db.migrate()
    with db.engine.begin() as c:
        c.exec_driver_sql('CREATE TABLE query_payload(id INTEGER PRIMARY KEY,value TEXT)')
        c.exec_driver_sql('CREATE TABLE query_writes(id INTEGER PRIMARY KEY,value TEXT)')
        c.exec_driver_sql('INSERT INTO query_payload VALUES(?,?)',[(i,'generated-'+str(i)+'x'*220) for i in range(256)])
    db.close()
    with closing(sqlite3.connect(path)) as native:
        page_size=native.execute('PRAGMA page_size').fetchone()[0]
        leaves=native.execute("SELECT pageno FROM dbstat WHERE name='query_payload' AND pagetype='leaf' ORDER BY pageno").fetchall()
        assert len(leaves)>3;page=leaves[2][0]
    db=Database('sqlite:///'+str(path),settings)
    reader=db.engine.raw_connection();writer=db.engine.raw_connection()
    for raw in [reader,writer]:raw.driver_connection.execute('PRAGMA shrink_memory')
    ctx=multiprocessing.get_context('spawn')
    child=ctx.Process(target=damage_generated_leaf,args=(path,page_size,page)) if corruption=='leaf' else ctx.Process(target=damage_generated_header,args=(path,))
    child.start();child.join(10)
    if child.is_alive():child.terminate();child.join(2);raise TimeoutError('generated injection child did not exit')
    assert child.exitcode==0
    return db,path,reader,writer


def consume(connection,route,stage):
    if route=='execute':
        stage.append('execute');return connection.execute('SELECT sum(length(value)) FROM query_payload')
    if route=='executemany':
        stage.append('executemany');return connection.executemany('INSERT INTO query_writes(value) SELECT value FROM query_payload WHERE id>=?',[(0,)])
    cursor=connection.execute('SELECT id,value FROM query_payload ORDER BY id');stage.append('executed')
    try:
        if route=='fetchall':stage.append('fetchall');return cursor.fetchall()
        if route=='fetchone':
            while True:
                stage.append('fetchone');row=cursor.fetchone()
                if row is None:return
        if route=='fetchmany':
            while True:
                stage.append('fetchmany');rows=cursor.fetchmany(3)
                if not rows:return
        if route=='next':
            while True:stage.append('next');next(cursor)
        raise ValueError(route)
    finally:cursor.close()


def sqlalchemy_consume(db,route,stage):
    if route=='result':
        with db.engine.connect() as c:
            result=c.exec_driver_sql('SELECT id,value FROM query_payload ORDER BY id');stage.append('executed')
            try:
                while True:stage.append('result');next(result)
            finally:result.close()
    else:
        with db.session() as session:
            result=session.execute(select(QueryPayload).order_by(QueryPayload.id)).scalars().yield_per(1);stage.append('executed')
            try:
                for row in result:stage.append('orm');assert row.value.startswith('generated-')
            finally:result.close()


def independent_rows(path):
    with closing(sqlite3.connect(path)) as native:return native.execute('SELECT COUNT(*) FROM query_writes').fetchone()[0]


def child_write(path,result,strategy):
    db=Database('sqlite:///'+str(path),SQLiteSettings(commit_integrity_check=strategy,min_free_bytes=0))
    try:
        with db.session() as session:session.execute(text("INSERT INTO query_writes VALUES(99,'must-not-commit')"))
        result.put('committed')
    except ContractError as error:result.put(error.code)
    finally:db.close()
