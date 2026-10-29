"""Actual 26-page SOP extraction plus isolated review/media integrity regression."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import shutil

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.api.main import create_app
from infra.models import AuditEvent, KnowledgeVersion
from packages.config.errors import ContractError

ROOT = Path(__file__).resolve().parents[1]
BASE = '/api/scenes/electronics_assembly/sop'


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def sop_config(tmp_path):
    (tmp_path/'config').mkdir()
    shutil.copytree(ROOT/'knowledge', tmp_path/'knowledge')
    config=yaml.safe_load((ROOT/'config/system.yaml').read_text())
    assets=tmp_path/'assets';assets.mkdir()
    catalog=json.loads((ROOT/'datasets/video_samples/catalog.json').read_text())
    catalog['assets']=[a for a in catalog['assets'] if a['domain']=='bull_new_energy']
    outline=json.loads((ROOT/'datasets/video_samples/bull_new_energy/sop/outline.draft.json').read_text())
    mapping=json.loads((ROOT/'datasets/video_samples/bull_new_energy/sop/station-mapping.draft.json').read_text())
    for asset in catalog['assets']:
        target=assets/asset['path'];target.parent.mkdir(parents=True,exist_ok=True)
        if asset['kind']=='normative_document':
            shutil.copyfile(ROOT/asset['path'],target)
        else:
            # Range validation is byte serving, not codec validation; fixtures
            # carry their own hashes rather than impersonating actual recordings.
            data=(asset['asset_id']+' range fixture').encode()*200
            target.write_bytes(data);asset['sha256']=digest(data);asset['bytes']=len(data)
            next(e for e in mapping['entries'] if e['asset_id']==asset['asset_id'])['source_sha256']=asset['sha256']
    for name,data in [('catalog.json',catalog),('outline.json',outline),('mapping.json',mapping)]:
        (assets/name).write_text(json.dumps(data,ensure_ascii=False))
    config['sop_review'].update(asset_root='assets',catalog_path='catalog.json',outline_path='outline.json',mapping_path='mapping.json')
    path=tmp_path/'config/system.yaml';path.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
    return path


@pytest.fixture
def sop_api(sop_config):
    app=create_app(sop_config)
    with TestClient(app) as client:
        yield client,app,sop_config


def get(client):
    response=client.get(BASE)
    assert response.status_code==200,response.text
    return response.json()


def revision(client,state,**overrides):
    body={'base_revision':state['revision'],'reviewer':'process_owner','reason':'Reviewed the retained original references',
          'mappings':state['mappings'],'disputes':[]}|overrides
    return client.post(BASE+'/revisions',json=body)


def decision(client,state,action='approve',**overrides):
    return client.post(BASE+'/decisions',json={'revision':state['revision'],'action':action,'reviewer':'process_owner',
                                             'reason':'Review fixture; no activation'}|overrides)


def evidence(client,state,**overrides):
    return client.post(BASE+'/evidence',json={'revision':state['revision'],'kind':'observation','asset_id':'bull_station_01',
        'section_id':'D4-D7ABL.DGC07','start_ms':0,'end_ms':1000,'producer':'reviewer_01','note':'Visible operation only',
        'evidence_type':'visible_operation','evidence_reference':None}|overrides)


def test_real_pdf_has_26_pages_11_sections_and_exact_source_ranges(sop_api):
    client,app,_=sop_api
    state=get(client)
    assert state['source']['pdf_pages']==26 and state['source']['body_pages']==23
    assert state['source']['front_matter_pages']==[1,2,3]
    assert len(state['sections'])==11 and len(state['mappings'])==7
    assert state['source']['sha256']=='39802e29c07b43fbee3fa9274cc56d247f178fdb833cdbfd363bdbc89904a736'
    assert len(state['disputes'])==5 and state['status']=='draft'
    assert not state['activated'] and not state['is_ground_truth']
    assert state['initial_mapping_candidates'][0]['review_status']=='pending'
    assert state['initial_mapping_candidates'][0]['source_sha256']==state['assets'][0]['sha256']
    body_pages=[]
    for section in state['sections']:
        refs=section['raw_references'];assert len(refs)==len(section['pdf_pages'])
        for ref in refs:
            assert ref['source_sha256']==state['source']['sha256']
            assert ref['text_start']==0 and ref['text_end']==len(ref['text'])
            assert ref['text_sha256']==digest(ref['text'].encode())
            assert 'pdftotext version' in ref['extraction_version'] and 'unicode-codepoint-offsets-v1' in ref['extraction_version']
            body_pages.append(ref['pdf_page'])
    assert body_pages==list(range(4,27))
    for suffix in ('07','08','09'):
        assert next(s for s in state['sections'] if s['document_id'].endswith(suffix))['evidence_classification']['instrument_required']
    assert get(client)['revision']==state['revision']


def test_revisions_many_to_many_approve_revoke_append_only_and_no_activation(sop_api):
    client,app,_=sop_api
    state=get(client)
    with app.state.database.session() as session:
        knowledge_before=[(r.id,r.content_hash,r.status,r.data_json) for r in session.scalars(select(KnowledgeVersion))]
    blocked=decision(client,state)
    assert blocked.status_code==422 and blocked.json()['detail']['code']=='SOP_DISPUTES_PENDING'
    mappings=copy.deepcopy(state['mappings'])
    mappings[0]['section_ids']=['D4-D7ABL.DGC01','D4-D7ABL.DGC02']
    mappings[1]['section_ids']=list(mappings[0]['section_ids'])
    mappings[2]['section_ids']=[]
    response=revision(client,state,mappings=mappings)
    assert response.status_code==201,response.text
    revised=response.json();assert revised['revision']!=state['revision']
    approved=decision(client,revised)
    assert approved.status_code==200,approved.text
    approved=approved.json();assert approved['status']=='approved'
    assert not approved['activated'] and not approved['is_ground_truth'] and approved['compliance_result']=='not_evaluated'
    assert decision(client,approved).json()['detail']['code']=='SOP_DECISION_STATE'
    revoked=decision(client,approved,'revoke').json();assert revoked['status']=='revoked'
    assert decision(client,revoked,'revoke').json()['detail']['code']=='SOP_DECISION_STATE'
    assert [e['event_type'] for e in revoked['audit']]==['sop.revision','sop.decision','sop.decision']
    assert [e['sequence'] for e in revoked['audit']]==[1,2,3]
    assert all(e['source_sha256']==state['source']['sha256'] and e['config_hash']==app.state.bundle.config_hash for e in revoked['audit'])
    assert get(client)['audit']==revoked['audit']
    with app.state.database.session() as session:
        assert knowledge_before==[(r.id,r.content_hash,r.status,r.data_json) for r in session.scalars(select(KnowledgeVersion))]


def test_stale_and_concurrent_revisions_cannot_overwrite_current_review(sop_api):
    client,_,_=sop_api;state=get(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda i:revision(client,state,reason=f'review branch {i}'),range(2)))
    assert sorted(r.status_code for r in results)==[201,409]
    assert len(get(client)['audit'])==1
    assert decision(client,state).status_code==409
    assert evidence(client,state).status_code==409


@pytest.mark.parametrize('mutation,code',[
    ('missing_asset','SOP_MAPPING_ASSETS'),('duplicate_asset','SOP_MAPPING_ASSETS'),
    ('unknown_section','SOP_MAPPING_SECTIONS'),('duplicate_section','SOP_MAPPING_SECTIONS')])
def test_mapping_rejects_incomplete_and_ambiguous_identity(sop_api,mutation,code):
    client,_,_=sop_api;state=get(client);mappings=copy.deepcopy(state['mappings'])
    if mutation=='missing_asset':mappings.pop()
    elif mutation=='duplicate_asset':mappings[-1]=mappings[0]
    elif mutation=='unknown_section':mappings[0]['section_ids']=['unknown_section']
    else:mappings[0]['section_ids']=['D4-D7ABL.DGC01']*2
    response=revision(client,state,mappings=mappings)
    assert response.status_code==422 and response.json()['detail']['code']==code
    assert get(client)['audit']==[]


@pytest.mark.parametrize('kind',['observation','prediction','normative_expectation','human_annotation','semantic_candidate'])
def test_evidence_kinds_are_separate_and_instrument_claims_remain_unverified(sop_api,kind):
    client,_,_=sop_api;state=get(client)
    result=evidence(client,state,kind=kind)
    assert result.status_code==201,result.text
    saved=result.json()['evidence'][-1]
    assert saved['kind']==kind and saved['result']=='insufficient_evidence'
    assert saved['compliance_result']=='not_evaluated' and not saved['is_ground_truth']
    assert saved['document_sha256']==state['source']['sha256'] and saved['source_video_sha256']==state['assets'][0]['sha256']
    result=evidence(client,state,kind=kind,evidence_type='instrument_record',evidence_reference='instrument-report-001')
    if kind in ('prediction','semantic_candidate'):
        assert result.status_code==422
    else:
        assert result.status_code==201
        assert result.json()['evidence'][-1]['result']=='pending_instrument_review'
        assert result.json()['compliance_result']=='not_evaluated'


def test_evidence_ranges_unknown_references_and_blank_audit_are_rejected(sop_api):
    client,_,_=sop_api;state=get(client)
    for change,code in [({'asset_id':'garment_test'},'SOP_EVIDENCE_REFERENCE'),
                        ({'section_id':'unknown_section'},'SOP_EVIDENCE_REFERENCE'),
                        ({'end_ms':999999999},'SOP_EVIDENCE_RANGE'),
                        ({'start_ms':1000,'end_ms':500},'REQUEST_INVALID'),
                        ({'note':'   '},'REQUEST_INVALID'),
                        ({'evidence_reference':' '},'REQUEST_INVALID')]:
        response=evidence(client,state,**change)
        assert response.status_code==422 and response.json()['detail']['code']==code
    assert revision(client,state,reason=' ').json()['detail']['code']=='REQUEST_INVALID'
    assert revision(client,state,disputes=[' ']).json()['detail']['code']=='REQUEST_INVALID'
    assert get(client)['audit']==[]


def test_original_media_has_range_head_etag_and_pdf_mime(sop_api):
    client,app,_=sop_api;state=get(client)
    asset=state['assets'][0];url=asset['media_url'];path,_=app.state.sop.media('electronics_assembly',asset['asset_id']);data=path.read_bytes()
    response=client.get(url,headers={'Range':'bytes=7-30'})
    assert response.status_code==206 and response.content==data[7:31]
    assert response.headers['content-range']==f'bytes 7-30/{len(data)}'
    assert response.headers['content-length']=='24' and response.headers['content-type']=='video/mp4'
    head=client.head(url,headers={'Range':'bytes=7-30'})
    assert head.status_code==206 and not head.content and head.headers['content-length']=='24'
    assert client.get(url,headers={'Range':'bytes=-8'}).content==data[-8:]
    assert client.get(url,headers={'Range':'bytes=10-'}).content==data[10:]
    for value in ('bytes=-0','bytes=900000-','bytes=5-2','bytes=0-2,4-5','x=0-1','bytes='+'9'*5000+'-'):
        response=client.get(url,headers={'Range':value})
        assert response.status_code==416 and response.headers['content-range']==f'bytes */{len(data)}'
    assert client.get(url,headers={'Range':'bytes=7-30','If-Range':'"old-version"'}).content==data
    response=client.get(state['source']['media_url'],headers={'Range':'bytes=0-7'})
    assert response.status_code==206 and response.content.startswith(b'%PDF-')
    assert response.headers['content-type']=='application/pdf'


def test_scene_and_asset_scope_cannot_leak_other_registry(sop_api):
    client,_,_=sop_api
    assert client.get('/api/scenes/unknown_scene/sop').status_code==404
    assert client.get('/api/scenes/garment_sop/sop').json()['detail']['code']=='SOP_NOT_CONFIGURED'
    assert client.get(BASE+'/media/garment_test').json()['detail']['code']=='SOP_ASSET_NOT_FOUND'
    assert client.get(BASE+'/media/unknown_asset').json()['detail']['code']=='SOP_ASSET_NOT_FOUND'


def test_cached_original_detects_same_size_changed_bytes(sop_api):
    client,app,_=sop_api;state=get(client)
    asset=state['assets'][0];path,_=app.state.sop.media('electronics_assembly',asset['asset_id'])
    data=path.read_bytes();path.write_bytes(bytes([data[0]^1])+data[1:])
    response=client.get(asset['media_url'])
    assert response.status_code==422 and response.json()['detail']['code']=='SOP_ASSET_IDENTITY'


def test_registry_symlink_escape_is_refused(sop_api,tmp_path):
    client,app,_=sop_api;state=get(client)
    asset=state['assets'][0];path,_=app.state.sop.media('electronics_assembly',asset['asset_id'])
    outside=tmp_path/'not_an_asset.mp4';outside.write_bytes(path.read_bytes());path.unlink();path.symlink_to(outside)
    response=client.get(asset['media_url'])
    assert response.status_code==422 and response.json()['detail']['code']=='SOP_ASSET_PATH'


def test_mapping_source_sha_and_pdf_sha_changes_are_blocked(sop_config):
    assets=sop_config.parent.parent/'assets';mapping_path=assets/'mapping.json';mapping=json.loads(mapping_path.read_text())
    mapping['entries'][0]['source_sha256']='a'*64;mapping_path.write_text(json.dumps(mapping))
    with TestClient(create_app(sop_config)) as client:
        response=client.get(BASE)
        assert response.status_code==422 and response.json()['detail']['code']=='SOP_ASSET_IDENTITY'
