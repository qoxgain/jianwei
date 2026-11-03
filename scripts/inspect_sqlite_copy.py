"""Preserve raw SQLite evidence and inspect only a separate working copy.

Stop writers before acquisition: raw DB/WAL/SHM file copies are not an atomic
online backup. This tool never opens the original database with SQLite.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def inspect(source, output):
    source=Path(source).resolve();output=Path(output).resolve()
    if not source.is_file() or output==source or source.is_relative_to(output):
        raise ValueError('Choose an existing source and a separate NEW evidence directory')
    output.mkdir(parents=True,exist_ok=False)
    raw=output/'raw';working=output/'working';raw.mkdir();working.mkdir()
    files=[p for p in (source,Path(str(source)+'-wal'),Path(str(source)+'-shm'),Path(str(source)+'-journal')) if p.is_file()]
    evidence={}
    for path in files:
        before=sha(path);shutil.copyfile(path,raw/path.name);shutil.copyfile(raw/path.name,working/path.name)
        evidence[path.name]={'before_sha256':before,'copy_sha256':sha(raw/path.name),
            'after_sha256':sha(path),'bytes':path.stat().st_size}
    stable=all(item['before_sha256']==item['copy_sha256']==item['after_sha256'] for item in evidence.values())
    result={'original_opened_by_sqlite':False,'original_files_written':False,
        'files':evidence,'source_bytes_stable_during_copy':stable,
        'copy_atomicity':'not_guaranteed_stop_writers_before_acquisition',
        'root_cause':'not_established','integrity':'not_checked'}
    if stable:
        try:
            with sqlite3.connect(working/source.name) as connection:
                rows=connection.execute('PRAGMA integrity_check').fetchall()
                result['integrity']='ok' if rows==[('ok',)] else 'failed'
                result['integrity_message_count']=len(rows)
                result['journal_mode']=connection.execute('PRAGMA journal_mode').fetchone()[0]
        except sqlite3.DatabaseError as error:
            result.update(integrity='failed',sqlite_errorname=getattr(error,'sqlite_errorname','SQLITE_ERROR'))
    (output/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    result=inspect(args.database,args.output_dir)
    print(json.dumps({k:result[k] for k in ('integrity','original_files_written','root_cause')}))
    return 0 if result['integrity']=='ok' else 2


if __name__=='__main__':raise SystemExit(main())
