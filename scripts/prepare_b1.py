"""Prepare an isolated system.yaml; never alters root configuration or history."""
import argparse,os,json,shutil,subprocess
from pathlib import Path
import numpy as np
import yaml
from packages.config.loader import PROJECT_ROOT,load_bundle
from packages.local_motion.extraction import sha,write,OFFICIAL_SHA
from packages.encoders.pretrained import PREPROCESSING_VERSION


def settings(source,source_sha,width,height,end,synthetic=False):
    rois=[{'id':'global','role':'global','xyxy':[0,0,1,1]},
        {'id':'hands','role':'hands','xyxy':[.12,.02,.72,.63]},
        {'id':'interaction','role':'interaction','xyxy':[.22,.25,.78,.84]},
        {'id':'background','role':'background','xyxy':[.78,.64,.98,.94]}]
    if synthetic:
        rois[1]['xyxy']=[.08,.15,.5,.7];rois[2]['xyxy']=[.1,.1,.65,.85];rois[3]['xyxy']=[.75,.1,.95,.9]
    return {'enabled':True,'evidence_class':'synthetic_engineering' if synthetic else 'development_exploration',
        'source_id':source,'source_sha256':source_sha,'display_width':width,'display_height':height,'start_ms':0,'end_ms':end,
        'rois':rois,'window_ms':[1000,2000],'sample_fps':[4,8],'stride_ms':500,'max_pts_error_ms':50,
        'min_valid_fraction':.95,'max_windows':1000,'motion_size':32,'block_size':8,'search_radius':3,
        'texture_floor':.015,'motion_improvement':.05,'baseline_fraction':.2,'thresholds':[1.5,2.5,3.5],
        'smoothing_ms':[0,500,1000],'primary_window_ms':2000,'primary_fps':8,'primary_threshold':2.5,
        'primary_smoothing_ms':500,'refractory_ms':1000,'candidate_budget':50,'boundary_tolerance_ms':500}


def synthetic_video(path):
    """Actual encoded moving blocks, including a background nuisance interval."""
    width,height,fps,count=160,120,16,192
    command=['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}','-r',str(fps),'-i','pipe:0',
        '-an','-c:v','libx264','-threads','1','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-y',str(path)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for i in range(count):
            image=np.full((height,width,3),100,dtype=np.uint8)
            # Textured moving object: active/still/active/still, actual observations.
            active=(2<=i/fps<5) or (7<=i/fps<10)
            x=20+(i%32 if active else 8);image[32:62,x:x+20]=[220,55,30]
            image[34:60:4,x:x+20:4]=240
            if 5.5<=i/fps<6.5:image[:,120:]=60+(i%4)*35
            process.stdin.write(image.tobytes())
        process.stdin.close();error=process.stderr.read();assert process.wait(20)==0,error.decode()
    finally:
        if process.poll() is None:process.kill();process.wait()
    write(path.with_suffix('.generated.json'),{'kind':'b1-generated-rectangles-v1','sha256':sha(path),
        'width':width,'height':height,'fps':fps,'frames':count,'evidence_class':'synthetic_engineering'})
    return width,height,count*1000//fps


def prepare(output,model,encoder_report,synthetic=False):
    output=Path(output).resolve();model=Path(model).resolve()
    if not output.is_relative_to(PROJECT_ROOT/'artifacts'):raise ValueError('Private new artifacts directory required')
    output.mkdir(parents=True,exist_ok=False)
    record=json.loads(Path(encoder_report).read_text())
    if not record.get('passed') or record.get('onnx_sha256')!=OFFICIAL_SHA or sha(model)!=OFFICIAL_SHA:raise ValueError('Official frozen encoder/export receipt required')
    config=yaml.safe_load((PROJECT_ROOT/'config/system.yaml').read_text());config['active_profile']='demo';config['active_scene']='anonymous_observation'
    profile=config['profiles']['demo'];profile['data_root']='./data';profile['database_url']='sqlite:///./data/b1-unused.db';profile['api_port']=18071
    profile['encoder_weights']=os.path.relpath(model,output)
    if synthetic:
        source=output/'generated.mp4';width,height,end=synthetic_video(source);source_id='b1_generated'
    else:
        asset=next(a for a in json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())['assets'] if a['asset_id']=='garment_test')
        source=PROJECT_ROOT/asset['path'];source_id=asset['asset_id'];width,height,end=640,480,118500
    profile['sources']=[{'id':source_id,'type':'file','uri':os.path.relpath(source,output),'scene_ids':['anonymous_observation'],'roi_xyxy':[0,0,1,1]}]
    config['encoder'].update(adapter='onnx',feature_dim=512,weights_sha256=OFFICIAL_SHA,preprocessing_version=PREPROCESSING_VERSION,
        checkpoint_revision='torchvision-v0.22.1-R3D_18_Weights.KINETICS400_V1')
    config['ingestion'].update(decode_width=640 if not synthetic else 160,encoder_input_size=112,encoder_threads=2,
        encode_timeout_seconds=7200,probe_timeout_seconds=300,decode_timeout_seconds=600)
    config['local_motion']=settings(source_id,sha(source),width,height,end,synthetic)
    path=output/'config/system.yaml';path.parent.mkdir();shutil.copytree(PROJECT_ROOT/'knowledge',output/'knowledge')
    path.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False));load_bundle(path);return path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--model',type=Path,required=True);p.add_argument('--encoder-report',type=Path,required=True)
    p.add_argument('--synthetic',action='store_true');a=p.parse_args();print(prepare(a.output,a.model,a.encoder_report,a.synthetic))


if __name__=='__main__':main()
