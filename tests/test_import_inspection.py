"""Registered media access and real CPU diagnostic graph exploration boundaries.

The generated test pattern and analytical ONNX graph are fixtures, not evidence
of field recognition or pretrained learning accuracy.
"""
import hashlib
import json
import subprocess

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.main import create_app
from infra.models import ImportJob
from packages.config.loader import load_bundle
from packages.contracts.ingestion import ImportRequest
from packages.knowledge.service import content_hash
from tests.test_ingestion_api import import_config, enqueue, upload, wait_job
from tests.test_ingestion_core import onnx_bundle, fixture_request, imported

SCENE='anonymous_observation'


@pytest.fixture
def diagnostic_video(tmp_path):
    path=tmp_path/'test-pattern.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=64x48:rate=16:duration=4',
                    '-c:v','mpeg4','-threads','1','-y',str(path)],check=True,timeout=30)
    return path


@pytest.fixture
def inspection_api(import_config,tmp_path):
    diagnostic=onnx_bundle(tmp_path)
    raw=yaml.safe_load(import_config.read_text())
    raw['encoder']=diagnostic.config.encoder.model_dump()
    raw['ingestion']['encoder_input_size']=16
    raw['profiles']['demo']['encoder_weights']=diagnostic.profile.encoder_weights
    raw['sop_review']['preview_enabled']=False
    import_config.write_text(yaml.safe_dump(raw,allow_unicode=True,sort_keys=False))
    app=create_app(import_config)
    with TestClient(app) as client:yield client,app,import_config


def import_pattern(client,path,**extra):
    source=upload(client,path.read_bytes(),'mp4')
    body={'upload_id':source['id'],'format':'mp4','source_id':'file_01','data_origin':'real_video',
          'episode_origin':'manual','episodes':[{'episode_id':'fixture','domain_index':0,'start_ms':0,'end_ms':4000}],
          'acquisition':{'capture_session_id':'fixture_session','captured_at':'2026-10-29T01:02:03+08:00','review_status':'confirmed'},**extra}
    job=enqueue(client,SCENE,body)
    result=wait_job(client,SCENE,job['id'])
    assert result['status']=='succeeded',result['error']
    return result


def test_actual_import_media_ranges_hash_revalidation_and_scope(inspection_api,diagnostic_video):
    client,app,_=inspection_api
    job=import_pattern(client,diagnostic_video)
    assert job['manifest']['acquisition']['captured_at']=='2026-10-29T01:02:03+08:00'
    prefix=f'/api/scenes/{SCENE}/imports/{job["id"]}'
    original=diagnostic_video.read_bytes()
    full=client.get(prefix+'/media');assert full.content==original
    assert full.headers['etag']=='"'+hashlib.sha256(original).hexdigest()+'"'
    partial=client.get(prefix+'/media',headers={'Range':'bytes=4-17'})
    assert partial.status_code==206 and partial.content==original[4:18]
    assert partial.headers['content-range']==f'bytes 4-17/{len(original)}'
    head=client.head(prefix+'/media');assert head.status_code==200 and head.content==b''
    other=client.get(f'/api/scenes/workshop_safety/imports/{job["id"]}/media')
    assert other.status_code==404 and other.json()['detail']['code']=='IMPORT_NOT_FOUND'
    path,_=app.state.inspection.media(SCENE,job['id'])
    path.write_bytes(bytes([original[0]^1])+original[1:])
    changed=client.get(prefix+'/media')
    assert changed.status_code==422 and changed.json()['detail']['code']=='UPLOAD_CORRUPTED'


