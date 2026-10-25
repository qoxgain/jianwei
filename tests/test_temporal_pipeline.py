"""Research boundaries exercised through registered P0.2 imports and real weights."""
import json
from pathlib import Path
import shutil
import time
from copy import deepcopy
import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select
from apps.api.main import create_app
from infra.models import TemporalRun
from packages.config.errors import ContractError
from packages.temporal.data import load_sequences, snapshot_imports, window_refs, Sequence
from packages.temporal import engine

ROOT=Path(__file__).resolve().parents[1]


def setup_config(tmp_path):
    shutil.copytree(ROOT/'config',tmp_path/'config');shutil.copytree(ROOT/'knowledge',tmp_path/'knowledge')
    path=tmp_path/'config/system.yaml';raw=yaml.safe_load(path.read_text())
    raw['encoder']['feature_dim']=8
    raw['learning'].update(hidden_dim=6,epochs=3,batch_size=16,learning_rate=.01)
    raw['temporal'].update(projection_dim=4,max_pairs_per_split=32,worker_poll_seconds=.01)
    raw['ingestion']['worker_poll_seconds']=.01
    path.write_text(yaml.safe_dump(raw,allow_unicode=True));return path


def wait_for(client,path,seconds=20):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        response=client.get(path);assert response.status_code==200,response.text
        result=response.json()
        if result['status'] not in {'queued','running'}:return result
        time.sleep(.02)
    raise AssertionError('job timeout: '+path)


@pytest.fixture(scope='module')
def research(tmp_path_factory):
    root=tmp_path_factory.mktemp('research');config=setup_config(root)
    app=create_app(config)
    with TestClient(app) as client:
        example=client.post('/api/scenes/anonymous_observation/temporal/example')
        assert example.status_code==202,example.text
        example=example.json()
        for ids in example['groups'].values():
            for identifier in ids:
                imported=wait_for(client,f'/api/scenes/anonymous_observation/imports/{identifier}')
                assert imported['status']=='succeeded',imported
        yield client,app,example,root


def test_full_training_then_sealed_evaluation_and_artifacts(research):
    client,app,example,root=research;prefix='/api/scenes/anonymous_observation/temporal'
    request={'import_ids':example['train_import_ids']}
    response=client.post(prefix+'/train',json=request);assert response.status_code==202,response.text
    identifier=response.json()['id']
    assert client.post(prefix+'/train',json=request).json()['id']==identifier
    result=wait_for(client,prefix+'/runs/'+identifier)
    assert result['status']=='succeeded',result['error']
    report=result['report'];assert report['model']['state']=='candidate'
    assert not report['test_read'] and not report['auto_activated']
    assert report['training']['parameter_delta_l2']>0
    assert report['snapshot']['source_counts']=={'train':4,'calibration':2}
    assert set(report['calibration']['metrics'])==set(engine.MODELS)
    assert report['semantic_accuracy'] is None
    assert client.get('/api/scenes/garment_sop/temporal/runs/'+identifier).status_code==404
    weights=client.get(prefix+'/runs/'+identifier+'/artifacts/weights.npz')
    assert weights.status_code==200 and weights.content[:2]==b'PK'
    logs=client.get(prefix+'/runs/'+identifier+'/artifacts/calibration-predictions.jsonl').text.splitlines()
    records=[json.loads(line) for line in logs]
    for prediction,observation in zip(records[::2],records[1::2]):
        assert prediction['kind']=='empirical_forecast' and observation['kind']=='future_feature_observation'
        assert observation['prediction_hash']==prediction['record_hash']
        assert all(prediction['input_max_pts_ms']<=prediction['issued_at_pts_ms']<start<end for start,end in prediction['target_windows'])
    evaluation={'model_run_id':identifier,'import_ids':example['test_import_ids'],'sealed_test':True}
    r=client.post(prefix+'/evaluate',json=evaluation);assert r.status_code==202,r.text
    test=wait_for(client,prefix+'/runs/'+r.json()['id'])
    assert test['status']=='succeeded',test['error']
    assert test['report']['selected_model']==report['model']['selected_model']
    assert not test['report']['refit_on_test']
    assert test['report']['test']['independent_sources']==2
    assert client.get(prefix+'/runs/'+identifier).json()['report']==report


