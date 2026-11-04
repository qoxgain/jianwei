"""Own generated B-tree ordering defect; never accepts an existing database to mutate."""
from pathlib import Path
import hashlib,shutil,sqlite3


def generated_corrupt_index(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    clean=root/'generated-clean.sqlite';damaged=root/'generated-damaged.sqlite'
    with sqlite3.connect(clean) as c:
        c.execute('CREATE TABLE probe(id INTEGER PRIMARY KEY, value TEXT)')
        c.execute('CREATE TABLE payload(id INTEGER PRIMARY KEY, value TEXT)')
        c.executemany('INSERT INTO payload VALUES(?,?)',[(i,f'generated-{i:04d}') for i in range(32)])
        c.execute('CREATE INDEX generated_order ON payload(value)')
        c.commit();page=c.execute("SELECT rootpage FROM sqlite_master WHERE name='generated_order'").fetchone()[0]
        assert c.execute('PRAGMA quick_check').fetchall()==[('ok',)]
    shutil.copyfile(clean,damaged);raw=bytearray(damaged.read_bytes())
    page_size=int.from_bytes(raw[16:18],'big');page_size=65536 if page_size==1 else page_size
    header=(page-1)*page_size+(100 if page==1 else 0)
    assert raw[header]==0x0a and int.from_bytes(raw[header+3:header+5],'big')>=2
    # A count-only freelist inconsistency is returned as diagnostic rows rather
    # than necessarily raising SQLITE_CORRUPT. An unrelated leaf still accepts writes.
    assert int.from_bytes(raw[32:36],'big')==0 and int.from_bytes(raw[36:40],'big')==0
    raw[36:40]=(1).to_bytes(4,'big')
    damaged.write_bytes(raw);damaged.chmod(0o400)
    return {'clean':clean,'damaged':damaged,'kind':'generated_freelist_count_mismatch',
        'sha256':hashlib.sha256(raw).hexdigest(),'page_size':page_size,'index_root_page':page}
