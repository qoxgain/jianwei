"""A.1 five-state acceptance service: generated patterns, two I/O tokens, new private DB."""
import argparse,asyncio,json,subprocess
from contextlib import asynccontextmanager
from pathlib import Path
import anyio,yaml
from fastapi.responses import HTMLResponse
from apps.api.main import create_app
from packages.config.loader import PROJECT_ROOT
from packages.contracts.ingestion import ImportRequest
from infra.models import ImportJob
from scripts.serve_p037_a_demo import prepare


def create_harness(root,port):
 path=prepare(root,port)
 for name,color in [('pending','45'),('failed','135'),('cancelled','225')]:
  subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=64x48:rate=16:duration=4',
   '-vf','hue=h='+color,'-c:v','libx264','-threads','1',str(root/(name+'.mp4'))],check=True,timeout=30)
 raw=yaml.safe_load(path.read_text());raw['media_access']={'wait_timeout_seconds':5,'sealing_wait_seconds':30,'poll_seconds':.01};path.write_text(yaml.safe_dump(raw,allow_unicode=True,sort_keys=False))
 app=create_app(path);original=app.router.lifespan_context
 @asynccontextmanager
 async def lifespan(application):
  async with original(application):
   limiter=anyio.to_thread.current_default_thread_limiter();previous=limiter.total_tokens;limiter.total_tokens=2
   service=application.state.imports;entries=[];requests={}
   async def wait(job):
    for _ in range(1000):
     state=service.get('anonymous_observation',job['id'])
     if state['status'] not in ('queued','running'):return state
     await asyncio.sleep(.03)
    raise RuntimeError('Generated fixture did not finish')
   for name in ('development','sealed','pending','failed','cancelled'):
    artifact=service.register_file(root/(name+'.mp4'),'mp4')
    body={'upload_id':artifact['id'],'format':'mp4','source_id':'file_01','data_origin':'real_video',
     'requested_split':'test' if name=='sealed' else 'unassigned','episode_origin':'manual',
     'episodes':[{'episode_id':'a1-generated','domain_index':0,'start_ms':0,'end_ms':4000}]}
    requests[name]=body
    state=await wait(service.enqueue('anonymous_observation',ImportRequest.model_validate(body)))
    if state['status']!='succeeded':raise RuntimeError('Generated fixture failed: '+str(state.get('error')))
    entries.append({'name':name,'base':'/api/scenes/anonymous_observation/imports/'+state['id']})
   failed={**requests['failed'],'requested_split':'test','episodes':[{'episode_id':'invalid-generated-interval','domain_index':0,'start_ms':5000,'end_ms':6000}]}
   failed_state=await wait(service.enqueue('anonymous_observation',ImportRequest.model_validate(failed)))
   if failed_state['status'] not in ('failed','blocked'):raise RuntimeError('Failure fixture did not fail')
   # Pause the fixture worker so pending can be inspected without a scheduling race.
   service.close()
   pending=service.enqueue('anonymous_observation',ImportRequest.model_validate({**requests['pending'],'requested_split':'test'}))
   cancelled=service.enqueue('anonymous_observation',ImportRequest.model_validate({**requests['cancelled'],'requested_split':'test'}))
   service.cancel('anonymous_observation',cancelled['id'])
   application.state.a1_demo={'scope':'generated engineering fixtures only; worker paused for pending-state inspection',
    'io_pool_tokens':limiter.total_tokens,'entries':entries,'pending_job_id':pending['id'],'stage_b_started':False}
   try:yield
   finally:limiter.total_tokens=previous
 app.router.lifespan_context=lifespan
 @app.get('/stage-a1-check',response_class=HTMLResponse,include_in_schema=False)
 def page():return (PROJECT_ROOT/'demo/stage-a1-live.html').read_text().replace('__FIXTURES__',json.dumps(app.state.a1_demo))
 app.router.routes.insert(0,app.router.routes.pop());return app


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--port',type=int,default=18738)
 args=p.parse_args();app=create_harness(args.root.resolve(),args.port)
 import uvicorn
 uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')


if __name__=='__main__':main()