@pytest.mark.parametrize('mutation',['test','duplicate','cross_scene','tenant'])
def test_training_request_rejects_leakage_and_scope(research,mutation):
    client,_,example,_=research;body={'import_ids':example['train_import_ids']}
    path='/api/scenes/anonymous_observation/temporal/train'
    if mutation=='test':body['import_ids']=body['import_ids']+example['test_import_ids']
    if mutation=='duplicate':body['import_ids']=body['import_ids']+[body['import_ids'][0]]
    if mutation=='cross_scene':path='/api/scenes/garment_sop/temporal/train'
    if mutation=='tenant':body['tenant_id']='foreign'
    response=client.post(path,json=body)
    assert response.status_code in {404,422},response.text


def test_snapshot_cache_tamper_is_blocked(research):
    _,app,example,_=research;service=app.state.temporal;bundle=app.state.bundle
    snapshot=snapshot_imports(bundle,app.state.database,'anonymous_observation',example['train_import_ids'],{'train','calibration'})
    entry=snapshot['entries'][0];path=app.state.imports.output_root/entry['import_id']/'vectors.npy'
    old=path.read_bytes()
    try:
        path.write_bytes(old+b'changed')
        with pytest.raises(ContractError,match='缓存'):
            load_sequences(bundle,app.state.database,app.state.imports.output_root,snapshot)
    finally:path.write_bytes(old)


def test_calibration_does_not_fit_models_and_reload_reproduces(research,tmp_path):
    _,app,example,_=research;b=app.state.bundle
    snapshot=snapshot_imports(b,app.state.database,'anonymous_observation',example['train_import_ids'],{'train','calibration'})
    sequences=load_sequences(b,app.state.database,app.state.imports.output_root,snapshot)
    first=engine.train(sequences,snapshot,b.config.learning,b.config.temporal,tmp_path/'one',b.config_hash,'0'*64)
    changed=[Sequence(s.manifest,np.array(s.vectors)+(10 if s.batches[0].split=='calibration' else 0),s.batches,s.rows) for s in sequences]
    second=engine.train(changed,snapshot,b.config.learning,b.config.temporal,tmp_path/'two',b.config_hash,'0'*64)
    assert first['model']['weights_sha256']==second['model']['weights_sha256']
    model,arrays=engine.load_model(tmp_path/'one',first['model'])
    _,arrays2=engine.load_model(tmp_path/'two',second['model'])
    history=np.array(sequences[0].vectors[:b.config.learning.history_windows])
    pred,_=engine.predict(history,arrays,3);again,_=engine.predict(history,arrays2,3)
    for key in pred:np.testing.assert_array_equal(pred[key],again[key])
    assert not first['test_read']


def test_no_pair_crosses_quality_gap_or_episode(research):
    _,app,example,_=research;b=app.state.bundle
    snapshot=snapshot_imports(b,app.state.database,'anonymous_observation',example['train_import_ids'],{'train','calibration'})
    sequences=load_sequences(b,app.state.database,app.state.imports.output_root,snapshot)
    seq=sequences[0];keep=[i for i in range(len(seq.rows)) if i!=20]
    broken=Sequence(seq.manifest,seq.vectors,[seq.batches[i] for i in keep],[seq.rows[i] for i in keep])
    windows,rejected=window_refs([broken],b.config.learning,b.config.temporal)
    assert rejected['quality_or_continuity_gap']>0
    for w in windows:
        original_rows=[broken.rows[i] for i in (*w.history,*w.targets)]
        assert not min(original_rows)<20<max(original_rows)
        assert len({broken.batches[i].episode_id for i in (*w.history,*w.targets)})==1


def test_disabled_old_configuration_keeps_imports_usable(tmp_path):
    path=setup_config(tmp_path);raw=yaml.safe_load(path.read_text());raw.pop('temporal');path.write_text(yaml.safe_dump(raw))
    with TestClient(create_app(path)) as client:
        assert not client.get('/api/temporal/capabilities').json()['enabled']
        assert client.get('/api/ingestion/capabilities').json()['enabled']
        assert client.post('/api/scenes/anonymous_observation/temporal/example').status_code==422


