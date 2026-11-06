"""Offline runner. Full reports and all prototype arrays remain private."""
from pathlib import Path
import json,time,subprocess
import numpy as np
from packages.config.loader import PROJECT_ROOT
from packages.local_motion.extraction import sha,write
from packages.knowledge.service import content_hash
from packages.config.errors import ContractError
from .data import load_features,cross_source_admission
from .study import fit_stream,transform
from .algorithms import match,segmentation
from .forecast import score_forecasts
from .memory import pack_state,load_state,shadow_registry,validate_registry


def code_identity():
    import importlib.metadata,platform
    paths=list((PROJECT_ROOT/'packages/unlabeled').glob('*.py'))+[PROJECT_ROOT/'packages/contracts/unlabeled.py',PROJECT_ROOT/'packages/temporal/models.py']
    return {'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT_ROOT,text=True).strip(),
        'source_sha256':{str(p.relative_to(PROJECT_ROOT)):sha(p) for p in paths},
        'dependencies':{name:importlib.metadata.version(name) for name in ['numpy','pydantic','rfc8785']},'python':platform.python_version()}


def run(bundle,output,parent_registry=None):
    output=Path(output).resolve()
    if not output.is_relative_to(PROJECT_ROOT/'artifacts'):raise ValueError('Private new artifacts directory required')
    output.mkdir(parents=True,exist_ok=False);started=time.perf_counter();identity=code_identity()
    try:
        manifest,streams,missing=load_features(bundle);s=bundle.config.unlabeled;models={};reports=[]
        for stream in streams:
            report,model=fit_stream(stream,s);reports.append(report)
            if model is not None:models[stream.name]=model
        payload={'schema':'b2u-unlabeled-study-v1','source':manifest['source'],'evidence_class':manifest['evidence_class'],
            'feature_cache_sha256':manifest['features_sha256'],'feature_recipe_hash':manifest['recipe_hash'],'model_sha256':manifest['recipe']['model_sha256'],
            'config_hash':bundle.config_hash,'settings':s.model_dump(mode='json'),'code_identity':identity,
            'methods':reports,'unavailable_methods':missing,'elapsed_seconds':time.perf_counter()-started,'annotation_required':False,
            'real_precision':None,'real_recall':None,'real_f1':None,'proxy_metrics_are_action_accuracy':False,
            'source_independence':'one original development source; chronological partitions are not independent sources',
            'formal_test_data_consumed':False,'production_activated':False,'p04_admitted':False,
            'limits':['Exact cached feature recurrence is not verified action identity.','Background ROI is contaminated/unverified.',
                'Offline segment cells differ from feature support; raw-overlap pairs are excluded from motif profiles.',
                'Cross-space MSE is not a representation accuracy ranking.','No confirmed independent field query source is fabricated.']}
        payload['run_id']=content_hash({k:v for k,v in payload.items() if k not in {'methods','elapsed_seconds'}})
        parent=None
        if parent_registry:
            parent=json.loads(Path(parent_registry).read_text())
            if parent['source_sha256']==payload['source']['sha256']:raise ContractError('UNLABELED_UPDATE_SAME_SOURCE','重复执行同一来源不是新的自学习来源；不可虚增原型记忆')
            # Independent-source checks happen in the query/update command before this branch.
            raise ContractError('UNLABELED_UPDATE_REQUIRES_FROZEN_PARENT','更新须使用显式冻结父模型协议，不能把任意新拟合结果并入旧库')
        registry=shadow_registry(payload)
        pack_state(output,{'models':models,'source':payload['source'],'evidence_class':payload['evidence_class'],'run_id':payload['run_id'],
            'settings':s.model_dump(mode='json'),'registry_version':registry['version'],
            'branches':[{'models':models,'source':payload['source'],'evidence_class':payload['evidence_class'],'run_id':payload['run_id']}],
            'code_identity':identity},bundle.config.ingestion.max_expanded_bytes)
        write(output/'report-private.json',payload);write(output/'registry-private.json',registry)
        return payload
    except BaseException as error:
        write(output/'failure.json',{'status':'failed','code':getattr(error,'code','UNLABELED_EXECUTION_FAILED'),'type':type(error).__name__,'elapsed_seconds':time.perf_counter()-started,'production_updated':False});raise


def query(parent_directory,bundle,output,update=False):
    output=Path(output).resolve()
    if not output.is_relative_to(PROJECT_ROOT/'artifacts'):raise ValueError('Private output required')
    output.mkdir(parents=True,exist_ok=False)
    try:return _query(parent_directory,bundle,output,update)
    except BaseException as error:
        write(output/'failure.json',{'status':'blocked_or_failed','code':getattr(error,'code','UNLABELED_QUERY_FAILED'),'type':type(error).__name__,'parent_modified':False,'production_updated':False});raise


