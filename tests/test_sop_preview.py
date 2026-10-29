"""Display-only derivatives preserve original identity, geometry and packet PTS."""
from types import SimpleNamespace
from pathlib import Path
import hashlib
import json
import subprocess
import time

import pytest
from packages.config.errors import ContractError
from packages.contracts.ingestion import IngestionSettings
from packages.contracts.sop import SopSettings
from packages.sop.preview import preview_video, _packet_times


def video(tmp_path,angle=0,sar='1/1'):
    base=tmp_path/'base.mp4';target=tmp_path/'中文原片.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=128x96:rate=8:duration=2',
        '-vf','setsar='+sar,'-c:v','libx264','-threads','1',str(base)],check=True)
    subprocess.run(['ffmpeg','-v','error','-y','-display_rotation',str(angle),'-i',str(base),'-c','copy',str(target)],check=True)
    return target


def fixture(tmp_path,**changes):
    bundle=SimpleNamespace(profile=SimpleNamespace(tenant_id='preview_fixture'),
        paths={'datasets':tmp_path/'datasets'},config=SimpleNamespace(ingestion=IngestionSettings()))
    settings=SopSettings(scene_id='electronics_assembly',catalog_path='unused',outline_path='unused',mapping_path='unused',preview_long_edge=128,**changes)
    return bundle,settings


@pytest.mark.parametrize('angle,sar,dimensions',[(0,'1/1',(128,96)),(90,'1/1',(96,128)),(-90,'1/1',(96,128)),(180,'1/1',(128,96)),(-90,'2/1',(48,128))])
def test_preview_has_display_geometry_once_original_pts_and_source_sha(tmp_path,angle,sar,dimensions):
    source=video(tmp_path,angle,sar);before=source.read_bytes();sha=hashlib.sha256(before).hexdigest()
    bundle,settings=fixture(tmp_path)
    path,metadata=preview_video(source,sha,bundle,settings)
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(path)]))['streams'][0]
    assert (info['width'],info['height'])==dimensions
    assert info['codec_name']=='h264' and info['sample_aspect_ratio']=='1:1'
    assert all(item.get('rotation',0)==0 for item in info.get('side_data_list',[]))
    assert _packet_times(source,time.monotonic()+10,100)==_packet_times(path,time.monotonic()+10,100)
    assert metadata['original_pts_preserved'] and metadata['feature_input'] is False
    assert metadata['source_sha256']==sha and metadata['preview_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert source.read_bytes()==before and path!=source
    timestamp=path.stat().st_mtime_ns
    assert preview_video(source,sha,bundle,settings)==(path,metadata)
    assert path.stat().st_mtime_ns==timestamp


def test_source_changed_disabled_and_frame_budget_are_blocked(tmp_path):
    source=video(tmp_path);sha=hashlib.sha256(source.read_bytes()).hexdigest();bundle,settings=fixture(tmp_path)
    with pytest.raises(ContractError) as error:preview_video(source,'a'*64,bundle,settings)
    assert error.value.code=='PREVIEW_SOURCE_CHANGED'
    with pytest.raises(ContractError) as error:preview_video(source,sha,bundle,settings.model_copy(update={'preview_enabled':False}))
    assert error.value.code=='PREVIEW_DISABLED'
    bundle.config.ingestion=bundle.config.ingestion.model_copy(update={'max_frames':5})
    with pytest.raises(ContractError) as error:preview_video(source,sha,bundle,settings)
    assert error.value.code=='PREVIEW_FRAME_LIMIT'
    assert not list(bundle.paths['datasets'].rglob('*.mp4'))


def test_preview_timeout_cleans_owned_temporary_derivative(tmp_path,monkeypatch):
    from packages.sop import preview
    source=video(tmp_path);sha=hashlib.sha256(source.read_bytes()).hexdigest();bundle,settings=fixture(tmp_path)
    original=preview._remaining;calls=0
    def expired(deadline):
        nonlocal calls
        calls+=1
        if calls>=6:raise ContractError('PREVIEW_TIMEOUT','fixture deadline')
        return original(deadline)
    monkeypatch.setattr(preview,'_remaining',expired)
    with pytest.raises(ContractError) as error:preview_video(source,sha,bundle,settings)
    assert error.value.code=='PREVIEW_TIMEOUT'
    assert not list(bundle.paths['datasets'].rglob('*.mp4'))


def test_exhausted_shared_cache_budget_does_not_publish_preview(tmp_path):
    source=video(tmp_path);sha=hashlib.sha256(source.read_bytes()).hexdigest();bundle,settings=fixture(tmp_path,preview_disk_budget_bytes=1048576)
    root=bundle.paths['datasets']/'viewing-previews'/hashlib.sha256(bundle.profile.tenant_id.encode()).hexdigest()
    root.mkdir(parents=True);(root/'retained.bin').write_bytes(b'x'*1048576)
    with pytest.raises(ContractError) as error:preview_video(source,sha,bundle,settings)
    assert error.value.code=='PREVIEW_DISK_LIMIT'
    assert (root/'retained.bin').stat().st_size==1048576
    assert not list(root.glob('*.mp4'))


from tests.test_sop_review import sop_config, sop_api, BASE


def test_registered_preview_http_range_head_lineage_and_no_pdf_conversion(sop_api,tmp_path):
    client,app,config_path=sop_api
    assets=config_path.parent.parent/'assets'
    catalog=json.loads((assets/'catalog.json').read_text());mapping=json.loads((assets/'mapping.json').read_text())
    item=next(a for a in catalog['assets'] if a['asset_id']=='bull_station_02')
    source=video(tmp_path,-90);data=source.read_bytes();sha=hashlib.sha256(data).hexdigest()
    (assets/item['path']).write_bytes(data);item.update(sha256=sha,bytes=len(data))
    next(e for e in mapping['entries'] if e['asset_id']==item['asset_id'])['source_sha256']=sha
    (assets/'catalog.json').write_text(json.dumps(catalog));(assets/'mapping.json').write_text(json.dumps(mapping))
    uri=BASE+'/media/bull_station_02/preview'
    response=client.get(uri,headers={'Range':'bytes=0-15'})
    assert response.status_code==206 and len(response.content)==16
    assert response.headers['content-type']=='video/mp4'
    assert response.headers['x-original-sha256']==sha
    assert response.headers['x-media-lineage']=='derived-for-local-viewing-only'
    assert response.headers['etag']!=f'"{sha}"'
    assert client.head(uri).status_code==200
    assert client.head(uri).content==b''
    assert client.get(uri,headers={'Range':'bytes=999999999-'}).status_code==416
    assert client.get(BASE+'/media/bull_sop/preview').json()['detail']['code']=='PREVIEW_MEDIA_KIND'
    assert client.get(uri.replace('electronics_assembly','garment_sop')).status_code==422
