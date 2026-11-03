"""Sealed-media counterexamples use a generated pattern, never real sealed data."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from sqlalchemy import select
from infra.models import SourceSplit
from tests.test_import_inspection import inspection_api,diagnostic_video,import_pattern,SCENE
from tests.test_ingestion_api import import_config


def test_sealed_original_and_preview_rejected_before_any_content_io(inspection_api,diagnostic_video,monkeypatch):
    client,app,_=inspection_api;job=import_pattern(client,diagnostic_video,requested_split='test')
    app.state.bundle.config.sop_review.preview_enabled=True
    prefix=f'/api/scenes/{SCENE}/imports/{job["id"]}'
    assert client.get(prefix+'/exploration').status_code==422
    original=Path.open;opened=[]
    def watch(path,*a,**kw):
        if str(path).endswith('.mp4'):opened.append(str(path))
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'open',watch)
    def forbidden(*a,**kw):pytest.fail('Unauthorized request must not transcode or hit preview cache')
    monkeypatch.setattr('packages.sop.preview.preview_video',forbidden)
    for method,suffix,headers in [('GET','/media',{}),('GET','/media',{'Range':'bytes=0-9'}),
        ('HEAD','/media',{}),('GET','/media',{'Range':'bytes=broken'}),('GET','/media/preview',{}),('HEAD','/media/preview',{})]:
        r=client.request(method,prefix+suffix,headers=headers)
        assert r.status_code==422,(method,suffix,r.status_code)
        if method!='HEAD':assert r.json()['detail']['code']=='MEDIA_SEALED_FORBIDDEN'
    assert opened==[]


def test_current_partition_overrides_old_unassigned_manifest_and_parallel_cache(inspection_api,diagnostic_video):
    client,app,_=inspection_api;job=import_pattern(client,diagnostic_video)
    url=f'/api/scenes/{SCENE}/imports/{job["id"]}/media'
    assert client.get(url,headers={'Range':'bytes=0-9'}).status_code==206
    with app.state.database.session() as s:
        s.scalar(select(SourceSplit).where(SourceSplit.source_hash==job['manifest']['source_hash'])).split='test'
    with ThreadPoolExecutor(max_workers=6) as pool:
        codes=list(pool.map(lambda _:client.get(url,headers={'Range':'bytes=0-9'}).status_code,range(12)))
    assert codes==[422]*12


def test_registered_symlink_and_hardlink_aliases_rejected(inspection_api,diagnostic_video,tmp_path):
    import os
    client,app,_=inspection_api;job=import_pattern(client,diagnostic_video,requested_split='test')
    source=app.state.imports.upload_root/(job['manifest']['source_hash']+'.mp4')
    for kind in ('symlink','hardlink'):
        alias=tmp_path/(kind+'.mp4')
        if kind=='symlink':alias.symlink_to(source)
        else:os.link(source,alias)
        with pytest.raises(Exception) as error:app.state.inspection.access.check(SCENE,alias,'f'*64)
        assert error.value.code=='MEDIA_SEALED_FORBIDDEN'


def test_original_root_alias_and_deferred_response_rechecked(inspection_api,diagnostic_video,tmp_path):
    import asyncio
    from types import SimpleNamespace as N
    from starlette.responses import Response
    client,app,_=inspection_api;job=import_pattern(client,diagnostic_video)
    access=app.state.inspection.access;path,sha=app.state.inspection.media(SCENE,job['id'])
    response=access.response(Response(b'not-for-release'),SCENE,path,sha)
    root='b'*64
    app.state.bundle.config.temporal.dataset_manifest=N(tenant_id=app.state.bundle.profile.tenant_id,sources=[
        N(source_sha256=sha,root_source_sha256=root,parent_source_sha256=root,capture_session_id='same',split='unassigned'),
        N(source_sha256=root,root_source_sha256=root,parent_source_sha256=None,capture_session_id='same',split='test')])
    sent=[]
    async def send(message):sent.append(message)
    async def receive():return {'type':'http.request'}
    with pytest.raises(Exception) as error:asyncio.run(response({'type':'http','method':'GET'},receive,send))
    assert error.value.code=='MEDIA_SEALED_FORBIDDEN' and sent==[]


from tests.test_sop_review import sop_api,sop_config


def test_sop_catalog_alias_blocked_before_verification_preview_or_cached_pdf(sop_api,monkeypatch):
    from infra.models import new_id
    client,app,_=sop_api;scene='electronics_assembly'
    service=app.state.sop;state=service.get(scene);a=next(v for v in state['assets'] if v['kind']=='video')
    url=f'/api/scenes/{scene}/sop/media/'+a['asset_id']
    assert client.get(url,headers={'Range':'bytes=0-3'}).status_code==206
    with app.state.database.session() as s:s.add(SourceSplit(id='a'*64,tenant_id=service.tenant,source_hash=a['sha256'],split='test'))
    def forbidden(*a,**kw):pytest.fail('SOP sealed alias must reject before fingerprint/PDF/transcode')
    monkeypatch.setattr(service,'_verify',forbidden)
    monkeypatch.setattr('packages.sop.preview.preview_video',forbidden)
    for method,suffix in [('GET',''),('HEAD',''),('GET','/preview'),('HEAD','/preview')]:
        r=client.request(method,url+suffix,headers={'Range':'bytes=0-3'})
        assert r.status_code==422


def test_stream_lease_serializes_sealing_against_already_authorized_delivery(inspection_api,diagnostic_video):
    from threading import Event
    from starlette.responses import StreamingResponse
    client,app,_=inspection_api;job=import_pattern(client,diagnostic_video)
    access=app.state.inspection.access;path,sha=app.state.inspection.media(SCENE,job['id'])
    started,release,sealed=Event(),Event(),Event()
    @app.get('/test-only-guarded-stream')
    def stream():
        def chunks():
            started.set();assert release.wait(10);yield b'generated mechanism fixture'
        return access.response(StreamingResponse(chunks()),SCENE,path,sha)
    app.router.routes.insert(0,app.router.routes.pop())
    def seal():
        with access.gate.hold(),app.state.database.session() as s:
            s.scalar(select(SourceSplit).where(SourceSplit.source_hash==sha)).split='test'
        sealed.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading=pool.submit(client.get,'/test-only-guarded-stream');assert started.wait(5)
        sealing=pool.submit(seal)
        try:assert not sealed.wait(.1)
        finally:release.set()
        assert reading.result(10).status_code==200;sealing.result(10)
    assert sealed.is_set()
    assert client.get(f'/api/scenes/{SCENE}/imports/{job["id"]}/media').status_code==422
