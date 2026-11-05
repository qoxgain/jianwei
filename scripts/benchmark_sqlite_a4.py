"""Generated SQLite workloads; fixed baselines, real wall/CPU/IO/latency only.

Warm steady-state transactions are timed separately from admission/connection
startup and full checks. Latency samples/logs remain private, not feature data.
No result is inferred for a failed/timed-out case. Every case gets a fresh copy.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib,json,math,os,platform,random,resource,shutil,signal,sqlite3,subprocess,sys,time
from pathlib import Path
from threading import Barrier,Lock,Thread

ROOT=Path(__file__).resolve().parents[1]


def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def fixture(path,mib):
    target=mib*1024*1024;rows=0
    with closing(sqlite3.connect(path)) as c:
        c.execute('PRAGMA page_size=4096');c.execute('PRAGMA journal_mode=DELETE');c.execute('PRAGMA synchronous=FULL')
        c.execute('CREATE TABLE payload(id INTEGER PRIMARY KEY,value BLOB NOT NULL)')
        c.execute('CREATE INDEX payload_group ON payload((id%127))')
        c.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,writer INTEGER,payload TEXT NOT NULL)')
        while c.execute('PRAGMA page_count').fetchone()[0]*4096<target:
            c.executemany('INSERT INTO payload VALUES(?,zeroblob(900))',((rows+i,) for i in range(512)))
            rows+=512
        c.commit();assert c.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    return {'requested_mib':mib,'bytes':path.stat().st_size,'payload_rows':rows,'sha256':digest,
        'layout':'900-byte generated zero BLOB rows, primary key + id modulo index; 64-byte event inserts'}


def io():
    try:
        return {k:int(v) for k,v in (line.split(':') for line in Path('/proc/self/io').read_text().splitlines())}
    except OSError:return {}


def usage():
    r=resource.getrusage(resource.RUSAGE_SELF)
    return {'user_cpu_seconds':r.ru_utime,'system_cpu_seconds':r.ru_stime,'peak_rss_kib':r.ru_maxrss,'io':io()}


def difference(before,after):
    return {'user_cpu_seconds':after['user_cpu_seconds']-before['user_cpu_seconds'],
        'system_cpu_seconds':after['system_cpu_seconds']-before['system_cpu_seconds'],
        'peak_rss_kib':after['peak_rss_kib'],
        'io_delta':{k:after['io'][k]-before['io'].get(k,0) for k in after['io']}}


def quantiles(samples):
    values=sorted(samples)
    def q(p):
        if not values:return None
        pos=(len(values)-1)*p;lower=math.floor(pos);upper=math.ceil(pos)
        return values[lower]+(values[upper]-values[lower])*(pos-lower)
    return {'samples':len(values),'p50_ms':q(.5),'p95_ms':q(.95),'p99_ms':q(.99),
        'tail_scope':'descriptive_only_small_sample' if len(values)<100 else 'finite_run_descriptive_not_SLO'}


def full_check(path):
    start=time.perf_counter()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as c:
        rows=c.execute('PRAGMA integrity_check').fetchall()
    return {'ms':1000*(time.perf_counter()-start),'ok':rows==[('ok',)]}


def error_code(e):
    original=getattr(e,'orig',e)
    return getattr(original,'sqlite_errorname',None) or getattr(e,'code',None) or type(original).__name__


def case(out,spec):
    from infra.database import Database
    from packages.contracts.database import SQLiteSettings,SQLiteBackupSettings
    out.mkdir(parents=True,exist_ok=False);path=out/'generated.sqlite';shutil.copyfile(spec['fixture_path'],path)
    with closing(sqlite3.connect(path)) as c:
        assert c.execute('PRAGMA journal_mode='+spec['journal_mode']).fetchone()[0].upper()==spec['journal_mode']
    configuration={'journal_mode':spec['journal_mode'],'synchronous':'FULL','busy_timeout_ms':1000,'min_free_bytes':0,
        'backup':SQLiteBackupSettings(timeout_seconds=20,pages_per_step=16,retry_seconds=.001)}
    if spec['variant']=='a4_fault_state':
        configuration['commit_integrity_check']='fault_state';configuration['integrity_check_interval_seconds']=1
    elif spec['variant']=='a4_full':configuration['commit_integrity_check']='full'
    policy=SQLiteSettings(**configuration);started=time.perf_counter();db=Database('sqlite:///'+str(path),policy)
    raws=[];samples=[];mutex=Lock();count=0;backup_report={'requested':spec['backup']}
    result={'spec':{k:v for k,v in spec.items() if k!='fixture_path'},'status':'started','generated_only':True,
        'environment':{'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,'platform':platform.platform()},
        'code_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'configuration':policy.model_dump(),
        'source_sha256':{str(p):hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in map(Path,
            ['infra/database.py','infra/sqlite_guard.py','infra/sqlite_backup.py','infra/sqlite_connection.py',
             'infra/sqlite_health.py','scripts/benchmark_sqlite_a4.py']) if (ROOT/p).exists()}}
    try:
        if hasattr(db,'register_legacy'):
            from infra.sqlite_admission import digest_separate_process
            db.register_legacy({'reviewer':'generated-benchmark','reference':'generated-only-not-field',
                'expected_sha256':digest_separate_process(path),'writers_stopped':True,'consumption_reviewed':True,'legacy_business':True})
        for i in range(spec['clients']):
            raw=db.engine.raw_connection();c=raw.driver_connection
            c.execute('PRAGMA cache_size=-2048');c.execute('INSERT INTO events VALUES(?,?,?)',(-i-1,i,'0'*64));c.rollback();raws.append(raw)
        result['startup_seconds']=time.perf_counter()-started
        result['initial_full_check']=full_check(path);assert result['initial_full_check']['ok']
        result['initial_main_bytes']=path.stat().st_size
        if spec['quarantined']:
            transition=time.perf_counter();db.fault={'reason':'generated_performance_quarantine','root_cause':'generated_fixture_not_original_incident'}
            result['quarantine_transition_ms']=1000*(time.perf_counter()-transition)
        elif hasattr(db,'start_integrity_monitor'):db.start_integrity_monitor()
        barrier=Barrier(spec['clients']+1);started_write=[None];backup_thread=None

        def writer(i):
            nonlocal count
            c=raws[i].driver_connection;local_count=0;barrier.wait()
            while True:
                elapsed=time.perf_counter()-started_write[0]
                with mutex:enough=count>=spec['min_transactions']
                if spec['quarantined']:
                    if local_count>=100:return
                elif (elapsed>=spec['seconds'] and enough) or elapsed>=max(12,spec['seconds']*4):return
                begin=time.perf_counter();code=None
                try:
                    c.execute('INSERT INTO events VALUES(?,?,?)',(i*10000000+local_count+1,i,'0'*64));c.commit()
                    with mutex:count+=1
                except Exception as e:c.rollback();code=error_code(e)
                duration=1000*(time.perf_counter()-begin)
                with mutex:samples.append({'writer':i,'ms':duration,'status':code or 'committed'})
                local_count+=1

        def copy():
            backup_report['start_offset_seconds']=time.perf_counter()-started_write[0]
            try:backup_report['result']=db.backup(out/'online-archive.sqlite')['status']
            except Exception as e:backup_report['error']=error_code(e)
            backup_report['finish_offset_seconds']=time.perf_counter()-started_write[0]

        before=usage()
        with ThreadPoolExecutor(max_workers=spec['clients']) as pool:
            jobs=[pool.submit(writer,i) for i in range(spec['clients'])];started_write[0]=time.perf_counter()
            if spec['backup']:backup_thread=Thread(target=copy);backup_thread.start()
            barrier.wait()
            for job in jobs:job.result(timeout=40)
            write_elapsed=time.perf_counter()-started_write[0];after=usage()
        if backup_thread is not None:
            backup_thread.join(25)
            if backup_thread.is_alive():raise TimeoutError('backup did not stop within its deadline')
            backup_report['overlap_seconds']=max(0,min(write_elapsed,backup_report['finish_offset_seconds'])-backup_report['start_offset_seconds'])
            assert backup_report.get('result')=='ok',backup_report
        successes=[s['ms'] for s in samples if s['status']=='committed'];errors=[s for s in samples if s['status']!='committed']
        errors_by_code={code:sum(s['status']==code for s in errors) for code in {s['status'] for s in errors}}
        locking={'SQLITE_BUSY','SQLITE_LOCKED','DATABASE_COORDINATION_UNAVAILABLE','RESOURCE_LOCK_TIMEOUT','DATABASE_ADMISSION_BUSY'}
        lock_failures=sum(n for code,n in errors_by_code.items() if any(code.startswith(k) for k in locking))
        result.update(committed=count,attempts=len(samples),write_seconds=write_elapsed,tps=count/write_elapsed,
            successful_latency=quantiles(successes),all_attempt_latency=quantiles([s['ms'] for s in samples]),
            errors_by_code=errors_by_code,lock_failure_fraction=lock_failures/max(1,len(samples)),
            resources=difference(before,after),backup=backup_report)
        if hasattr(db,'integrity_policy'):result['integrity_policy_at_write_end']=db.integrity_policy()
        if spec['quarantined']:
            assert count==0 and errors_by_code=={'DATABASE_UNAVAILABLE':spec['clients']*100}
            result['rejected_attempts_per_second']=len(samples)/write_elapsed
        else:
            assert count>=spec['min_transactions'] and all(any(code.startswith(k) for k in locking) for code in errors_by_code),errors_by_code
        for raw in raws:raw.rollback();raw.close()
        raws=[];db.close()
        result['final_full_check']=full_check(path);assert result['final_full_check']['ok']
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
            assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==count
        result['final_main_bytes']=path.stat().st_size;result['status']='passed'
    except Exception as e:
        result.update(status='failed',error={'type':type(e).__name__,'code':error_code(e),'message':str(e)[:400]})
    finally:
        for raw in raws:raw.rollback();raw.close()
        db.close()
        with (out/'transactions.jsonl').open('w') as stream:
            for sample in samples:stream.write(json.dumps(sample)+'\n')
        save(out/'results.json',result)
    return result


def run(command,workdir,target,timeout):
    with subprocess.Popen(command,cwd=workdir,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True) as process:
        try:stdout,stderr=process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);stdout,stderr=process.communicate();stderr+='\nPROCESS_TIMEOUT'
        target.write_text(stdout+stderr);return process.returncode


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--a3-root',type=Path);p.add_argument('--a2-root',type=Path);p.add_argument('--smoke',action='store_true')
    p.add_argument('--seconds',type=float,default=2);p.add_argument('--min-transactions',type=int,default=12)
    p.add_argument('--sizes-mib',type=int,nargs='+',default=[1,79,160]);p.add_argument('--seeds',type=int,nargs='+',default=[11,23])
    p.add_argument('--case-spec',type=Path,help=argparse.SUPPRESS);a=p.parse_args();out=a.output.resolve()
    if a.case_spec:
        r=case(out,json.loads(a.case_spec.read_text()));return 0 if r['status']=='passed' else 1
    out.mkdir(parents=True,exist_ok=False);inputs=out/'fixtures';inputs.mkdir()
    sizes=[1] if a.smoke else a.sizes_mib
    data={size:fixture(inputs/f'generated-{size}.sqlite',size) for size in sizes}
    roots={'a4_fault_state':ROOT,'a4_full':ROOT}
    if not a.smoke:
        if a.a3_root is None or a.a2_root is None:p.error('Fixed A.3 and A.2 worktrees are required')
        roots.update(a3=a.a3_root.resolve(),a2=a.a2_root.resolve())
        assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=roots['a3'],text=True).strip()=='fb69646336eb6346ea3cec498a08856a80a34e60'
        assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=roots['a2'],text=True).strip()=='642984426e8faa251a776cedee47d48c8529d918'
    specs=[]
    for seed in ([0] if a.smoke else a.seeds):
        order=[]
        for size in sizes:
            variants=list(roots) if size==79 or a.smoke else ['a3','a4_fault_state']
            for mode in ['DELETE','WAL']:
                for clients in [1,4]:
                    for backup in [False,True]:
                        for variant in variants:
                            order.append({'variant':variant,'size_mib':size,'journal_mode':mode,'clients':clients,'backup':backup,
                                'quarantined':False,'seed':seed,'seconds':.1 if a.smoke else a.seconds,
                                'min_transactions':3 if a.smoke else a.min_transactions,'fixture_path':str(inputs/f'generated-{size}.sqlite')})
        random.Random(seed).shuffle(order);specs+=order
    if not a.smoke:
        for variant in roots:
            for mode in ['DELETE','WAL']:
                for clients in [1,4]:
                    specs.append({'variant':variant,'size_mib':79,'journal_mode':mode,'clients':clients,'backup':False,
                        'quarantined':True,'seed':0,'seconds':0,'min_transactions':0,'fixture_path':str(inputs/'generated-79.sqlite')})
    manifest={'schema':'sqlite-a4-performance-v1','generated_only':True,'historical_independent_fixture_available':False,
        'layout':data,'case_order':'seed-shuffled; same generated input per size, fresh copy each case',
        'measurement':'warm retained connections; startup/full checks separately; FULL synchronous; 2MiB SQLite cache per connection',
        'p99_scope':'finite-run descriptive statistic, not a production SLO or independent field validation','runs':[]}
    for i,spec in enumerate(specs):
        name=f'{i:03d}-{spec["variant"]}-{spec["size_mib"]}-{spec["journal_mode"].lower()}';job=out/(name+'.json');save(job,spec)
        root=roots[spec['variant']]
        if root!=ROOT:shutil.copyfile(Path(__file__),root/'scripts/benchmark_sqlite_a4.py')
        target=out/name;code=run([sys.executable,'-m','scripts.benchmark_sqlite_a4','--output',str(target),'--case-spec',str(job)],
            root,out/(name+'.log'),90)
        result=json.loads((target/'results.json').read_text()) if (target/'results.json').exists() else {'status':'failed','spec':spec,'error':{'process_returncode':code}}
        result['process_returncode']=code;manifest['runs'].append(result);save(out/'results.json',manifest)
        print(json.dumps({'case':i+1,'total':len(specs),'variant':spec['variant'],'mib':spec['size_mib'],'mode':spec['journal_mode'],
            'clients':spec['clients'],'backup':spec['backup'],'status':result['status'],'tps':result.get('tps')}),flush=True)
    manifest['summary']={'runs':len(specs),'passed':sum(r['status']=='passed' for r in manifest['runs']),
        'failed':sum(r['status']!='passed' for r in manifest['runs'])};save(out/'results.json',manifest);print(json.dumps(manifest['summary']))
    return 0 if not manifest['summary']['failed'] else 1


if __name__=='__main__':raise SystemExit(main())
