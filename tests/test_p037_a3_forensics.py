"""A read-only evidence acquisition must not release the caller's SQLite locks."""
import json
import subprocess
import sys


def test_forensic_copy_keeps_parent_exclusive_lock(tmp_path):
    out=tmp_path/'isolated-forensics'
    subprocess.run([sys.executable,'-m','scripts.check_sqlite_forensic_lock','--output',str(out)],
        check=True,timeout=25,capture_output=True,text=True)
    result=json.loads((out/'results.json').read_text())
    assert result['before']['result']==result['after']['result']=='SQLITE_BUSY'
    assert result['caller_pid']!=result['forensic_worker_pid']
    assert result['source_bytes_unchanged'] and result['source_integrity_after_close']=='ok'
