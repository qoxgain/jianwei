"""Fabricated registries exercise preregistration guards, not real learning evidence.

These fixtures contain no recordings or claimed field measurements. They validate
that a configured study cannot silently change sources or reuse sealed data.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func
import yaml

from infra.database import Database
from infra.models import AuditEvent, Base, TemporalRun, ImportJob
from packages.config.errors import ContractError
from packages.config.loader import load_bundle
from packages.contracts.dataset import DatasetManifest
from packages.contracts.research import ResearchProtocol
from packages.contracts.temporal import TrainRequest, EvaluationRequest, ReadinessRequest
from packages.ingest_jobs.service import ImportService
from packages.knowledge.service import content_hash
from packages.temporal import engine
from packages.temporal.data import snapshot_imports
from packages.temporal.protocol import freeze_protocol, preview_protocol, assert_frozen_protocol, _bindings
from packages.contracts.capture import CaptureIdentity
from packages.contracts.learning import FeatureSpace
from packages.ingestion.capture import roi_identity
from packages.encoders import pretrained
from packages.temporal_jobs.service import TemporalService
from tests.test_dataset_provenance import source, dataset, insert_sources

SCENE='anonymous_observation'
ROOT=Path(__file__).resolve().parents[1]


def protocol_fields(**overrides):
    return {'protocol_id':'fixture_preregistered_study','registered_by':'fixture_reviewer',
        'registered_at':'2026-10-01T10:00:00+00:00','primary_metric':'source_macro_mse',
        'min_independent_sessions':2,'confidence_level':.95,'bootstrap_replicates':200,
        'bootstrap_seed':23,'minimum_relative_improvement':.05,
        'controls':['persistence','shuffled_ridge'],**overrides}


def test_protocol_roundtrips_json_preserving_aware_time_and_predeclared_controls():
    registered=ResearchProtocol.model_validate(protocol_fields())
    wire=registered.model_dump(mode='json')
    assert ResearchProtocol.model_validate(wire)==registered
    assert ResearchProtocol.model_validate_json(json.dumps(wire))==registered
    assert registered.registered_at.utcoffset().total_seconds()==0
    assert list(registered.controls)==['persistence','shuffled_ridge']


@pytest.mark.parametrize('change',[
    {'registered_at':'2026-10-01T10:00:00'},
    {'registered_by':' '},
    {'min_independent_sessions':1},
    {'min_independent_sessions':True},
    {'bootstrap_replicates':1000000},
    {'confidence_level':1.0},
    {'minimum_relative_improvement':1.0},
    {'minimum_relative_improvement':-.1},
    {'controls':['persistence']},
    {'controls':['persistence','ridge']},
    {'primary_metric':'best_test_metric'},
])
def test_protocol_rejects_unbounded_or_posthoc_metric_contracts(change):
    with pytest.raises(ValidationError):ResearchProtocol.model_validate(protocol_fields(**change))


def test_protocol_requires_explicit_session_count_and_improvement_threshold():
    for missing in ('min_independent_sessions','minimum_relative_improvement'):
        raw=protocol_fields();raw.pop(missing)
        with pytest.raises(ValidationError):ResearchProtocol.model_validate(raw)


def test_absent_and_explicit_null_protocol_preserve_existing_configuration_hash(tmp_path):
    import shutil
    (tmp_path/'config').mkdir();shutil.copytree(ROOT/'knowledge',tmp_path/'knowledge')
    raw=yaml.safe_load((ROOT/'config/system.yaml').read_text());raw['temporal'].pop('research_protocol',None)
    path=tmp_path/'config/system.yaml';path.write_text(yaml.safe_dump(raw,allow_unicode=True))
    before=load_bundle(path)
    raw['temporal']['research_protocol']=None;path.write_text(yaml.safe_dump(raw,allow_unicode=True))
    after=load_bundle(path)
    assert before.config_hash==after.config_hash
    assert 'research_protocol' not in before.snapshot['temporal']
    assert 'research_protocol' not in after.snapshot['temporal']


@pytest.fixture
def study(tmp_path):
    # A fabricated acquisition registry wrapped around committed timestamp
    # fixtures. No frame, feature or actual field identity is used or asserted.
    b=load_bundle()
    b.config.temporal.research_protocol=ResearchProtocol.model_validate(protocol_fields())
    identity=CaptureIdentity(physical_camera_id='camera_1',stable_view_id='fixture_fixed_view',review_status='confirmed')
    file_source=next(item for item in b.profile.sources if item.id=='file_01')
    file_source.capture_identity=identity
    view=content_hash({'contract':'physical-view-v1','physical_camera_id':'camera_1','stable_view_id':'fixture_fixed_view'})
    b.config.encoder=b.config.encoder.model_copy(update={'adapter':'onnx','weights_sha256':'a'*64,
        'feature_dim':512,'pooling':'global_avg_pool','preprocessing_version':pretrained.PREPROCESSING_VERSION,
        'checkpoint_revision':'torchvision-v0.22.1-R3D_18_Weights.KINETICS400_V1'})
    b.config.ingestion.decode_width=640;b.config.ingestion.encoder_input_size=112
    sources=[source(1),source(2),source(3,'calibration'),source(4,'test'),source(5,'test')]
    for item in sources:item['view_id']=view
    b.config.temporal.dataset_manifest=DatasetManifest.model_validate(dataset(sources))
    b=replace(b,paths={name:tmp_path/name for name in b.paths})
    db=Database(f'sqlite:///{tmp_path / "fixture-study.sqlite"}')
    Base.metadata.create_all(db.engine);insert_sources(db,sources)
    recorded=json.loads((ROOT/'docs/delivery/p0.3.4/garment_test_short/manifest.json').read_text())
    bindings=_bindings(b,SCENE)
    prep=deepcopy(recorded['preprocessing'])
    prep['implementation_sha']=bindings['feature_recipe']['video_implementation_sha']
    prep['recipe_implementation_sha']=bindings['feature_recipe']['recipe_implementation_sha']
    sampling=content_hash({'clip_ms':round(b.config.video.clip_seconds*1000),
        'stride_ms':round(b.config.video.stride_seconds*1000),'frames_per_clip':16,'method':'nearest_original_pts_v1'})
    space=FeatureSpace(contract_version='3.1',encoder_sha='a'*64,preprocessing_sha=content_hash(prep),
        sampling_sha=sampling,roi_policy_hash=roi_identity(file_source),pooling='global_avg_pool',dimension=512)
    with db.session() as session:
        for index,item in enumerate(sources):
            m=deepcopy(recorded)
            m.update(source_hash=item['source_sha256'],source_group_id=item['source_sha256'],
                source_id='file_01',split=item['split'],view_id=view,preprocessing=prep,
                feature_space=space.model_dump(),feature_space_id=space.identifier(),capture_identity=identity.model_dump(),
                acquisition={'capture_session_id':item['capture_session_id'],'captured_at':item['captured_at'],'review_status':'confirmed'},
                view_review=[{'domain_index':0,'start_ms':0,'end_ms':20440,'status':'usable',
                    'reason':'Fabricated timestamp fixture; not field review','reviewer':'fixture_reviewer'}])
            m['encoder_evidence']['weights_sha']='a'*64
            for batch in m['feature_batches']:
                batch.update(source_hash=item['source_sha256'],source_id='file_01',split=item['split'],
                    feature_space=space.model_dump(),feature_space_id=space.identifier())
            session.get(ImportJob,f'import_{index}').manifest_json=json.dumps(m)
    imports=ImportService(b,db);service=TemporalService(b,db,imports)
    train=snapshot_imports(b,db,SCENE,['import_0','import_1','import_2'],{'train','calibration'})
    test=snapshot_imports(b,db,SCENE,['import_3','import_4'],{'test'})
    yield service,train,test
    service.close();db.close()


def assert_code(code,operation):
    with pytest.raises(ContractError) as error:operation()
    assert error.value.code==code


def row_counts(service):
    with service.database.session() as session:
        return [session.scalar(select(func.count()).select_from(table)) for table in (TemporalRun,AuditEvent)]


def test_real_training_without_protocol_stops_before_vector_reads(study,monkeypatch):
    service,_,_=study
    service.settings.research_protocol=None
    monkeypatch.setattr(np,'load',lambda *a,**k:pytest.fail('missing protocol opened vectors'))
    before=row_counts(service)
    assert_code('TEMPORAL_PROTOCOL_REQUIRED',lambda:service.enqueue(SCENE,TrainRequest(import_ids=['import_0','import_1','import_2'])))
    assert row_counts(service)==before


def trained_row(service, train, identifier='fixture_model'):
    binding=freeze_protocol(service.bundle,SCENE,train)
    model={'snapshot':train,'research_protocol':binding,'engine_sha256':engine.code_hash(),
        'learning':service.bundle.config.learning.model_dump(mode='json'),
        'settings':service.settings.model_dump(mode='json'),'selected_model':'ridge'}
    with service.database.session() as session:
        session.add(TemporalRun(id=identifier,tenant_id=service.tenant,scene_id=SCENE,kind='train',
            idempotency_key=content_hash(identifier),config_hash=service.bundle.config_hash,
            knowledge_hash=service._knowledge_hash(SCENE),engine_hash=engine.code_hash(),
            request_json='{}',snapshot_json=json.dumps(train),status='succeeded',artifact_key='fixture_attempt',
            report_json=json.dumps({'model':model})))
    return model


def evaluation_request(identifier='fixture_model'):
    return EvaluationRequest(model_run_id=identifier,import_ids=['import_3','import_4'],sealed_test=True)


def mark_running(service,identifier):
    with service.database.session() as session:
        row=session.get(TemporalRun,identifier);row.status='running';row.artifact_key='fixture_attempt'


def test_preview_and_freeze_read_only_bind_all_partitions_before_values(study,monkeypatch):
    service,train,_=study;before=row_counts(service)
    monkeypatch.setattr(np,'load',lambda *a,**k:pytest.fail('protocol preview opened values'))
    preview=service.protocol_preview(SCENE)
    assert preview['status']=='configured' and preview['metadata_only']
    assert not preview['jobs_created'] and not preview['feature_values_read']
    frozen=freeze_protocol(service.bundle,SCENE,train)
    assert frozen['hash']==content_hash(frozen['snapshot'])
    assert len(frozen['snapshot']['roster']['partitions']['test'])==2
    assert frozen['snapshot']['training_snapshot_hash']==train['hash']
    assert frozen['snapshot']['bindings']['engine_hash']==engine.code_hash()
    assert row_counts(service)==before


@pytest.mark.parametrize('mutation,code',[
    ('encoder','TEMPORAL_FORMAL_ENCODER_REQUIRED'),('external','TEMPORAL_FORMAL_ENCODER_REQUIRED'),
    ('sampling','TEMPORAL_PROTOCOL_FEATURE_MISMATCH'),('recipe','TEMPORAL_PROTOCOL_FEATURE_MISMATCH'),
    ('roi','TEMPORAL_PROTOCOL_FEATURE_MISMATCH'),('legacy_capture','TEMPORAL_CAPTURE_REQUIRED')])
def test_real_freeze_rejects_diagnostic_external_stale_or_legacy_features(study,mutation,code):
    service,train,_=study;changed=deepcopy(train);entry=changed['entries'][0]
    if mutation=='encoder':entry['encoder_evidence']['weights_sha']='b'*64
    if mutation=='external':entry['encoder_evidence']['vector_production']='external_claim_only'
    if mutation=='sampling':entry['feature_space']['sampling_sha']='b'*64
    if mutation=='recipe':entry['preprocessing']['decode_width']+=1
    if mutation=='roi':entry['source_id']='unknown_registered_source'
    if mutation=='legacy_capture':entry.pop('capture_contract')
    assert_code(code,lambda:freeze_protocol(service.bundle,SCENE,changed))


def test_training_and_evaluation_require_complete_frozen_rosters(study):
    service,train,test=study
    frozen=freeze_protocol(service.bundle,SCENE,train)
    partial=deepcopy(train);partial['entries']=partial['entries'][1:]
    assert_code('TEMPORAL_PROTOCOL_ROSTER_MISMATCH',lambda:freeze_protocol(service.bundle,SCENE,partial))
    partial=deepcopy(test);partial['entries']=partial['entries'][:1]
    assert_code('TEMPORAL_PROTOCOL_ROSTER_MISMATCH',lambda:assert_frozen_protocol(frozen,partial,service.bundle,SCENE))
    assert_frozen_protocol(frozen,test,service.bundle,SCENE)


@pytest.mark.parametrize('mutation',['policy','sampling','knowledge','binding_tamper'])
def test_changed_protocol_or_versions_cannot_reuse_model_binding(study,mutation,monkeypatch):
    service,train,test=study;frozen=freeze_protocol(service.bundle,SCENE,train)
    if mutation=='policy':service.settings.research_protocol.minimum_relative_improvement=.25
    if mutation=='sampling':service.bundle.config.video.stride_seconds=1.0
    if mutation=='knowledge':
        import packages.temporal.protocol as module
        original=module._bindings
        monkeypatch.setattr(module,'_bindings',lambda *a,**k:{**original(*a,**k),'knowledge_hash':'d'*64})
    if mutation=='binding_tamper':frozen['snapshot']['protocol']['minimum_relative_improvement']=.8
    assert_code('TEMPORAL_PROTOCOL_CHANGED',lambda:assert_frozen_protocol(frozen,test,service.bundle,SCENE))


def test_frozen_protocol_written_before_first_training_vector_read(study,monkeypatch):
    service,_,_=study
    result=service.enqueue(SCENE,TrainRequest(import_ids=['import_0','import_1','import_2']))
    mark_running(service,result['id'])
    def load(*args):
        path=service.output_root/result['id']/'fixture_attempt'/'protocol.json'
        assert json.loads(path.read_text())==result['request']['_research_protocol']
        raise ContractError('FIXTURE_STOP','stop after proving freeze precedes vectors')
    monkeypatch.setattr('packages.temporal_jobs.service.load_sequences',load)
    service._process(result['id'])
    assert service.get(SCENE,result['id'])['error']['code']=='FIXTURE_STOP'


def test_protocol_change_while_queued_stops_before_feature_read(study,monkeypatch):
    service,_,_=study
    result=service.enqueue(SCENE,TrainRequest(import_ids=['import_0','import_1','import_2']))
    mark_running(service,result['id']);service.settings.research_protocol.minimum_relative_improvement=.2
    monkeypatch.setattr('packages.temporal_jobs.service.load_sequences',lambda *a:pytest.fail('changed protocol read vectors'))
    service._process(result['id'])
    assert service.get(SCENE,result['id'])['error']['code']=='TEMPORAL_PROTOCOL_CHANGED'


def test_consumption_committed_before_read_survives_failure_and_blocks_retry(study,monkeypatch):
    service,train,_=study;trained_row(service,train)
    request=evaluation_request();job=service.enqueue(SCENE,request,'evaluate');mark_running(service,job['id'])
    monkeypatch.setattr(engine,'load_model',lambda *a:({},{}))
    def crash(*args):
        with service.database.session() as session:
            assert service._test_consumed(session,job['id'])
        raise OSError('fixture cache error after test consumption')
    monkeypatch.setattr('packages.temporal_jobs.service.load_sequences',crash)
    service._process(job['id'])
    assert service.get(SCENE,job['id'])['status']=='failed'
    assert_code('TEMPORAL_TEST_ALREADY_CONSUMED',lambda:service.retry(SCENE,job['id']))
    # Idempotency returns the failed record; it does not execute it again.
    repeated=service.enqueue(SCENE,request,'evaluate')
    assert repeated['id']==job['id'] and not repeated['created'] and repeated['status']=='failed'
    trained_row(service,train,'another_model')
    assert_code('TEMPORAL_TEST_ALREADY_USED',lambda:service.enqueue(SCENE,evaluation_request('another_model'),'evaluate'))


def test_consumed_running_test_is_blocked_after_process_restart(study,monkeypatch):
    service,train,_=study;model=trained_row(service,train)
    job=service.enqueue(SCENE,evaluation_request(),'evaluate');mark_running(service,job['id'])
    service._consume_test(job['id'],model['research_protocol'])
    monkeypatch.setattr(service,'_loop',lambda:None)
    service.start();service.close()
    result=service.get(SCENE,job['id'])
    assert result['status']=='blocked' and result['error']['code']=='TEMPORAL_TEST_ALREADY_CONSUMED'


def test_failure_before_consumption_can_retry_same_reserved_job(study,monkeypatch):
    service,train,_=study;trained_row(service,train)
    job=service.enqueue(SCENE,evaluation_request(),'evaluate');mark_running(service,job['id'])
    monkeypatch.setattr(engine,'load_model',lambda *a:(_ for _ in ()).throw(ContractError('FIXTURE_MODEL_CORRUPT','fixture')))
    monkeypatch.setattr('packages.temporal_jobs.service.load_sequences',lambda *a:pytest.fail('bad model read vectors'))
    service._process(job['id'])
    with service.database.session() as session:assert not service._test_consumed(session,job['id'])
    assert service.retry(SCENE,job['id'])['status']=='queued'


def test_concurrent_services_reserve_heldout_only_once(study):
    service,train,_=study;trained_row(service,train,'model_a');trained_row(service,train,'model_b')
    other=TemporalService(service.bundle,service.database,service.imports)
    def enqueue(pair):
        target,name=pair
        try:return target.enqueue(SCENE,evaluation_request(name),'evaluate')['created']
        except ContractError as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(enqueue,[(service,'model_a'),(other,'model_b')]))
    assert sorted(results,key=str)==sorted([True,'TEMPORAL_TEST_ALREADY_USED'],key=str)
    with service.database.session() as session:
        assert session.scalar(select(func.count()).select_from(TemporalRun).where(TemporalRun.kind=='evaluate'))==1
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type=='temporal.test_reserved'))==1


@pytest.mark.parametrize('identity',['source','root','session'])
@pytest.mark.parametrize('kind',['train','evaluate'])
def test_legacy_fitting_or_evaluation_from_another_scene_prevents_resealing(study,identity,kind):
    service,train,test=study;trained_row(service,train)
    legacy=deepcopy(test);entry=legacy['entries'][0];legacy['entries']=[entry]
    if identity!='source':entry['source_hash']='f'*64
    if identity=='session':entry['provenance']['root_source_sha256']='e'*64
    if identity=='source':entry.pop('provenance')
    with service.database.session() as session:
        session.add(TemporalRun(id='legacy_study',tenant_id=service.tenant,scene_id='garment_sop',kind=kind,
            idempotency_key='b'*64,config_hash='c'*64,knowledge_hash='d'*64,engine_hash='e'*64,
            request_json='{}',snapshot_json=json.dumps(legacy),status='succeeded',report_json='{}'))
    assert_code('TEMPORAL_TEST_ALREADY_USED',lambda:service.enqueue(SCENE,evaluation_request(),'evaluate'))


def test_readiness_never_inspects_test_timestamps_without_admitted_candidate(study,monkeypatch):
    service,_,_=study
    import packages.temporal_jobs.readiness as readiness
    original=readiness.metadata_sequences
    def guarded(manifest,*args):
        assert manifest['split']!='test','unfrozen test metadata inspected'
        return original(manifest,*args)
    monkeypatch.setattr(readiness,'metadata_sequences',guarded)
    report=service.readiness(SCENE,ReadinessRequest(import_ids=['import_0','import_1','import_2','import_3','import_4']))
    assert report['can_submit_train'] and not report['can_submit_evaluate']
    assert report['partitions']['test']['causal_windows'] is None
    assert not report['evaluation']['windows_checked'] and report['evaluation']['rejected_windows']=={}


def test_readiness_real_missing_protocol_blocked_and_test_consumed_not_rechecked(study,monkeypatch):
    service,train,_=study
    service.settings.research_protocol=None
    result=service.readiness(SCENE,ReadinessRequest(import_ids=['import_0','import_1','import_2']))
    assert not result['can_submit_train']
    assert any(b['code']=='TEMPORAL_PROTOCOL_REQUIRED' for b in result['train']['blockers'])
    service.settings.research_protocol=ResearchProtocol.model_validate(protocol_fields())
    trained_row(service,train)
    request=ReadinessRequest(import_ids=['import_3','import_4'],model_run_id='fixture_model')
    ready=service.readiness(SCENE,request)
    assert ready['can_submit_evaluate'] and ready['evaluation']['windows_checked']
    assert ready['partitions']['test']['causal_windows']>0
    service.enqueue(SCENE,evaluation_request(),'evaluate')
    monkeypatch.setattr('packages.temporal_jobs.readiness.metadata_sequences',lambda *a:pytest.fail('reserved test inspected'))
    blocked=service.readiness(SCENE,request)
    assert not blocked['can_submit_evaluate'] and not blocked['evaluation']['windows_checked']
    assert any(b['code']=='TEMPORAL_TEST_ALREADY_USED' for b in blocked['evaluation']['blockers'])


def test_future_registration_and_insufficient_test_roster_do_not_freeze(study):
    service,train,_=study
    service.settings.research_protocol=ResearchProtocol.model_validate(protocol_fields(registered_at='2099-01-01T00:00:00+00:00'))
    assert_code('TEMPORAL_PROTOCOL_FUTURE_REGISTRATION',lambda:freeze_protocol(service.bundle,SCENE,train))
    service.settings.research_protocol=ResearchProtocol.model_validate(protocol_fields(min_independent_sessions=3))
    assert_code('TEMPORAL_PROTOCOL_INSUFFICIENT_SESSIONS',lambda:freeze_protocol(service.bundle,SCENE,train))


def test_same_session_derivative_requires_explicit_canonical_selection(study):
    service,_,_=study
    raw=service.settings.dataset_manifest.model_dump(mode='json');original=raw['sources'][0]
    child=deepcopy(original);child.update(source_sha256='e'*64,parent_source_sha256=original['source_sha256'],relation='transcode')
    raw['sources'].append(child);service.settings.dataset_manifest=DatasetManifest.model_validate(raw)
    preview=service.protocol_preview(SCENE)
    assert any(b['code']=='TEMPORAL_DUPLICATE_SESSION' for b in preview['blockers'])
    selected=[item['source_sha256'] for item in raw['sources'][:-1]]
    service.settings.research_protocol=ResearchProtocol.model_validate(protocol_fields(source_sha256s=selected))
    preview=service.protocol_preview(SCENE)
    assert preview['status']=='configured'
    assert len(service.settings.dataset_manifest.sources)==6
    assert sum(map(len,preview['rosters'][0]['partitions'].values()))==5


def test_pending_near_duplicate_review_blocks_before_values(study,monkeypatch):
    service,_,_=study
    raw=service.settings.dataset_manifest.model_dump(mode='json')
    raw['duplicate_reviews']=[{'left_sha256':raw['sources'][0]['source_sha256'],
        'right_sha256':raw['sources'][3]['source_sha256'],'resolution':'pending'}]
    service.settings.dataset_manifest=DatasetManifest.model_validate(raw)
    monkeypatch.setattr(np,'load',lambda *a,**k:pytest.fail('pending review opened values'))
    assert_code('TEMPORAL_DUPLICATE_REVIEW_PENDING',lambda:service.enqueue(SCENE,TrainRequest(import_ids=['import_0','import_1','import_2'])))