def test_real_import_exploration_preserves_anonymous_scope_and_media_links(inspection_api,diagnostic_video):
    client,app,_=inspection_api;job=import_pattern(client,diagnostic_video)
    prefix=f'/api/scenes/{SCENE}/imports/{job["id"]}'
    response=client.get(prefix+'/exploration');assert response.status_code==200,response.text
    report=response.json()
    assert report['source_hash']==job['manifest']['source_hash']
    assert report['eligible_rows']==5 and report['feature_space']['dimension']==3
    assert report['segments'] and report['coverage']['fraction']==1
    assert report['media_url']==prefix+'/media' and report['preview_url'] is None
    assert report['training_executed'] is False and report['test_read'] is False
    assert report['semantic_accuracy'] is None and report['auto_activated'] is False
    assert client.get(prefix+'/media/preview').json()['detail']['code']=='PREVIEW_DISABLED'
    assert client.get(f'/api/scenes/workshop_safety/imports/{job["id"]}/exploration').status_code==404
    app.state.bundle.config.temporal=None
    assert client.get(prefix+'/exploration').json()['detail']['code']=='TEMPORAL_DISABLED'


@pytest.mark.parametrize('split',['train','calibration','test'])
def test_assigned_source_exploration_rejected_before_media_read(inspection_api,diagnostic_video,monkeypatch,split):
    client,app,_=inspection_api;job=import_pattern(client,diagnostic_video,requested_split=split)
    def forbidden(*args):raise AssertionError('An assigned source must not be opened by exploration')
    monkeypatch.setattr(app.state.inspection,'media',forbidden)
    result=client.get(f'/api/scenes/{SCENE}/imports/{job["id"]}/exploration')
    assert result.status_code==422 and result.json()['detail']['code']=='EXPLORATION_SPLIT_FORBIDDEN'


def test_synthetic_reference_cannot_expose_media_or_real_exploration(inspection_api):
    client,_,_=inspection_api
    queued=client.post(f'/api/scenes/{SCENE}/imports/example').json()
    job=wait_job(client,SCENE,queued['id']);assert job['status']=='succeeded',job['error']
    prefix=f'/api/scenes/{SCENE}/imports/{job["id"]}'
    assert client.get(prefix+'/media').json()['detail']['code']=='IMPORT_MEDIA_UNAVAILABLE'
    assert client.get(prefix+'/exploration').json()['detail']['code']=='EXPLORATION_SOURCE_UNVERIFIED'


def test_verified_real_npz_retains_full_review_and_excludes_human_interval(tmp_path,diagnostic_video):
    bundle=load_bundle();example,request=fixture_request(bundle,tmp_path/'features')
    with np.load(example['input_path'],allow_pickle=False) as original:
        features=original['features'][:4].copy()
    np.savez_compressed(example['input_path'],features=features,pts_ms=np.arange(4)*1000,durations_ms=np.full(4,1000))
    source=next(s for s in bundle.profile.sources if s.id=='file_01')
    fields=request.model_dump(mode='json')
    fields.update(source_id='file_01',data_origin='real_video',declared_source_hash=hashlib.sha256(diagnostic_video.read_bytes()).hexdigest(),
        feature_space={**fields['feature_space'],'roi_policy_hash':content_hash({'source_id':source.id,'roi_xyxy':source.roi_xyxy})},
        episode_origin='manual',episodes=[{'episode_id':'fixture','domain_index':0,'start_ms':0,'end_ms':4000}],
        view_intervals=[{'domain_index':0,'start_ms':0,'end_ms':1000,'status':'usable','reason':'fixture stable','reviewer':'reviewer'},
                        {'domain_index':0,'start_ms':1000,'end_ms':2000,'status':'excluded','reason':'fixture camera movement','reviewer':'reviewer'}])
    manifest=imported(bundle,example,ImportRequest.model_validate(fields),tmp_path/'out',source_path=diagnostic_video)
    assert manifest['source_verified'] is True and manifest['total_rows']==4 and manifest['eligible_rows']==3
    assert [v['status'] for v in manifest['view_review']]==['usable','excluded','unreviewed']
    assert manifest['view_review'][-1]['start_ms']==2000 and manifest['view_review'][-1]['end_ms']==4000
    assert manifest['rejected_rows']==[{'row':1,'reason':'human_view_exclusion'}]
    assert [row['eligible'] for row in manifest['rows']]==[True,False,True,True]
    assert all(not (b['pts_start_ms']<2000 and b['pts_end_ms']>1000) for b in manifest['feature_batches'])
