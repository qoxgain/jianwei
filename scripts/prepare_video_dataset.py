"""Isolated engineering or research sampling configuration; never edits main parameters."""
import json, os, shutil
from pathlib import Path
import yaml
from packages.config.loader import PROJECT_ROOT, load_bundle
from packages.ingestion.pipeline import file_hash


def prepare(output, *, research=False, encoder_report=None):
    output=output.resolve()
    config=yaml.safe_load((PROJECT_ROOT/'config/system.yaml').read_text())
    model=PROJECT_ROOT/'artifacts/models/r3d18-kinetics400-v1.onnx'
    from packages.encoders.pretrained import CHECKPOINT_SHA256, PREPROCESSING_VERSION
    from packages.contracts.configuration import SceneConfig
    from packages.temporal_jobs.sampling import grid_plan
    evidence=json.loads((encoder_report or PROJECT_ROOT/'docs/delivery/p0.3.1/encoder/export.json').read_text())
    if (evidence.get('passed') is not True or evidence.get('checkpoint_sha256')!=CHECKPOINT_SHA256
            or evidence.get('onnx_sha256')!=file_hash(model)
            or evidence.get('preprocessing_version')!=PREPROCESSING_VERSION
            or evidence.get('frozen') is not True or evidence.get('training') is not False):
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
    # Retain the original engineering profile by default. Research explicitly
    # retains system.yaml's sampling AND prediction policy, without tuning on data.
    if not research:
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
    parsed=SceneConfig.model_validate(config)
    if research and not grid_plan(parsed.video,parsed.learning,parsed.temporal)['compatible_grid']:
        raise ValueError('Research sampling grid is incompatible with the configured prediction policy')
    if path.exists() and yaml.safe_load(path.read_text())!=config:
        raise ValueError('Refusing to overwrite changed acceptance config')
    copies=[]
    for source in (PROJECT_ROOT/'knowledge').rglob('*'):
        if source.is_file():
            target=output/'knowledge'/source.relative_to(PROJECT_ROOT/'knowledge')
            if target.exists() and target.read_bytes()!=source.read_bytes():
                raise ValueError('Refusing to overwrite changed knowledge')
            copies.append((source,target))
    # Check all existing files before writing any of them.
    for source,target in copies:
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
    load_bundle(path);return path


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=PROJECT_ROOT/'artifacts/p033-runtime')
    parser.add_argument('--research',action='store_true',help='Use system.yaml sampling and forecast policy; no automatic tuning')
    parser.add_argument('--encoder-report',type=Path,help='Passed export evidence matching the local frozen ONNX bytes')
    args=parser.parse_args()
    print(prepare(args.output_dir,research=args.research,encoder_report=args.encoder_report))
