"""Generated query/row-consumption counterexamples; no existing DB input accepted."""
import argparse,hashlib,json,sqlite3,subprocess,sys
from pathlib import Path
from tests.sqlite_query_fixture import generated_query_case,consume,sqlalchemy_consume,independent_rows
from packages.config.errors import ContractError


def case(root,strategy,journal,route):
    db,path,reader,writer=generated_query_case(root,strategy,journal);stage=[]
    record={'strategy':strategy,'journal':journal,'route':route,'generated_only':True}
    try:
        try:
            if route in {'result','orm'}:
                reader.close();reader=None
                sqlalchemy_consume(db,route,stage)
            else:consume(reader.driver_connection,route,stage)
        except Exception as wrapped:
            error=getattr(wrapped,'orig',wrapped)
            record.update(error_type=type(error).__name__,sqlite_errorname=getattr(error,'sqlite_errorname',None),
                sqlite_errorcode=getattr(error,'sqlite_errorcode',None),error_stage=stage[-1] if stage else None)
        record['fault_after_query']=db.fault is not None
        record['intent_after_query']=db.fault_intent_path.exists()
        if reader is not None:reader.driver_connection.rollback()
        try:
            writer.driver_connection.execute("INSERT INTO query_writes VALUES(99,'must-not-commit')")
            writer.driver_connection.commit();record['write_after_query']='committed'
        except ContractError as error:record['write_after_query']=error.code
        writer.driver_connection.rollback()
        record['independent_rows']=independent_rows(path)
        assert record.get('sqlite_errorcode',0)&255==sqlite3.SQLITE_CORRUPT,record
        return record
    finally:
        if reader is not None:reader.close()
        writer.close();db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--expect-vulnerable',action='store_true');a=parser.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    report={'schema':'sqlite-query-counterexample-v1','generated_only':True,'real_damaged_originals':'blocked_missing_original',
        'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,'source_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'source_sha256':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in
            ['infra/database.py','infra/sqlite_guard.py','infra/sqlite_connection.py','tests/sqlite_query_fixture.py','tests/sqlite_corruption_fixture.py','scripts/reproduce_sqlite_query_fault.py']},'runs':[]}
    for strategy in ['full','fault_state']:
        for journal in ['DELETE','WAL']:
            for route in ['execute','executemany','fetchone','fetchmany','fetchall','next','result','orm']:
                result=case(a.output/f'{strategy}-{journal}-{route}',strategy,journal,route);report['runs'].append(result)
                (a.output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
                print(json.dumps(result),flush=True)
    vulnerable=[r for r in report['runs'] if r['write_after_query']=='committed']
    report['summary']={'cases':len(report['runs']),'query_isolated':sum(r['intent_after_query'] for r in report['runs']),
        'illegal_writes':len(vulnerable),'independent_illegal_rows':sum(r['independent_rows'] for r in report['runs'])}
    (a.output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    if a.expect_vulnerable:
        assert {(r['journal'],r['route']) for r in vulnerable}>={(j,r) for j in ['DELETE','WAL'] for r in ['fetchone','fetchmany','fetchall','next']}
        assert all(r['strategy']=='fault_state' and r['route'] not in {'execute','executemany'} for r in vulnerable)
    else:assert not vulnerable and all(r['fault_after_query'] and r['intent_after_query'] for r in report['runs'])
    print(json.dumps(report['summary']))


if __name__=='__main__':main()
