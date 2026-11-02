import json
from types import SimpleNamespace
import httpx
import pytest
from scripts.validate_video_dataset import poll_import

@pytest.mark.parametrize('status,payload',[(500,{'detail':'fixture database unavailable'}),(404,{'detail':'missing'}),(200,{'detail':'invalid schema'})])
def test_poll_preserves_http_or_schema_failure_instead_of_keyerror(tmp_path,status,payload):
    response=httpx.Response(status,json=payload,request=httpx.Request('GET','http://fixture/import'))
    path=tmp_path/'failure.json'
    with pytest.raises((httpx.HTTPStatusError,ValueError)):
        poll_import(SimpleNamespace(get=lambda u:response),'/import',path)
    saved=json.loads(path.read_text());assert saved['http_status']==status

def test_successful_progress_is_preserved(tmp_path):
    data={'status':'running','progress':{'stage':'encode','completed':3}}
    response=httpx.Response(200,json=data,request=httpx.Request('GET','http://fixture/import'))
    assert poll_import(SimpleNamespace(get=lambda u:response),'/import',tmp_path/'none.json')==data
