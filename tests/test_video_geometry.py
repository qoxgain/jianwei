import json
import subprocess
import numpy as np
import pytest
from packages.contracts.ingestion import IngestionSettings
from packages.ingestion.video import decode_video, video_geometry, clip_tensor
from packages.config.errors import ContractError


def make_video(tmp_path, angle=0, sar='1/1'):
    base=tmp_path/'base.mp4';target=tmp_path/'rotated.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=64x48:rate=8:duration=2',
        '-vf','setsar='+sar,'-c:v','libx264','-threads','1','-y',str(base)],check=True)
    subprocess.run(['ffmpeg','-v','error','-display_rotation',str(angle),'-i',str(base),'-c','copy','-y',str(target)],check=True)
    return base,target


@pytest.mark.parametrize('angle,k',[(0,0),(90,1),(-90,-1),(180,2)])
def test_rotation_is_applied_once_pixels_pts_color_and_roi(tmp_path,angle,k):
    base,target=make_video(tmp_path,angle)
    settings=IngestionSettings(encoder_input_size=16,decode_width=64)
    frames,pts,durations,*_=decode_video(base,tmp_path,settings)
    rotated,rpts,rdurations,*tail=decode_video(target,tmp_path,settings)
    with frames,rotated:
        expected=np.rot90(frames[np.array([0,5])],k,axes=(1,2)).copy()
        np.testing.assert_array_equal(rotated[np.array([0,5])],expected)
        np.testing.assert_array_equal(pts,rpts);np.testing.assert_array_equal(durations,rdurations)
        np.testing.assert_array_equal(clip_tensor(rotated,[0,5],[.2,.1,.8,.7],16),clip_tensor(expected,[0,1],[.2,.1,.8,.7],16))
    assert tail[-1]['geometry']['roi_coordinates']=='normalized_display_after_rotation_and_sar'


@pytest.mark.parametrize('angle,shape',[(0,(48,128)),(90,(128,48)),(-90,(128,48)),(180,(48,128))])
def test_non_square_pixels_match_display_geometry(tmp_path,angle,shape):
    _,target=make_video(tmp_path,angle,'2/1')
    frames,*_=decode_video(target,tmp_path,IngestionSettings(decode_width=128,encoder_input_size=16))
    with frames:
        assert frames.shape[1:3]==shape
        assert frames[np.array([0])].shape==(1,*shape,3)


def test_industrial_display_is_portrait():
    g=video_geometry({'width':1920,'height':1080,'side_data_list':[{'rotation':-90}]},224)
    assert (g['decoded_width'],g['decoded_height'])==(126,224)


@pytest.mark.parametrize('stream',[{'width':64,'height':48,'sample_aspect_ratio':'-1:1'},
    {'width':64,'height':48,'side_data_list':[{'rotation':45}]}])
def test_unsupported_geometry_blocks(stream):
    with pytest.raises(ContractError): video_geometry(stream,224)
