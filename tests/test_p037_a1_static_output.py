"""Unknown or linked dist members cannot be served even after a correct build."""
import hashlib,json,os
from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.api.static import ReviewedStaticFiles


def test_runtime_static_root_is_not_a_media_directory(tmp_path):
 root=tmp_path/'dist';root.mkdir();index=root/'index.html';index.write_bytes(b'<h1>reviewed frontend</h1>')
 (root/'build-members.json').write_text(json.dumps({'version':'reviewed-web-build-v1','files':{'index.html':hashlib.sha256(index.read_bytes()).hexdigest()}}))
 protected=tmp_path/'protected.mp4';protected.write_bytes(b'generated protected fixture'*100)
 for name,kind in [('renamed.txt','hard'),('other.js','sym'),('unknown.type','copy')]:
  if kind=='hard':os.link(protected,root/name)
  elif kind=='sym':(root/name).symlink_to(protected)
  else:(root/name).write_bytes(protected.read_bytes())
 app=FastAPI();app.mount('/',ReviewedStaticFiles(root))
 with TestClient(app) as client:
  assert client.get('/').status_code==200
  assert client.get('/index.html',headers={'Range':'bytes=0-3'}).content==index.read_bytes()[:4]
  for name in ('renamed.txt','other.js','unknown.type'):
   for query in ('','?raw','?url','?import'):
    for method in ('GET','HEAD'):
     r=client.request(method,'/'+name+query,headers={'Range':'bytes=0-10'})
     assert r.status_code==404 and r.content!=protected.read_bytes()[:11]
  # Even a previously declared name is denied when replaced by a hardlink.
  index.unlink();os.link(protected,index)
  assert client.get('/index.html').status_code==404


def test_missing_or_linked_build_manifest_fails_closed(tmp_path):
 root=tmp_path/'dist';root.mkdir();(root/'index.html').write_bytes(b'not signed for publication')
 app=FastAPI();app.mount('/',ReviewedStaticFiles(root))
 with TestClient(app) as client:assert client.get('/').status_code==404
