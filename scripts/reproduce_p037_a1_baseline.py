"""Run only in an isolated b029f78 worktree; generated inputs, no field data."""
import argparse,asyncio,hashlib,json,os,shutil,socket,sqlite3,subprocess,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace as N
from urllib.request import urlopen,Request
from urllib.error import URLError,HTTPError
BASE='b029f783689de2bf94f0a8e1c5dd743516ddf5af'


def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2)+'\n')
def fault(db):db._record_fault(N(original_exception=N(sqlite_errorname='SQLITE_IOERR')))


def threads(count,root):
    import anyio
    from infra.file_gate import file_gate
    from packages.media.access import GuardedResponse
    from starlette.responses import StreamingResponse
    async def run():
        limiter=anyio.to_thread.current_default_thread_limiter();limiter.total_tokens=count
        gate=file_gate(root/'media.lock');first=anyio.Event();resume=anyio.Event();done=[]
        async def response(name):
            async def send(message):
                if name=='owner' and message['type']=='http.response.body' and not first.is_set():first.set();await resume.wait()
            async def receive():await anyio.sleep_forever()
            await GuardedResponse(StreamingResponse(iter([b'one',b'two'])),gate,lambda:None)(
                {'type':'http','method':'GET','asgi':{'spec_version':'2.4'}},receive,send)
            done.append(name)
        async with anyio.create_task_group() as tg:
            tg.start_soon(response,'owner');await first.wait()
            tg.start_soon(response,'waiter1');tg.start_soon(response,'waiter2')
            for _ in range(100):
                if limiter.borrowed_tokens==2:break
                await anyio.sleep(.01)
            save(root/'thread-progress.json',{'tokens':count,'borrowed':limiter.borrowed_tokens,'owner_first_chunk':first.is_set()})
            resume.set()
        save(root/'thread-result.json',{'tokens':count,'completed':done})
    anyio.run(run)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--threads',type=int);args=p.parse_args()
    assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==BASE,'Use the isolated fixed baseline'
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    if args.threads:threads(args.threads,out);return
    from infra.database import Database
    from sqlalchemy import text
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    result={'baseline':BASE,'scope':'generated pattern and disposable databases only'}
    for clause in ('RELEASE[s]','RELEASE"s"'):
        dbfile=out/(str(len(result))+'.sqlite');db=Database('sqlite:///'+str(dbfile));db.migrate()
        with db.session() as session:session.execute(text('CREATE TABLE probe(id INTEGER)'))
        raw=db.engine.raw_connection();c=raw.driver_connection;c.execute('SAVEPOINT s');c.execute('INSERT INTO probe VALUES(1)')
        ready,recorded=Event(),Event()
        def trace(sql):
            if sql.startswith('RELEASE'):ready.set();assert recorded.wait(4)
        c.set_trace_callback(trace)
        with ThreadPoolExecutor(max_workers=1) as pool:
            f=pool.submit(c.execute,clause);assert ready.wait(4);fault(db);recorded.set();f.result(4)
        with sqlite3.connect(dbfile) as check:rows=check.execute('SELECT COUNT(*) FROM probe').fetchone()[0]
        result[clause]={'fault_persisted_before_actual_release':True,'committed_rows':rows};assert rows==1
        c.set_trace_callback(None);raw.close();db.close()
    dbfile=out/'backup-target.sqlite';db=Database('sqlite:///'+str(dbfile));db.migrate();raw=db.engine.raw_connection()
    with sqlite3.connect(':memory:') as source:
        source.execute('CREATE TABLE injected(value)');source.execute('INSERT INTO injected VALUES(1)');source.commit();fault(db)
        source.backup(raw.driver_connection)
    with sqlite3.connect(dbfile) as check:result['backup']={'wrote_faulted_target':check.execute('SELECT COUNT(*) FROM injected').fetchone()[0]==1}
    raw.close();db.close()
    from packages.ingestion import recipe
    original_which,original_run=recipe.shutil.which,recipe.subprocess.run
    for mode in ('disappears','probe_error'):
        tool=out/('tool-'+mode);tool.write_bytes(b'generated executable fixture')
        def failed(*a,**kw):
            if mode=='disappears':tool.unlink();raise FileNotFoundError('fixture disappeared')
            raise subprocess.CalledProcessError(1,a[0])
        recipe.shutil.which=lambda name:str(tool) if name=='ffmpeg' else None;recipe.subprocess.run=failed
        x=recipe.runtime_snapshot()['tools']['ffmpeg'];result['tool_'+mode]=x;assert x=={'status':'unavailable'}
    recipe.shutil.which,recipe.subprocess.run=original_which,original_run
    from tests.test_import_inspection import inspection_api,diagnostic_video,import_pattern,SCENE
    from tests.test_ingestion_api import import_config
    from packages.contracts.ingestion import ImportRequest
    from infra.models import ImportJob,SourceSplit
    from sqlalchemy import select
    root=out/'api';root.mkdir();config=import_config.__wrapped__(root);fixture=inspection_api.__wrapped__(config,root)
    client,app,_=next(fixture)
    try:
        video=diagnostic_video.__wrapped__(root);job=import_pattern(client,video)
        app.state.imports.stop_event.set();app.state.imports.wake_event.set();app.state.imports.thread.join()
        with app.state.database.session() as session:body=json.loads(session.get(ImportJob,job['id']).request_json)
        pending=app.state.imports.enqueue(SCENE,ImportRequest.model_validate({**body,'requested_split':'test'}))
        url=f'/api/scenes/{SCENE}/imports/{job["id"]}/media'
        before=client.get(url).status_code;app.state.imports.cancel(SCENE,pending['id']);after=client.get(url).status_code
        with app.state.database.session() as session:
            partition=session.scalar(select(SourceSplit).where(SourceSplit.source_hash==job['manifest']['source_hash'])).split
        result['pending_cancel']={'pending_status':before,'after_cancel':after,'source_split':partition}
        assert before==after==422 and partition=='unassigned'
    finally:
        try:next(fixture)
        except StopIteration:pass
    for count in (2,4):
        folder=out/('threads-'+str(count));folder.mkdir()
        try:
            subprocess.run([sys.executable,__file__,'--threads',str(count),'--output',str(folder)],timeout=8,check=True)
            state='completed'
        except subprocess.TimeoutExpired:state='timed_out'
        result['threads_'+str(count)]={'state':state,**json.loads((folder/'thread-progress.json').read_text())}
    assert result['threads_2']['state']=='timed_out' and result['threads_4']['state']=='completed'
    save(out/'backend-counterexamples.json',result);print(json.dumps(result))


if __name__=='__main__':main()
