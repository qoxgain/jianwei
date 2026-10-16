"""Offline structural validation. --preflight checks local prerequisites only."""
import argparse
import json
import os
from pathlib import Path
import yaml
from contracts import SystemConfig, Knowledge

class UniqueLoader(yaml.SafeLoader):
    pass

def unique_mapping(loader,node,deep=False):
    result={}
    for k,v in node.value:
        key=loader.construct_object(k,deep=deep)
        if key in result: raise ValueError(f'重复YAML键: {key}')
        result[key]=loader.construct_object(v,deep=deep)
    return result

UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,unique_mapping)

def read_yaml(path):
    with open(path,encoding='utf-8') as f: return yaml.load(f,Loader=UniqueLoader)

def resolve_path(root,value):
    p=Path(value).expanduser()
    return p.resolve() if p.is_absolute() else (root/p).resolve()

def load_bundle(config_path,profile=None):
    path=Path(config_path).resolve();root=path.parent.parent
    raw=read_yaml(path)
    if profile is not None: raw['active_profile']=profile
    cfg=SystemConfig.model_validate(raw)
    kn=Knowledge.model_validate(read_yaml(resolve_path(root,cfg.knowledge_file)))
    if kn.scene_id!=cfg.active_scene: raise ValueError('标签文件scene与配置不一致')
    for name,value in cfg.paths.model_dump().items():
        if Path(value).is_absolute() or '..' in Path(value).parts:
            raise ValueError(f'paths.{name}必须是data_root中的相对目录')
    return cfg,kn,root

def preflight(cfg,kn,root):
    p=cfg.profiles[cfg.active_profile];errors=[]
    if p.mode=='demo': return errors
    if kn.approval_status!='approved': errors.append('KNOWLEDGE_DRAFT: 需要场景规范的正式版本')
    for key,value in p.model_dump().items():
        if isinstance(value,str) and value.startswith('env://'):
            required=key=='database_url' or (key=='jwt_secret_ref' and p.auth_enabled) or (key=='teacher_api_key_ref' and p.teacher_enabled)
            if required and not os.getenv(value[6:]): errors.append(f'ENV_MISSING: {value[6:]}')
    if not resolve_path(root,p.encoder_weights).is_file(): errors.append('ENCODER_WEIGHTS_MISSING')
    if not cfg.encoder.weights_sha256: errors.append('ENCODER_SHA_NOT_PINNED')
    for s in p.sources:
        if s.type=='file' and not resolve_path(root,s.uri).is_file(): errors.append(f'INPUT_FILE_MISSING: {s.id}')
        if s.uri.startswith('env://') and not os.getenv(s.uri[6:]): errors.append(f'ENV_MISSING: {s.uri[6:]}')
    return errors

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--config',required=True);ap.add_argument('--profile');ap.add_argument('--preflight',action='store_true');args=ap.parse_args()
    try:
        cfg,kn,root=load_bundle(args.config,args.profile)
        errors=preflight(cfg,kn,root) if args.preflight else []
        print(json.dumps({'status':'FAIL' if errors else 'PASS','profile':cfg.active_profile,'mode':cfg.profiles[cfg.active_profile].mode,'knowledge_version':kn.knowledge_version,'check':'local_preflight' if args.preflight else 'static_only','errors':errors,'note':'未测试网络、摄像头、GPU或真实模型；静态通过不等于部署就绪。'},ensure_ascii=False,indent=2))
        return bool(errors)
    except Exception as e:
        # Avoid exposing validation input values which may contain credentials.
        print(json.dumps({'status':'FAIL','error_type':type(e).__name__,'note':'检查schema、必填字段、引用或YAML重复键；错误值不输出以免泄露凭证。'},ensure_ascii=False))
        return 1

if __name__=='__main__': raise SystemExit(main())
