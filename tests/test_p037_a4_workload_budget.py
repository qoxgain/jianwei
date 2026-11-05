"""Real WAL backup distinguishes deadline-before-callback from cancellation.

Generated source only. A slow phase represents durable receipt/preflight I/O;
the online writer must remain usable after either safe failure, with no target.
"""
from contextlib import closing
import sqlite3,time
from threading import Event
import pytest
from packages.config.errors import ContractError
from tests.test_p037_a2_backup import setup


@pytest.mark.parametrize('budget,expected,callback_expected',[
    (.05,'DATABASE_BACKUP_TIMEOUT',False),
    (2,'DATABASE_BACKUP_CANCELLED',True),
])
def test_wal_slow_preflight_does_not_confuse_deadline_with_callback_cancellation(tmp_path,budget,expected,callback_expected):
    source=tmp_path/'generated.sqlite';db=setup(source,'WAL',timeout=budget)
    raw=db.engine.raw_connection();c=raw.driver_connection
    c.execute('BEGIN EXCLUSIVE');c.execute('INSERT INTO probe VALUES(11)')
    cancel=Event();reached=Event();target=tmp_path/'archive.sqlite'
    def phase(name,_state):
        if name=='before_copy':time.sleep(.1)
    def progress(*_):reached.set();cancel.set()
    try:
        with pytest.raises(ContractError) as e:db.backup(target,cancel_event=cancel,progress=progress,phase_observer=phase)
        assert e.value.code==expected and reached.is_set()==callback_expected
        assert not target.exists() and db.fault is None
        c.commit()
        with closing(sqlite3.connect(source)) as independent:
            assert independent.execute('SELECT id FROM probe').fetchall()==[(11,)]
            assert independent.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
    finally:c.rollback();raw.close();db.close()
