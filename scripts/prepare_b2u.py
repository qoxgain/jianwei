"""Create isolated system.yaml and (optionally) independent generated sources."""
import argparse,json,os,shutil,subprocess
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import yaml
from packages.config.loader import PROJECT_ROOT,load_bundle
from packages.local_motion.extraction import sha,write,OFFICIAL_SHA,frozen_encoder_sha
from packages.encoders.pretrained import PREPROCESSING_VERSION
from scripts.prepare_b1 import settings as roi_settings


def research_settings(feature_manifest):
    return {'enabled':True,'feature_manifest':feature_manifest,'window_ms':2000,'sample_fps':8,
        'fit_fraction':.5,'calibration_fraction':.25,'purge_ms':2000,'projection_dim':8,'change_quantile':.8,
        'scales':[1,2,4],'min_segment_windows':2,'max_segment_windows':20,'fixed_segment_windows':4,'dp_penalty_multiplier':1.,
        'motif_lengths':[2,4,6],'min_repeats':3,'max_prototypes':8,'motif_neighbor_quantile':.35,'motif_radius_factor':1.2,
        'min_shape_rms':.15,'context_weight':.25,'activity_quantile':.25,'max_profile_windows':512,
        'seeds':[11,23,37,53,71],'noise_sigma':.05,'background_mix':.3,'boundary_tolerance_ms':1000,
        'forecast_history':6,'forecast_lags':[2,4],'ridge_lambda':1.,'retrieval_k':3,
        'min_gate_stability':.65,'min_gate_null_margin':.02,'min_gate_prediction_gain':0.,'max_gate_coverage':.9,
        'source_evidence':[],'formal_research_access':False,'production_updates':False}


def generated_video(path,seed):
    width,height,fps,count=160,120,16,1024;rng=np.random.default_rng(seed)
    base=np.clip(90+rng.integers(-8,9,(height,width,3)),0,255).astype(np.uint8)
    command=['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}','-r',str(fps),'-i','pipe:0',
        '-an','-c:v','libx264','-threads','1','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-y',str(path)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for index in range(count):
            phase=index%128;image=base.copy()
            if phase<32:x=24
            elif phase<64:x=24+(phase-32)
            elif phase<96:x=56-(phase-64)
            else:x=24
            y=32 if (index//128)%2==0 else 48
            image[y:y+25,x:x+20]=[215+(seed%7),55,30];image[y+2:y+23:4,x:x+20:4]=240
            if index%256>=192:image[:,120:]=50+(index%4)*25
            process.stdin.write(image.tobytes())
        process.stdin.close();process.stdin=None;_,error=process.communicate(timeout=30)
        if process.returncode:raise RuntimeError(error.decode()[:1000])
    finally:
        if process.poll() is None:process.kill();process.wait()
    record={'kind':'b2u-generated-motifs-v1','sha256':sha(path),'seed':seed,'frames':count,'fps':fps,'width':width,'height':height,
        'evidence_class':'synthetic_engineering','input_source_frames_read':0,'generator_sha256':sha(Path(__file__)),
        'generated_at':datetime.now(timezone.utc).isoformat(),'definition':'fresh seeded raster source; shared planted dynamics, not independent field footage'}
    write(path.with_suffix('.generated.json'),record);return record


def runtime(output,manifest,source,local_settings,evidence,model):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    cfg=yaml.safe_load((PROJECT_ROOT/'config/system.yaml').read_text());cfg['active_profile']='demo';cfg['active_scene']='anonymous_observation'
    p=cfg['profiles']['demo'];p['data_root']='./data';p['database_url']='sqlite:///./data/unused-b2u.db';p['encoder_weights']=os.path.relpath(model,output)
    p['sources']=[{'id':local_settings['source_id'],'type':'file','uri':os.path.relpath(source,output),'scene_ids':['anonymous_observation'],'roi_xyxy':[0,0,1,1]}]
    expected=frozen_encoder_sha(sha(model),local_settings['evidence_class']) if local_settings['evidence_class']=='synthetic_engineering' else OFFICIAL_SHA
    cfg['encoder'].update(adapter='onnx',feature_dim=512,weights_sha256=expected,preprocessing_version=PREPROCESSING_VERSION,checkpoint_revision='torchvision-v0.22.1-R3D_18_Weights.KINETICS400_V1')
    cfg['ingestion'].update(decode_width=local_settings['display_width'],encoder_input_size=112,encoder_threads=2,encode_timeout_seconds=7200,decode_timeout_seconds=600)
    cfg['local_motion']=local_settings;cfg['unlabeled']=research_settings(os.path.relpath(manifest,output));cfg['unlabeled']['source_evidence']=evidence
    shutil.copytree(PROJECT_ROOT/'knowledge',output/'knowledge');path=output/'config/system.yaml';path.parent.mkdir();path.write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False));load_bundle(path);return path


def prepare_real(output,manifest,model):
    m=json.loads(Path(manifest).read_text());catalog=json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())
    asset=next(v for v in catalog['assets'] if v['kind']=='video' and v['sha256']==m['source']['sha256'])
    evidence=[{'source_sha256':asset['sha256'],'root_source_sha256':asset['sha256'],'kind':'field','independence_review':'pending','near_duplicate_review':'pending',
        'capture_session_id':None,'view_id':None,'captured_at':None}]
    return runtime(output,Path(manifest).resolve(),PROJECT_ROOT/asset['path'],m['settings'],evidence,model)


def prepare_pair(output,model):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False);sources=[]
    for i,seed in enumerate([101,202]):
        video=output/f'generated-{i}.mp4';record=generated_video(video,seed);sources.append((video,record))
    evidence=[{'source_sha256':r['sha256'],'root_source_sha256':r['sha256'],'kind':'generated','independence_review':'verified','near_duplicate_review':'clear',
        'capture_session_id':'generated_seed_'+str(r['seed']),'view_id':'generated_fixed_raster_v1','captured_at':r['generated_at']} for _,r in sources]
    paths=[]
    for i,(video,r) in enumerate(sources):
        lm=roi_settings('b2u_generated_'+str(i),r['sha256'],160,120,64000,True);lm.update(window_ms=[2000],sample_fps=[8],stride_ms=1000,baseline_fraction=.25)
        for roi in lm['rois']:roi['encode_r3d']=False
        paths.append(runtime(output/f'runtime-{i}',output/f'features-{i}'/'manifest.json',video,lm,evidence,model))
    return paths


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--features',type=Path)
    p.add_argument('--synthetic-pair',action='store_true');p.add_argument('--model',type=Path,default=PROJECT_ROOT/'artifacts/p036/models/r3d18-kinetics400-v1.onnx');a=p.parse_args()
    if not a.output.resolve().is_relative_to(PROJECT_ROOT/'artifacts'):raise ValueError('Private artifacts runtime required')
    if a.synthetic_pair:print('\n'.join(str(v) for v in prepare_pair(a.output,a.model.resolve())))
    elif a.features:print(prepare_real(a.output,a.features,a.model.resolve()))
    else:p.error('Use --features or --synthetic-pair')


if __name__=='__main__':main()
