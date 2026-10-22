"""Explicit v3.0 export; never overwrite the input or an existing output."""
import argparse
import json
from pathlib import Path
import yaml
from packages.config.loader import load_bundle, resolve_path
from packages.contracts.models import Knowledge, SystemConfig
from packages.migrations.legacy_v3 import adapt_config, adapt_knowledge
from packages.knowledge.service import content_hash

def export(config_path, output_dir):
    bundle = load_bundle(config_path)
    if not isinstance(bundle.knowledge, Knowledge) or bundle.config.config_version != '3.0':
        raise ValueError('输入必须是严格校验通过的v3.0配置与知识')
    output = Path(output_dir).resolve()
    knowledge, nodes = adapt_knowledge(bundle.knowledge)
    normalized = adapt_config(bundle.config).model_dump(exclude={'knowledge_file','session'})
    scene_id = bundle.config.active_scene
    normalized['scenes'][scene_id]['knowledge_file'] = f'knowledge/scenes/{scene_id}.yaml'
    # Preserve the physical meaning of original relative resource paths.
    for profile in normalized['profiles'].values():
        for name in ('data_root','model_root','encoder_weights'):
            profile[name] = str(resolve_path(bundle.root,profile[name]))
        if profile['database_url'].startswith('sqlite:///'):
            name = profile['database_url'][10:]
            if name != ':memory:': profile['database_url'] = 'sqlite:///' + str(resolve_path(bundle.root,name))
        for source in profile['sources']:
            if source['type'] == 'file' and not source['uri'].startswith('env://'):
                source['uri'] = str(resolve_path(bundle.root,source['uri']))
    outputs = {
        'config/system.yaml': yaml.safe_dump(normalized,allow_unicode=True,sort_keys=False),
        f'knowledge/scenes/{scene_id}.yaml': yaml.safe_dump(knowledge.model_dump(),allow_unicode=True,sort_keys=False),
        'migration-report.json': json.dumps({'source_config_hash':bundle.config_hash,
            'old_knowledge_hash':content_hash(bundle.knowledge.model_dump()),
            'normalized_knowledge_hash':content_hash(knowledge.model_dump()),'node_map':nodes,
            'scene_id_preserved':scene_id,'original_files_modified':False},ensure_ascii=False,indent=2)+'\n'}
    for path,value in outputs.items():
        target = output/path
        if target.exists() and target.read_text()!=value:
            raise ValueError(f'输出已存在且内容不同，未覆盖：{path}')
        if target in (bundle.path,resolve_path(bundle.root,bundle.config.knowledge_file)):
            raise ValueError('禁止覆盖旧版输入')
    for path,value in outputs.items():
        target=output/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value)
    load_bundle(output/'config/system.yaml')
    return json.loads(outputs['migration-report.json'])

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True);parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    print(json.dumps(export(args.config,args.output_dir),ensure_ascii=False,indent=2))
