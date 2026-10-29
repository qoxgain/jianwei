"""Explicit, isolated P0.3.3 acceptance configuration; never edits main parameters."""
import json, os, shutil
from pathlib import Path
import yaml
from packages.config.loader import PROJECT_ROOT, load_bundle
from packages.ingestion.pipeline import file_hash


def prepare(output):
    output=output.resolve();(output/'config').mkdir(parents=True,exist_ok=True)
    shutil.copytree(PROJECT_ROOT/'knowledge',output/'knowledge',dirs_exist_ok=True)
    config=yaml.safe_load((PROJECT_ROOT/'config/system.yaml').read_text())
    model=PROJECT_ROOT/'artifacts/models/r3d18-kinetics400-v1.onnx'
    from packages.encoders.pretrained import CHECKPOINT_SHA256
    evidence=json.loads((PROJECT_ROOT/'docs/delivery/p0.3.1/encoder/export.json').read_text())
    if not evidence['passed'] or evidence['checkpoint_sha256']!=CHECKPOINT_SHA256 or evidence['onnx_sha256']!=file_hash(model):
        raise ValueError('Official frozen encoder does not match the verified export evidence')
    config['sop_review']['asset_root']=os.path.relpath(PROJECT_ROOT,output)
    config['profiles']['demo']['encoder_weights']=os.path.relpath(model,output)
    config['profiles']['demo']['api_port']=18053
    config['encoder'].update(adapter='onnx',feature_dim=512,pooling='global_avg_pool',
        weights_sha256=file_hash(model),preprocessing_version='torchvision-r3d18-kinetics400-v1',
        checkpoint_revision='torchvision-v0.22.1-R3D_18_Weights.KINETICS400_V1')
    config['ingestion'].update(max_upload_bytes=536870912,decode_width=640,encoder_input_size=112,
        probe_timeout_seconds=300,decode_timeout_seconds=600,encode_timeout_seconds=3600,
        upload_timeout_seconds=900,max_staging_bytes=2147483648,encoder_threads=2)
    # Explicit nonoverlapping 2-second clips for CPU acceptance. Same 16-frame/112 model input.
    config['video'].update(clip_seconds=2,stride_seconds=2,frames_per_clip=16)
    config['scenes']['anonymous_observation']['episode_policy'].update(
        observation_contract='p033-observation-v1',partial_window='extract_only')
    catalog=json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())
    sources=config['profiles']['demo']['sources']
    for asset in catalog['assets']:
        if asset['kind']!='video':continue
        sources.append({'id':asset['asset_id'],'type':'file','uri':os.path.relpath(PROJECT_ROOT/asset['path'],output),
            'scene_ids':['anonymous_observation'],'roi_xyxy':[0,0,1,1],
            'capture_identity':{'physical_camera_id':None,'stable_view_id':None,'review_status':'pending'}})
    path=output/'config/system.yaml'
    if path.exists() and yaml.safe_load(path.read_text())!=config:
        raise ValueError('Refusing to overwrite changed acceptance config')
    path.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
    load_bundle(path);return path


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=PROJECT_ROOT/'artifacts/p033-runtime')
    print(prepare(parser.parse_args().output_dir))
