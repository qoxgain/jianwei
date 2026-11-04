"""Fixed A.2 integrity-result defect, generated fixture copies only."""
import argparse,json,shutil,sqlite3,subprocess
from pathlib import Path
from tests.sqlite_corruption_fixture import generated_corrupt_index


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()=='642984426e8faa251a776cedee47d48c8529d918'
    a.output.mkdir(parents=True,exist_ok=False);fixture=generated_corrupt_index(a.output/'raw')
    working=a.output/'working.sqlite';shutil.copyfile(fixture['damaged'],working)
    from infra.database import Database
    from packages.config.errors import ContractError
    from sqlalchemy import text
    db=Database('sqlite:///'+str(working.resolve()));result={'baseline':'6429844','fixture_kind':fixture['kind']}
    with sqlite3.connect(working) as c:result['native_quick_check_rows']=c.execute('PRAGMA quick_check').fetchall()
    try:db.check_integrity();result['check']='unexpected_ok'
    except ContractError as e:result['check']=e.code
    result['persistent_marker_exists']=db.quarantine_path.exists();result['fault_state']=db.fault
    with db.session() as s:s.execute(text("INSERT INTO probe(value) VALUES('generated-after-failed-check')"))
    with sqlite3.connect(working) as c:result['independent_rows_after_commit']=c.execute('SELECT COUNT(*) FROM probe').fetchone()[0]
    db.close();(a.output/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
