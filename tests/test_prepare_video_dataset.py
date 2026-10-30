"""Isolated configuration creation: evidence binding and no overwrites."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest
import yaml

from packages.encoders.pretrained import CHECKPOINT_SHA256, PREPROCESSING_VERSION
from scripts import prepare_video_dataset as factory

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def inputs(tmp_path,monkeypatch):
    root=tmp_path/'repo'
    for name in ('config','knowledge'):
        shutil.copytree(ROOT/name,root/name)
    catalog=root/'datasets/video_samples/catalog.json';catalog.parent.mkdir(parents=True)
    shutil.copyfile(ROOT/'datasets/video_samples/catalog.json',catalog)
    model=root/'artifacts/models/r3d18-kinetics400-v1.onnx';model.parent.mkdir(parents=True)
    model.write_bytes(b'configuration unit fixture only; never an inference model')
    report=root/'export.json'
    report.write_text(json.dumps({'passed':True,'checkpoint_sha256':CHECKPOINT_SHA256,
        'onnx_sha256':hashlib.sha256(model.read_bytes()).hexdigest(),
        'preprocessing_version':PREPROCESSING_VERSION,'frozen':True,'training':False}))
    monkeypatch.setattr(factory,'PROJECT_ROOT',root)
    return root,report,tmp_path/'runtime'


def test_explicit_research_preserves_root_sampling_and_unknown_provenance(inputs):
    root,report,out=inputs
    path=factory.prepare(out,research=True,encoder_report=report)
    config=yaml.safe_load(path.read_text());original=yaml.safe_load((root/'config/system.yaml').read_text())
    assert config['video']==original['video'] and config['learning']==original['learning']
    assert config['encoder']['weights_sha256']==json.loads(report.read_text())['onnx_sha256']
    assert len(config['profiles']['demo']['sources'])==11
    assert all(s['capture_identity']['review_status']=='pending' for s in config['profiles']['demo']['sources'][2:])
    assert factory.prepare(out,research=True,encoder_report=report)==path
    assert yaml.safe_load((root/'config/system.yaml').read_text())==original


def test_default_engineering_profile_is_compatible_and_cannot_be_silently_replaced(inputs):
    _,report,out=inputs
    path=factory.prepare(out,encoder_report=report)
    before=path.read_bytes()
    assert yaml.safe_load(before)['video']['stride_seconds']==2
    with pytest.raises(ValueError,match='overwrite'):
        factory.prepare(out,research=True,encoder_report=report)
    assert path.read_bytes()==before


def test_changed_knowledge_blocks_before_any_output_is_written(inputs):
    _,report,out=inputs
    altered=out/'knowledge/labels.yaml';altered.parent.mkdir(parents=True)
    altered.write_text('user revision')
    with pytest.raises(ValueError,match='knowledge'):
        factory.prepare(out,research=True,encoder_report=report)
    assert sorted(p for p in out.rglob('*') if p.is_file())==[altered]
    assert altered.read_text()=='user revision'


@pytest.mark.parametrize('field,value',[('passed',False),('onnx_sha256','0'*64),('checkpoint_sha256','0'*64),
                                      ('preprocessing_version','other'),('frozen',False),('training',True)])
def test_invalid_export_evidence_does_not_create_runtime(inputs,field,value):
    _,report,out=inputs
    data=json.loads(report.read_text());data[field]=value;report.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='encoder'):
        factory.prepare(out,research=True,encoder_report=report)
    assert not out.exists()


def test_incompatible_research_grid_is_rejected_without_relaxing_tolerance(inputs):
    root,report,out=inputs;path=root/'config/system.yaml'
    config=yaml.safe_load(path.read_text());config['video']['stride_seconds']=2
    path.write_text(yaml.safe_dump(config))
    before=path.read_bytes()
    with pytest.raises(ValueError,match='incompatible'):
        factory.prepare(out,research=True,encoder_report=report)
    assert not out.exists() and path.read_bytes()==before
