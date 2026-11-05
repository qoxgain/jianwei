"""Repeat generated API/import + concurrent SQLite workloads; retain EVERY run.

This is an explicitly labelled analogue. The independent 11-run script and its
two damaged originals were not supplied; results cannot establish their cause.
Each case executes in a fresh process/directory, on disposable generated data.
Never hash/open the database as an ordinary file inside a live SQLite PID.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import platform
import random
import signal
import shutil
import sqlite3
import subprocess
import sys
from threading import Event, Lock, get_ident
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]


class Trace:
    def __init__(self,path):self.path=path;self.lock=Lock();self.sequence=0

    def __call__(self,stage,connection=None,**details):
        with self.lock:
            self.sequence+=1
            record={'sequence':self.sequence,'time_ns':time.time_ns(),'monotonic_ns':time.monotonic_ns(),
                'pid':os.getpid(),'thread':get_ident(),'stage':stage,**details}
            if connection is not None:
                record.update(connection_id=id(connection),in_transaction=connection.in_transaction)
            with self.path.open('a') as stream:stream.write(json.dumps(record)+'\n')


def settings(mode):
    from packages.contracts.database import SQLiteSettings,SQLiteBackupSettings
    return SQLiteSettings(journal_mode=mode,busy_timeout_ms=1000,min_free_bytes=0,
        backup=SQLiteBackupSettings(timeout_seconds=2,pages_per_step=1,retry_seconds=.002))


def write_batch(path,mode,start,count,trace_path,interrupted=False):
    from infra.database import Database
    trace=Trace(trace_path);db=Database('sqlite:///'+str(path),settings(mode))
    try:
        for i in range(count):
            raw=db.engine.raw_connection();c=raw.driver_connection
            try:
                trace('begin',c,writer_start=start)
                c.execute('INSERT INTO a3_probe VALUES(?,?)',(start+i,'generated'))
                trace('insert_before_commit',c)
                if interrupted:
                    # No Python cleanup: real process termination with an open journal.
                    trace('abrupt_exit_uncommitted',c);os._exit(17)
                if i%3==0:
                    c.execute('SAVEPOINT[nested]');c.execute('INSERT INTO a3_probe VALUES(?,?)',(-start-i,'rolled-back'))
                    c.execute('ROLLBACK TO[nested]');c.execute('RELEASE[nested]')
                c.commit();trace('commit_completed',c)
            finally:c.rollback();raw.close()
    finally:db.close();trace('connections_closed')


def run_case(output,mode,seed):
    from fastapi.testclient import TestClient
    import yaml
    from apps.api.main import create_app
    from infra.database import Database
    from packages.config.errors import ContractError
    from scripts.inspect_sqlite_copy import inspect as inspect_copy

    output.mkdir(parents=True,exist_ok=False);trace=Trace(output/'trace.jsonl');rng=random.Random(seed)
    (output/'config').mkdir();shutil.copytree(ROOT/'knowledge',output/'knowledge')
    config=yaml.safe_load((ROOT/'config/system.yaml').read_text());config['sqlite']=settings(mode).model_dump()
    config_path=output/'config/system.yaml';config_path.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
    result={'journal_mode':mode,'seed':seed,'generated_only':True,'status':'started','checks':[],
        'root_cause':'root_cause_not_established','failures':[],'backup_results':[]}
    app=create_app(config_path);db=None;path=None

    def check(stage):
        outcome=db.check_integrity();result['checks'].append({'stage':stage,'status':outcome['status']})
        trace(stage,integrity=outcome['status'],category=outcome.get('category'),fault_present=db.fault is not None)
        assert outcome['status']=='ok'

    try:
        with TestClient(app) as client:
            db=app.state.database;path=Path(db.engine.url.database)
            info=path.stat();trace('startup_migrated',device=info.st_dev,inode=info.st_ino,journal_mode=mode)
            with db.engine.begin() as c:c.exec_driver_sql('CREATE TABLE a3_probe(id INTEGER PRIMARY KEY,value TEXT)')
            check('before_concurrent_work')

            def copy(index,action):
                target=output/f'archive-{index}.sqlite';cancel=Event()
                def progress(status,remaining,total):
                    trace('backup_progress',index=index,status=status,remaining=remaining,total=total)
                    if action=='cancel':cancel.set()
                    if action=='exception':raise RuntimeError('generated callback failure')
                try:
                    report=db.backup(target,cancel_event=cancel,progress=progress)
                    assert action=='normal';return {'action':action,'status':report['status']}
                except ContractError as e:
                    expected={'cancel':'DATABASE_BACKUP_CANCELLED','exception':'DATABASE_BACKUP_FAILED'}.get(action)
                    assert e.code==expected,(action,e.code)
                    assert not target.exists();return {'action':action,'status':e.code}

            def checks_during_work():
                for _ in range(3):
                    check('concurrent_integrity');time.sleep(.003)

            # Seed changes submission order; journal/transaction schedule is explicit in trace.
            jobs=[('writer',i) for i in range(3)]+[('copy',i) for i in range(3)]+[('check',0)]
            rng.shuffle(jobs)
            with ThreadPoolExecutor(max_workers=7) as pool:
                futures=[]
                for kind,i in jobs:
                    if kind=='writer':future=pool.submit(write_batch,path,mode,100+i*100,6,output/f'writer-{i}.jsonl')
                    elif kind=='copy':future=pool.submit(copy,i,['normal','cancel','exception'][i])
                    else:future=pool.submit(checks_during_work)
                    futures.append((kind,future))
                # Actual durable import worker, explicitly synthetic/unassigned, no model or sealed data.
                queued=client.post('/api/scenes/anonymous_observation/imports/example')
                assert queued.status_code==202,queued.text;identifier=queued.json()['id'];trace('import_queued')
                deadline=time.monotonic()+15
                while True:
                    report=client.get('/api/scenes/anonymous_observation/imports/'+identifier).json()
                    if report['status'] not in {'queued','running'}:break
                    assert time.monotonic()<deadline;time.sleep(.01)
                assert report['status']=='succeeded',report.get('error')
                assert report['manifest']['data_origin']=='synthetic_features' and report['manifest']['split']=='unassigned'
                trace('import_succeeded');result['synthetic_import']='succeeded'
                for kind,future in futures:
                    value=future.result(10)
                    if kind=='copy':result['backup_results'].append(value)
            check('after_threads_and_import')

            # Writer-first barrier reproduces the original DELETE/WAL backup lock order.
            raw=db.engine.raw_connection();c=raw.driver_connection;c.execute('BEGIN EXCLUSIVE')
            c.execute('INSERT INTO a3_probe VALUES(1,?)',('exclusive',));trace('exclusive_writer',c)
            entered=Event();cancel=Event()
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending=pool.submit(db.backup,output/'exclusive-backup.sqlite',cancel_event=cancel,
                    progress=lambda *_:entered.set())
                try:
                    assert entered.wait(1);trace('backup_started_with_writer',c)
                    pool.submit(c.commit).result(1);trace('exclusive_commit_completed',c)
                    assert pending.result(3)['status']=='ok'
                finally:cancel.set();c.rollback();raw.close()
            check('after_exclusive_backup')

            # Separate owner deadline expires safely; writer continues and rolls back.
            timeout_owner=Database('sqlite:///'+str(path),settings(mode))
            # DELETE deliberately waits on an EXCLUSIVE writer until the short
            # deadline. WAL tests callback cancellation instead: keep the normal
            # budget so durable preflight/receipt I/O cannot consume a 50ms
            # deadline before that callback is reached on a loaded CI runner.
            if mode=='DELETE':timeout_owner.sqlite_settings.backup.timeout_seconds=.05
            raw=db.engine.raw_connection();c=raw.driver_connection;c.execute('BEGIN EXCLUSIVE')
            c.execute('INSERT INTO a3_probe VALUES(2,?)',('timeout-rollback',))
            cancel=Event()
            try:
                # In WAL a reader can legally finish; force cancellation in callback there.
                if mode=='WAL':
                    try:timeout_owner.backup(output/'bounded.sqlite',cancel_event=cancel,progress=lambda *_:cancel.set())
                    except ContractError as e:
                        trace('bounded_backup_result',code=e.code,expected='DATABASE_BACKUP_CANCELLED')
                        assert e.code=='DATABASE_BACKUP_CANCELLED',(mode,e.code)
                    else:raise AssertionError('cancel ignored')
                else:
                    try:timeout_owner.backup(output/'bounded.sqlite')
                    except ContractError as e:
                        trace('bounded_backup_result',code=e.code,expected='DATABASE_BACKUP_TIMEOUT')
                        assert e.code=='DATABASE_BACKUP_TIMEOUT',(mode,e.code)
                    else:raise AssertionError('timeout ignored')
                trace('bounded_wait_finished',c)
            finally:c.rollback();raw.close();timeout_owner.close()
            check('after_cancel_timeout')
        # API closes workers before its Database. Reopen independently in a fresh owner.
        trace('api_workers_and_database_closed');db=Database('sqlite:///'+str(path),settings(mode));db.migrate()
        check('after_reopen')
        ctx=multiprocessing.get_context('spawn')
        children=[ctx.Process(target=write_batch,args=(path,mode,1000+i*100,4,output/f'process-{i}.jsonl')) for i in range(2)]
        for child in children:child.start()
        for child in children:
            child.join(12)
            if child.is_alive():child.terminate();child.join(2);raise TimeoutError('spawn writer timed out')
            assert child.exitcode==0
        check('after_spawn_commits')
        interrupted=ctx.Process(target=write_batch,args=(path,mode,9999,1,output/'interrupted.jsonl',True));interrupted.start();interrupted.join(10)
        if interrupted.is_alive():interrupted.terminate();interrupted.join(2);raise TimeoutError('interrupted writer timed out')
        assert interrupted.exitcode==17;trace('process_exited_uncommitted',exitcode=interrupted.exitcode)
        check('after_abrupt_exit_recovery')
        with db.engine.connect() as c:
            assert c.exec_driver_sql('SELECT COUNT(*) FROM a3_probe').scalar_one()==27
            result['independent_expected_rows']=27
        result['status']='passed'
    except BaseException as e:
        result['status']='failed';result['failures'].append({'type':type(e).__name__,'code':getattr(e,'code',None),'message':str(e)[:500],
            'traceback':traceback.format_exc()})
        trace('failed',error_type=type(e).__name__,error_code=getattr(e,'code',None))
    finally:
        if db is not None:db.close()
        trace('all_managed_handles_closed')
        if path is not None and path.is_file():
            # Separate exec PID handles all raw evidence FDs, even on unexpected failure.
            inspected=inspect_copy(path,output/'cold-evidence')
            result['final_full_integrity']=inspected['integrity']
            result['source_evidence_unchanged']=inspected['source_bytes_stable_during_copy']
            if inspected['integrity']!='ok':result['status']='failed'
        (output/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seeds',type=int,nargs='+',default=list(range(11)));p.add_argument('--modes',nargs='+',choices=['DELETE','WAL'],default=['DELETE','WAL'])
    p.add_argument('--case',action='store_true',help=argparse.SUPPRESS);a=p.parse_args();out=a.output.resolve()
    if a.case:
        result=run_case(out,a.modes[0],a.seeds[0]);return 0 if result['status']=='passed' else 1
    out.mkdir(parents=True,exist_ok=False)
    report={'schema':'generated-sqlite-workload-v1','generated_only':True,'exact_independent_workload':False,
        'independent_evidence_available':False,'root_cause':'root_cause_not_established',
        'environment':{'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,'platform':platform.platform()},
        'code_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'source_sha256':{str(f):hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in
            map(Path,['infra/database.py','infra/sqlite_connection.py','infra/sqlite_guard.py','infra/sqlite_backup.py',
                'scripts/inspect_sqlite_copy.py','scripts/investigate_sqlite_workload.py'])},'runs':[]}
    for mode in a.modes:
        for seed in a.seeds:
            target=out/f'{mode.lower()}-{seed}';command=[sys.executable,'-m','scripts.investigate_sqlite_workload','--case',
                '--output',str(target),'--modes',mode,'--seeds',str(seed)]
            try:
                with subprocess.Popen(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    text=True,start_new_session=True) as run:
                    try:stdout,stderr=run.communicate(timeout=50)
                    except subprocess.TimeoutExpired:
                        # All spawned children inherit this session. Do not leave
                        # live writers behind while collecting failed evidence.
                        os.killpg(run.pid,signal.SIGKILL);stdout,stderr=run.communicate()
                        raise subprocess.TimeoutExpired(command,50,output=stdout,stderr=stderr)
                (out/f'{mode.lower()}-{seed}.log').write_text(stdout+stderr)
                result=json.loads((target/'results.json').read_text()) if (target/'results.json').exists() else {'status':'failed','failures':[{'process_returncode':run.returncode}]}
                result.update(journal_mode=mode,seed=seed,process_returncode=run.returncode)
            except subprocess.TimeoutExpired as e:
                (out/f'{mode.lower()}-{seed}.log').write_text('PROCESS_TIMEOUT\n'+(e.stdout or '')+(e.stderr or ''))
                result={'status':'failed','journal_mode':mode,'seed':seed,'failures':[{'type':'ProcessTimeout'}]}
            report['runs'].append(result)
            (out/'results.json').write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps({'mode':mode,'seed':seed,'status':result['status'],'integrity':result.get('final_full_integrity')}),flush=True)
    report['summary']={'runs':len(report['runs']),'passed':sum(r['status']=='passed' for r in report['runs']),
        'failed':sum(r['status']!='passed' for r in report['runs']),
        'structural_damage_observed':sum(r.get('final_full_integrity')=='failed' for r in report['runs'])}
    (out/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['summary']))
    return 1 if report['summary']['failed'] else 0


if __name__=='__main__':raise SystemExit(main())
