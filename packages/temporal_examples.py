"""Independent synthetic sources through the P0.2 import path, never real evidence."""
from pathlib import Path
import hashlib
import json
import numpy as np
from packages.config.errors import ContractError
from packages.contracts.ingestion import ImportRequest
from packages.ingestion_examples import build_example
from packages.knowledge.service import content_hash


def enqueue_examples(bundle,imports,scene,directory):
    if bundle.profile.mode!='demo':raise ContractError('EXAMPLE_DEMO_ONLY','研究示例只用于demo')
    groups={'train':4,'calibration':2,'test':2};jobs={s:[] for s in groups};index=0
    for split,count in groups.items():
        for _ in range(count):
            index+=1;example=build_example(bundle,scene,Path(directory)/str(index))
            dimension=bundle.config.encoder.feature_dim
            recipe={'version':'temporal-independent-oscillators-v1','dimension':dimension,'basis_seed':17,'rows':120,
                    'notice':'Synthetic numerical sources; no video encoder or semantic labels.'}
            example['encoder_path'].write_text(json.dumps(recipe,sort_keys=True)+'\n')
            rng=np.random.default_rng(1000+index);time=np.arange(120,dtype=np.float64)/4
            frequency=rng.uniform(.65,1.0,3);phase=rng.uniform(-np.pi,np.pi,3)
            latent=np.concatenate([np.sin(time[:,None]*frequency+phase),np.cos(time[:,None]*frequency+phase)],axis=1)
            basis=np.random.default_rng(17).normal(size=(6,dimension))/np.sqrt(6)
            vectors=(latent@basis+rng.normal(0,.015,size=(120,dimension))).astype(np.float32)
            np.savez_compressed(example['input_path'],features=vectors,pts_ms=np.arange(120,dtype=np.int64)*1000,
                                durations_ms=np.full(120,1000,dtype=np.int64))
            fields=example['request_fields'];space=fields['feature_space']
            space.update(encoder_sha=hashlib.sha256(example['encoder_path'].read_bytes()).hexdigest(),
                         preprocessing_sha=content_hash(recipe),pooling='synthetic_linear_basis')
            fields.update(requested_split=split,declared_source_hash=content_hash({'recipe':recipe,'source_seed':1000+index}))
            source=imports.register_file(example['input_path'],'npz');encoder=imports.register_file(example['encoder_path'],'encoder')
            request=ImportRequest.model_validate(fields|{'upload_id':source['id'],'encoder_artifact_id':encoder['id']})
            jobs[split].append(imports.enqueue(scene,request)['id'])
    return {'data_origin':'synthetic_features','groups':jobs,'train_import_ids':jobs['train']+jobs['calibration'],
            'test_import_ids':jobs['test'],'notice':'独立合成源，仅验证机制；等待导入完成后训练，test需另外发起评估。'}
