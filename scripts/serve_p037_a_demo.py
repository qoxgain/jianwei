"""Isolated Stage-A user acceptance harness. Generated patterns only, never field data."""
import argparse,asyncio,hashlib,json,shutil,subprocess
from contextlib import asynccontextmanager
from pathlib import Path
import yaml
from packages.config.loader import PROJECT_ROOT
from packages.contracts.ingestion import ImportRequest


def prepare(root,port):
    if not root.resolve().is_relative_to(PROJECT_ROOT/'artifacts'):raise ValueError('Use a NEW private artifacts directory')
    root.mkdir(parents=True,exist_ok=False);(root/'config').mkdir();shutil.copytree(PROJECT_ROOT/'knowledge',root/'knowledge')
    import onnx
    h,t=onnx.helper,onnx.TensorProto
    graph=h.make_graph([h.make_node('ReduceMean',['clips'],['features'],axes=[1,3,4],keepdims=0)],
        'analytical_generated_fixture_not_pretrained',[h.make_tensor_value_info('clips',t.FLOAT,['N','T',3,16,16])],
        [h.make_tensor_value_info('features',t.FLOAT,['N',3])])
    model=h.make_model(graph,opset_imports=[h.make_opsetid('',13)]);model.ir_version=10
    encoder=root/'analytical-fixture.onnx';encoder.write_bytes(model.SerializeToString())
    config=yaml.safe_load((PROJECT_ROOT/'config/system.yaml').read_text())
    config['encoder'].update(adapter='onnx',feature_dim=3,weights_sha256=hashlib.sha256(encoder.read_bytes()).hexdigest())
    config['profiles']['demo'].update(api_host='127.0.0.1',api_port=port,public_base_url=f'http://127.0.0.1:{port}',encoder_weights=str(encoder))
    config['ingestion']['encoder_input_size']=16;config['sop_review']['preview_enabled']=True
    config['temporal']['dataset_manifest']=None;config['temporal']['research_protocol']=None
    config['exploratory']['enabled']=False
    path=root/'config/system.yaml';path.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
    for name,color in [('development','0'),('sealed','90')]:
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=64x48:rate=16:duration=4',
            '-vf','hue=h='+color,'-c:v','libx264','-threads','1',str(root/(name+'.mp4'))],check=True,timeout=30)
    return path


def create_harness(root,port):
    from apps.api.main import create_app
    from fastapi.responses import HTMLResponse
    path=prepare(root,port);app=create_app(path);original=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            ids={}
            for name,split in [('development','unassigned'),('sealed','test')]:
                artifact=application.state.imports.register_file(root/(name+'.mp4'),'mp4')
                job=application.state.imports.enqueue('anonymous_observation',ImportRequest(upload_id=artifact['id'],
                    format='mp4',source_id='file_01',data_origin='real_video',requested_split=split,episode_origin='manual',
                    episodes=[{'episode_id':'generated_fixture','domain_index':0,'start_ms':0,'end_ms':4000}]))
                for _ in range(600):
                    state=application.state.imports.get('anonymous_observation',job['id'])
                    if state['status'] not in ('queued','running'):break
                    await asyncio.sleep(.05)
                if state['status']!='succeeded':raise RuntimeError('Generated acceptance fixture failed: '+str(state.get('error')))
                ids[name]='/api/scenes/anonymous_observation/imports/'+job['id']
            application.state.stage_a_fixtures=ids
            yield
    app.router.lifespan_context=lifespan
    @app.get('/stage-a-check',response_class=HTMLResponse,include_in_schema=False)
    def page():
        html=(PROJECT_ROOT/'demo/stage-a-live.html').read_text()
        return html.replace('__FIXTURES__',json.dumps(app.state.stage_a_fixtures))
    app.router.routes.insert(0,app.router.routes.pop())
    return app


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--port',type=int,default=18737)
    args=p.parse_args();app=create_harness(args.root.resolve(),args.port)
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')


if __name__=='__main__':main()