def _query(parent_directory,bundle,output,update=False):
    parent_directory=Path(parent_directory);parent=load_state(parent_directory,bundle.config.ingestion.max_expanded_bytes);s=bundle.config.unlabeled
    validate_registry(json.loads((parent_directory/'registry-private.json').read_text()),parent['registry_version'])
    # Metadata/source gates BEFORE reading any query feature values.
    from packages.local_motion.extraction import admission
    if not any(b['models'] for b in parent['branches']):raise ContractError('UNLABELED_NO_PARENT_MODEL','父版本没有可用原型模型状态')
    admission(bundle)
    from packages.config.loader import resolve_path
    query_manifest=json.loads(resolve_path(bundle.root,s.feature_manifest).read_text())
    frozen_settings={k:v for k,v in parent['settings'].items() if k not in {'feature_manifest','source_evidence'}}
    query_settings={k:v for k,v in s.model_dump(mode='json').items() if k not in {'feature_manifest','source_evidence'}}
    if frozen_settings!=query_settings:raise ContractError('UNLABELED_QUERY_CONFIG_CHANGED','查询不可修改已冻结算法/闸门参数')
    if parent['code_identity']['source_sha256']!=code_identity()['source_sha256']:raise ContractError('UNLABELED_QUERY_CODE_CHANGED','冻结父模型的算法源码版本不符，不能静默重解释原型')
    if parent['code_identity']['dependencies']!=code_identity()['dependencies']:raise ContractError('UNLABELED_QUERY_DEPENDENCIES_CHANGED','研究执行依赖不同，须显式版本验证')
    for branch in parent['branches']:
        first=next(iter(branch['models'].values()),None)
        if first is None:continue
        cross_source_admission(branch['source'],query_manifest['source'],s,first['contract'],first['contract'])
        if branch['evidence_class']!=query_manifest['evidence_class']:raise ContractError('UNLABELED_ORIGIN_CHANGED','真实与合成来源不能混作同一研究证据')
    manifest,streams,missing=load_features(bundle);reports=[];by_name={v.name:v for v in streams}
    for branch in parent['branches']:
        for name,model in branch['models'].items():
            if name not in by_name:continue
            stream=by_name[name];admission=cross_source_admission(branch['source'],manifest['source'],s,stream.contract,model['contract'])
            z=transform(stream.values,model['projection']);hits,labels=match(z,stream.starts,stream.ends,stream.activity,model['prototypes'],s,model['activity_floor'])
            segments,delta,multi=segmentation(z,stream.starts,stream.ends,s,model['segment_params'])
            reports.append({'method':name,'branch_run_id':branch['run_id'],'admission':admission,'candidate_matches':hits,'candidate_coverage':float(np.mean(labels>=0)),
                'experimental_active_matches':sum(h['prototype_id'] in model['active_ids'] for h in hits),'segmentation':segments,
                'forecasts':score_forecasts(z,stream.starts,stream.ends,s,model['forecasts'],stream.activity),
                'trace':[{'pts_ms':int(t),'change':float(d),'multiscale_change':float(m),'prototype_id':None if p<0 else f'anonymous_{p:03d}'} for t,d,m,p in zip(stream.ends,delta,multi,labels)]})
    report={'schema':'b2u-cross-source-query-v1','parent_run_id':parent['run_id'],'query_source':manifest['source'],'evidence_class':manifest['evidence_class'],
        'methods':reports,'missing':missing,'query_used_for_parent_fit':False,'real_precision':None,'real_recall':None,'real_f1':None,'p04_admitted':False,'production_activated':False}
    write(output/'query-private.json',report)
    if update:
        # Add a separately versioned fit group; never move old prototype centers
        # or pretend distances in different PCA normalizations are commensurate.
        candidate_reports=[];candidate_models={}
        for stream in streams:
            r,model=fit_stream(stream,s);candidate_reports.append(r)
            if model is not None:candidate_models[stream.name]=model
        candidate_id=content_hash({'parent':parent['run_id'],'query_feature_sha':manifest['features_sha256'],'settings':s.model_dump(mode='json'),'code':code_identity()})
        candidate={'source':manifest['source'],'run_id':candidate_id,'methods':candidate_reports}
        old=json.loads((parent_directory/'registry-private.json').read_text());registry=shadow_registry(candidate,old)
        branches=[*parent['branches'],{'models':candidate_models,'source':manifest['source'],'evidence_class':manifest['evidence_class'],'run_id':candidate_id}]
        pack_state(output,{'models':candidate_models,'branches':branches,'source':manifest['source'],'evidence_class':manifest['evidence_class'],
            'run_id':candidate_id,'settings':s.model_dump(mode='json'),'registry_version':registry['version'],'code_identity':code_identity()},bundle.config.ingestion.max_expanded_bytes)
        write(output/'new-candidate-study-private.json',candidate);write(output/'registry-private.json',registry)
        report['shadow_update']={'version':registry['version'],'new_entries':registry['new_entries'],'old_entries_unchanged':True,'separate_fit_groups':len(branches),'production_updated':False}
        write(output/'query-private.json',report)
    return report
