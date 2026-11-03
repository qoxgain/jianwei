"""Production dependency changes must not be mislabeled as old feature spaces."""
from copy import deepcopy
from pathlib import Path
import pytest
from packages.config.errors import ContractError
from packages.ingestion import recipe
from packages.knowledge.service import content_hash
from packages.temporal import engine, protocol
from packages.ingest_jobs.service import ImportService
from tests.test_research_protocol import study, SCENE


def test_dependency_closure_contains_sampling_encoder_time_roi_and_contracts():
    snapshot=recipe.production_snapshot()
    paths=set(snapshot['source_files'])
    for path in ('packages/ingestion/pipeline.py','packages/ingestion/video.py',
        'packages/ingestion/time.py','packages/ingestion/capture.py','packages/encoders/onnx_cpu.py',
        'packages/encoders/pretrained.py','packages/config/loader.py','packages/contracts/ingestion.py'):
        assert path in paths
    assert snapshot['sha256']==content_hash({k:v for k,v in snapshot.items() if k!='sha256'})
    assert set(snapshot['runtime']['tools'])=={'ffmpeg','ffprobe'}
    assert 'numpy' in snapshot['runtime']['packages']


@pytest.mark.parametrize('name',['pipeline.py','onnx_cpu.py','time.py','capture.py'])
def test_changed_source_invalidates_producer_protocol_and_engine(study,monkeypatch,name):
    service,train,test=study
    before=recipe.production_snapshot();key=ImportService.implementation_hash()
    model_hash=engine.code_hash();binding=protocol.freeze_protocol(service.bundle,SCENE,train)
    original=recipe.digest
    monkeypatch.setattr(recipe,'digest',lambda p:'f'*64 if Path(p).name==name else original(p))
    assert recipe.production_snapshot()!=before and ImportService.implementation_hash()!=key
    # The engine independently hashes the same dependency closure.
    original_hash=engine.file_hash
    monkeypatch.setattr(engine,'file_hash',lambda p:'f'*64 if Path(p).name==name else original_hash(p))
    assert engine.code_hash()!=model_hash
    with pytest.raises(ContractError) as error:protocol.assert_frozen_protocol(binding,test,service.bundle,SCENE)
    assert error.value.code=='TEMPORAL_PROTOCOL_CHANGED'
    assert protocol.preview_protocol(service.bundle,SCENE)['bindings']!=binding['snapshot']['bindings']


def test_changed_runtime_invalidates_cache_identity_without_changing_user_config(monkeypatch):
    before=ImportService.implementation_hash();original=recipe.runtime_snapshot
    monkeypatch.setattr(recipe,'runtime_snapshot',lambda:{**original(),'packages':{'numpy':'changed-runtime-fixture'}})
    assert ImportService.implementation_hash()!=before


def test_old_metadata_stays_unmodified_but_new_study_requires_new_recipe(study):
    service,train,_=study;original=deepcopy(train)
    for entry in train['entries']:entry['preprocessing'].pop('production_dependencies')
    with pytest.raises(ContractError) as error:protocol.freeze_protocol(service.bundle,SCENE,train)
    assert error.value.code=='TEMPORAL_PRODUCTION_RECIPE_REQUIRED'
    assert 'production_dependencies' in original['entries'][0]['preprocessing']
