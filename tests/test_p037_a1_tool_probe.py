"""Only initially absent tools are unavailable; post-hash probe failures are fatal."""
import subprocess
from pathlib import Path
from types import SimpleNamespace as N
import pytest
from packages.ingestion import recipe
from packages.config.errors import ContractError
from packages.ingest_jobs.service import ImportService


@pytest.mark.parametrize('failure',['gone','permission','nonzero','timeout','after_probe_missing','empty_output'])
def test_post_hash_probe_failure_blocks_snapshot_and_cache_key(tmp_path,monkeypatch,failure):
 tool=tmp_path/'ffmpeg';tool.write_bytes(b'generated executable fixture')
 monkeypatch.setattr(recipe.shutil,'which',lambda name:str(tool) if name=='ffmpeg' else None)
 first_hash=[];original=recipe.digest
 def digest(path):first_hash.append(Path(path).name);return original(path)
 monkeypatch.setattr(recipe,'digest',digest)
 def probe(*a,**kw):
  assert 'ffmpeg' in first_hash
  if failure=='gone':tool.unlink();raise FileNotFoundError('generated')
  if failure=='permission':raise PermissionError('generated')
  if failure=='nonzero':raise subprocess.CalledProcessError(1,a[0])
  if failure=='timeout':raise subprocess.TimeoutExpired(a[0],5)
  if failure=='after_probe_missing':tool.unlink()
  return N(stdout='' if failure=='empty_output' else 'generated version\n')
 monkeypatch.setattr(recipe.subprocess,'run',probe)
 with pytest.raises(ContractError) as e:ImportService.implementation_hash()
 assert e.value.code=='FEATURE_TOOL_PROBE_FAILED'


def test_initial_missing_tools_remain_legal_snapshot_for_nonvideo_inputs(monkeypatch):
 monkeypatch.setattr(recipe.shutil,'which',lambda _:None)
 x=recipe.production_snapshot()
 assert x['runtime']['tools']=={'ffmpeg':{'status':'unavailable'},'ffprobe':{'status':'unavailable'}}
 assert x['version']==recipe.VERSION
