"""Private immutable numerical state and additive experimental shadow memory."""
from pathlib import Path
import json
import numpy as np
from packages.local_motion.extraction import sha,write
from packages.knowledge.service import content_hash
from packages.config.errors import ContractError


def pack_state(directory,value,max_bytes=None):
    directory=Path(directory);arrays={};seen_arrays={}
    def pack(v):
        if isinstance(v,np.ndarray):
            if id(v) in seen_arrays:return {'kind':'ndarray','key':seen_arrays[id(v)]}
            key='array_'+str(len(arrays));arrays[key]=v;seen_arrays[id(v)]=key;return {'kind':'ndarray','key':key}
        if isinstance(v,np.generic):return v.item()
        if isinstance(v,dict):return {'kind':'mapping','items':[[k,pack(x)] for k,x in v.items()]}
        if isinstance(v,(list,tuple)):return {'kind':'list','items':[pack(x) for x in v]}
        return v
    payload=pack(value)
    if max_bytes is not None and sum(a.nbytes for a in arrays.values())>max_bytes:raise ContractError('UNLABELED_MEMORY_BUDGET','影子记忆数值超出统一内存预算')
    np.savez_compressed(directory/'model-private.npz',**arrays)
    metadata={'schema':'b2u-private-state-v1','arrays_sha256':sha(directory/'model-private.npz'),'payload':payload}
    write(directory/'model-state-private.json',{**metadata,'state_hash':content_hash(metadata)})


def load_state(directory,max_bytes=None):
    directory=Path(directory);metadata=json.loads((directory/'model-state-private.json').read_text())
    if content_hash({k:v for k,v in metadata.items() if k!='state_hash'})!=metadata.get('state_hash'):raise ContractError('UNLABELED_MODEL_CHANGED','冻结状态元数据被修改')
    if metadata['schema']!='b2u-private-state-v1' or sha(directory/'model-private.npz')!=metadata['arrays_sha256']:raise ContractError('UNLABELED_MODEL_CHANGED','私有原型状态哈希不符')
    if max_bytes is not None:
        import zipfile
        with zipfile.ZipFile(directory/'model-private.npz') as archive:
            if sum(f.file_size for f in archive.infolist())>max_bytes:raise ContractError('UNLABELED_MEMORY_BUDGET','冻结记忆展开超出预算')
    with np.load(directory/'model-private.npz',allow_pickle=False) as a:arrays={k:a[k].copy() for k in a.files}
    def unpack(v):
        if not isinstance(v,dict):return v
        if v['kind']=='ndarray':return arrays[v['key']]
        if v['kind']=='list':return [unpack(x) for x in v['items']]
        if v['kind']=='mapping':return {k:unpack(x) for k,x in v['items']}
        raise ValueError('Unsupported private state')
    return unpack(metadata['payload'])


def shadow_registry(report,parent=None):
    if parent is not None and (parent.get('scope')!='experimental_only' or parent.get('production_activated') is not False):raise ContractError('UNLABELED_PARENT_SCOPE','影子更新不能以生产库为覆盖目标')
    if parent is not None:validate_registry(parent)
    entries=list(parent['entries']) if parent else [];added=[]
    for method in report['methods']:
        if method['status']!='computed' or not method['shadow_gate']['passed']:continue
        for prototype in method['prototypes']:
            item={'method':method['method'],'prototype_id':prototype['id'],'fit_state_hash':method['fit']['state_hash'],
                'source_sha256':report['source']['sha256'],'candidate_run_id':report['run_id'],'semantic_label':None}
            if item not in entries:entries.append(item);added.append(item)
    payload={'schema':'b2u-shadow-registry-v1','scope':'experimental_only','parent_version':parent['version'] if parent else None,
        'entries':entries,'new_entries':len(added),'candidate_run_id':report['run_id'],'source_sha256':report['source']['sha256'],
        'status':'experimental_candidates_admitted' if added else 'no_new_eligible_candidates','production_activated':False,'p04_admitted':False,
        'rule':'fit/calibration gates only; replay never activates or edits earlier entries'}
    return {**payload,'version':content_hash(payload)}


def validate_registry(registry,expected=None):
    if registry.get('schema')!='b2u-shadow-registry-v1' or content_hash({k:v for k,v in registry.items() if k!='version'})!=registry.get('version'):
        raise ContractError('UNLABELED_REGISTRY_CHANGED','原型库版本回执不符')
    if expected is not None and registry['version']!=expected:raise ContractError('UNLABELED_REGISTRY_CHANGED','库与数值状态不是同一冻结版本')
    return registry