def test_discarded_clip_is_a_barrier_for_all_consumers(research):
    from packages.temporal.data import has_barrier
    _,app,example,_=research;b=app.state.bundle
    snapshot=snapshot_imports(b,app.state.database,'anonymous_observation',example['train_import_ids'],{'train','calibration'})
    seq=load_sequences(b,app.state.database,app.state.imports.output_root,snapshot)[0]
    changed=Sequence(deepcopy(seq.manifest),seq.vectors,seq.batches,seq.rows)
    changed.manifest['discarded_clips']=[{'start_ms':19000,'end_ms':21000,'time_domain_id':seq.batches[0].time_domain_id}]
    assert has_barrier(changed,18,21,b.config.temporal)
    windows,_=window_refs([changed],b.config.learning,b.config.temporal)
    assert all(not (w.history[0]<21 and max(w.targets)>18) for w in windows)


def test_research_failure_retries_without_reusing_partial_weights(research,monkeypatch):
    client,app,example,_=research;prefix='/api/scenes/anonymous_observation/temporal'
    original=engine.train
    def blocked(*args,**kwargs):raise ContractError('TEST_TRANSIENT','controlled retry test')
    monkeypatch.setattr(engine,'train',blocked)
    response=client.post(prefix+'/train',json={'import_ids':example['train_import_ids'][1:]})
    assert response.status_code==202,response.text
    identifier=response.json()['id'];first=wait_for(client,prefix+'/runs/'+identifier)
    assert first['status']=='blocked'
    with app.state.database.session() as session:
        first_attempt=session.get(TemporalRun,identifier).artifact_key
    monkeypatch.setattr(engine,'train',original)
    assert client.post(prefix+'/runs/'+identifier+'/retry').status_code==202
    retried=wait_for(client,prefix+'/runs/'+identifier)
    assert retried['status']=='succeeded',retried['error']
    with app.state.database.session() as session:
        assert session.get(TemporalRun,identifier).artifact_key!=first_attempt


@pytest.mark.parametrize('config_changed',[False,True])
def test_pending_research_recovers_or_blocks_changed_configuration(tmp_path,config_changed):
    config=setup_config(tmp_path);app=create_app(config);prefix='/api/scenes/anonymous_observation/temporal'
    with TestClient(app) as client:
        example=client.post(prefix+'/example').json()
        for identifier in example['train_import_ids']:
            assert wait_for(client,'/api/scenes/anonymous_observation/imports/'+identifier)['status']=='succeeded'
        app.state.temporal.close()
        response=client.post(prefix+'/train',json={'import_ids':example['train_import_ids']})
        assert response.status_code==202,response.text
        identifier=response.json()['id']
        with app.state.database.session() as session:
            session.get(TemporalRun,identifier).status='running'
    if config_changed:
        raw=yaml.safe_load(config.read_text());raw['temporal']['seed']+=1;config.write_text(yaml.safe_dump(raw))
    with TestClient(create_app(config)) as client:
        result=wait_for(client,prefix+'/runs/'+identifier)
        if config_changed:
            assert result['status']=='blocked' and result['error']['code']=='TEMPORAL_CONFIG_CHANGED'
            assert client.post(prefix+'/runs/'+identifier+'/retry').status_code==422
        else:
            assert result['status']=='succeeded',result['error']
            assert client.post(prefix+'/train',json={'import_ids':example['train_import_ids']}).json()['id']==identifier


def test_sealed_evaluation_rejects_calibration_and_corrupted_model(research):
    client,app,example,_=research;prefix='/api/scenes/anonymous_observation/temporal'
    identifier=client.post(prefix+'/train',json={'import_ids':example['train_import_ids']}).json()['id']
    trained=wait_for(client,prefix+'/runs/'+identifier)
    response=client.post(prefix+'/evaluate',json={'model_run_id':identifier,'import_ids':example['groups']['calibration'],'sealed_test':True})
    assert response.status_code==422 and response.json()['detail']['code']=='TEMPORAL_SPLIT_FORBIDDEN'
    with app.state.database.session() as session:
        row=session.get(TemporalRun,identifier);directory=app.state.temporal.output_root/row.id/row.artifact_key
    weights=directory/'weights.npz';old=weights.read_bytes()
    try:
        weights.write_bytes(old+b'tamper')
        with pytest.raises(ContractError,match='hash'):
            engine.load_model(directory,trained['report']['model'])
    finally:weights.write_bytes(old)
