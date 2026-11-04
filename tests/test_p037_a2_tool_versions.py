"""Tool-specific nonempty records; real tool snapshots and immutable legacy identities."""
from pathlib import Path
from types import SimpleNamespace as N
import pytest
from packages.ingestion import recipe
from packages.config.errors import ContractError


@pytest.mark.parametrize('name',['ffmpeg','ffprobe'])
@pytest.mark.parametrize('output',['','\n \t\n','  \r\n\r\n','unrelated tool version 1.0',
 'ffmpeg version','ffprobe version','ffmpeg version \x00','ffprobe version \x00'])
def test_blank_or_wrong_tool_record_does_not_create_a_production_identity(tmp_path,monkeypatch,name,output):
    tool=tmp_path/name;tool.write_bytes(b'generated executable fixture')
    monkeypatch.setattr(recipe.shutil,'which',lambda n:str(tool) if n==name else None)
    monkeypatch.setattr(recipe.subprocess,'run',lambda *a,**kw:N(stdout=output))
    with pytest.raises(ContractError) as e:recipe.production_snapshot()
    assert e.value.code=='FEATURE_TOOL_PROBE_FAILED'


@pytest.mark.parametrize('name',['ffmpeg','ffprobe'])
def test_strip_blank_lines_and_keep_tool_specific_version_and_libraries(tmp_path,monkeypatch,name):
    tool=tmp_path/'configured-binary';tool.write_bytes(b'generated executable fixture')
    monkeypatch.setattr(recipe.shutil,'which',lambda n:str(tool) if n==name else None)
    monkeypatch.setattr(recipe.subprocess,'run',lambda *a,**kw:N(stdout=f'\n \t\n {name} version N-123-gabcd Copyright example \n\n libavutil 59.0 / 59.0 \n'))
    x=recipe.production_snapshot();record=x['runtime']['tools'][name]
    assert record['version']==f'{name} version N-123-gabcd Copyright example'
    assert record['libraries']==['libavutil 59.0 / 59.0'] and x['version']=='p037-a2-feature-production-v5'


def test_installed_tools_produce_real_valid_version_records():
    x=recipe.runtime_snapshot()
    for name,value in x['tools'].items():
        if value.get('status')=='unavailable':continue
        assert value['version'].startswith(name+' version ') and len(value['executable_sha256'])==64
