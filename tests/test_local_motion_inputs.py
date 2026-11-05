"""B.1 fixed ROI, time coverage and genuine lightweight motion controls."""
import copy
from pathlib import Path
from types import SimpleNamespace as N
import numpy as np
import pytest
from pydantic import ValidationError
from packages.contracts.local_motion import LocalMotionSettings,MotionROI
from packages.local_motion.motion import crop,descriptors
from packages.local_motion.extraction import sample_indices,admission,geometry_check
from packages.config.errors import ContractError
from packages.config.loader import load_bundle
from scripts.prepare_b1 import settings


def config():return settings('garment_test','a'*64,640,480,118500)


@pytest.mark.parametrize('box',[[-.1,0,1,1],[0,0,1.1,1],[.5,0,.5,1],[0,.9,1,.5],[0,0,float('nan'),1]])
def test_invalid_roi_never_silently_clamps(box):
    with pytest.raises(ValidationError):MotionROI(id='bad',role='hands',xyxy=box)


def test_required_controls_unique_ids_primary_arm_and_sampling_budget():
    for change in [{'rois':config()['rois'][:-1]},{'sample_fps':[32]},{'primary_fps':16},{'window_ms':[1000,1000]}]:
        with pytest.raises(ValidationError):LocalMotionSettings.model_validate({**config(),**change})


def test_original_pts_nearest_and_no_duplicate_fabricated_samples():
    pts=np.arange(0,3000,40);indices=sample_indices(pts,0,2000,8,25)
    assert len(indices)==16 and len(set(indices))==16 and max(abs(pts[indices]-np.arange(16)*125))<=20
    with pytest.raises(ContractError):sample_indices(np.array([0,1000,2000]),0,2000,8,50)


def test_stationary_and_translated_texture_have_different_motion_without_semantic_claims():
    s=LocalMotionSettings.model_validate(config());rng=np.random.default_rng(12)
    image=rng.integers(0,256,(32,32,3),dtype=np.uint8);static=np.repeat(image[None],4,axis=0)
    moving=np.stack([np.roll(image,i,axis=1) for i in range(4)]);pts=np.array([0,125,250,375])
    a=descriptors(static,pts,s);b=descriptors(moving,pts,s)
    assert a['frame_difference']==a['block_motion']==0
    assert b['frame_difference']>0 and b['block_motion']>0 and b['textured_fraction']>0
    with pytest.raises(ContractError):crop(static,[0,0,.001,1])


def test_optional_setting_preserves_original_config_snapshot_hash():
    bundle=load_bundle();assert 'local_motion' not in bundle.snapshot


def test_sealed_registry_rejected_before_any_source_bytes(tmp_path,monkeypatch):
    s=LocalMotionSettings.model_validate(config());source=N(id='garment_test',type='file',uri=str(tmp_path/'must-not-read.mp4'))
    registry=N(sources=[N(split='test',source_sha256=s.source_sha256,root_source_sha256=s.source_sha256,parent_source_sha256=None)])
    bundle=N(config=N(local_motion=s,temporal=N(dataset_manifest=registry)),profile=N(sources=[source]),root=tmp_path)
    with pytest.raises(ContractError) as e:admission(bundle)
    assert e.value.code=='MOTION_SEALED_FORBIDDEN'
