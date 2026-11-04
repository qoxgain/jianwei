"""Replacements preserve both size and mtime; fingerprints must still change."""
import hashlib,os
from pathlib import Path
import pytest
from packages.ingestion import recipe
from packages.ingest_jobs.service import ImportService
from packages.config.errors import ContractError


@pytest.mark.parametrize('tool_name',['ffmpeg','ffprobe'])
def test_same_path_size_mtime_content_replacement_invalidates_recipe_and_cache(tmp_path,monkeypatch,tool_name):
    tool=tmp_path/tool_name;tool.write_text(f'#!/bin/sh\necho {tool_name} version fixture-A\n');tool.chmod(0o700)
    original=recipe.shutil.which
    monkeypatch.setattr(recipe.shutil,'which',lambda name:str(tool) if name==tool_name else original(name))
    before=recipe.production_snapshot();key=ImportService.implementation_hash();stat=tool.stat()
    tool.write_text(f'#!/bin/sh\necho {tool_name} version fixture-B\n');os.utime(tool,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    assert (tool.stat().st_size,tool.stat().st_mtime_ns)==(stat.st_size,stat.st_mtime_ns)
    after=recipe.production_snapshot()
    assert after['runtime']['tools'][tool_name]['executable_sha256']==hashlib.sha256(tool.read_bytes()).hexdigest()
    assert before['sha256']!=after['sha256'] and ImportService.implementation_hash()!=key
    assert before['runtime']['tools'][tool_name]['version']==f'{tool_name} version fixture-A'
    assert after['runtime']['tools'][tool_name]['version']==f'{tool_name} version fixture-B'


def test_replacement_during_version_probe_fails_closed(tmp_path,monkeypatch):
    p=tmp_path/'fixture';p.write_bytes(b'old')
    def replacement(*a,**kw):
        from types import SimpleNamespace
        p.write_bytes(b'new');return SimpleNamespace(stdout='fixture version\n')
    monkeypatch.setattr(recipe.subprocess,'run',replacement)
    with pytest.raises(ContractError) as error:recipe._tool(str(p),3,1)
    assert error.value.code=='FEATURE_TOOL_CHANGED'
