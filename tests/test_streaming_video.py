"""Bounded decoding must preserve real RGB/PTS and always reap its reader."""
from dataclasses import replace
from pathlib import Path
import subprocess

import numpy as np
import pytest

from packages.config.errors import ContractError
from packages.config.loader import load_bundle
from packages.contracts.ingestion import ImportRequest, IngestionSettings
from packages.ingestion import process_import
from packages.ingestion.video import decode_video, clip_tensor, FrameStore


@pytest.fixture
def video(tmp_path):
    path = tmp_path / 'pattern.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=64x48:rate=16:duration=8', '-c:v', 'mpeg4',
                    '-threads', '1', '-y', str(path)], check=True, timeout=30)
    return path


def test_streaming_preserves_original_pixels_and_overlapping_samples_without_rgb_disk(video, tmp_path):
    settings = IngestionSettings(encoder_input_size=16, max_expanded_bytes=600000)
    frames, pts, durations, scores, quality, info = decode_video(video, tmp_path, settings)
    assert len(pts) == quality['decoded_frames'] == 128
    assert int(pts[-1] + durations[-1]) == 8000
    assert np.all(scores == 1)
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(video),
        '-vsync', '0', '-threads', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'])
    expected = np.frombuffer(raw, dtype=np.uint8).reshape(128, 48, 64, 3)
    assert expected.nbytes > settings.max_expanded_bytes
    with frames:
        for start in [0, 8, 16, 80]:
            indices = np.arange(start, start + 32, 2)
            actual = clip_tensor(frames, indices, [0, 0, 1, 1], 16)
            np.testing.assert_array_equal(actual, clip_tensor(expected, indices, [0, 0, 1, 1], 16))
            assert len(frames.cache) <= 31
        assert info['decode_resources']['sampling_restarts'] == 0
    assert frames.closed and frames.reader is None
    assert not (tmp_path / 'decoded.rgb').exists()
    assert info['decode_resources']['rgb_temporary_disk_bytes'] == 0
    assert info['decode_resources']['buffer_peak_bytes'] <= settings.max_expanded_bytes


@pytest.mark.parametrize('changes, code', [
    ({'max_duration_seconds': 2}, 'VIDEO_DURATION_LIMIT'),
    ({'max_frames': 40}, 'VIDEO_FRAME_LIMIT'),
    ({'max_expanded_bytes': 1024}, 'VIDEO_DECODE_SIZE_LIMIT'),
])
def test_limits_still_reject_oversized_source_or_frame(video, tmp_path, changes, code):
    with pytest.raises(ContractError) as error:
        decode_video(video, tmp_path, IngestionSettings(**changes))
    assert error.value.code == code
    assert not (tmp_path / 'decoded.rgb').exists()


def test_wide_sampling_window_is_rejected_before_allocation(video, tmp_path):
    frames, *_ = decode_video(video, tmp_path, IngestionSettings(max_expanded_bytes=100000))
    with frames, pytest.raises(ContractError) as error:
        frames[np.array([0, 127])]
    assert error.value.code == 'VIDEO_DECODE_SIZE_LIMIT'
    assert frames.reader is None


def test_backwards_request_restarts_safely_without_reordering_pts(video, tmp_path):
    frames, *_ = decode_video(video, tmp_path, IngestionSettings())
    with frames:
        earlier = frames[np.array([0, 5])].copy()
        frames[np.array([80, 85])]
        np.testing.assert_array_equal(earlier, frames[np.array([0, 5])])
        assert frames.resources['sampling_restarts'] == 1


def test_second_pass_rejects_source_replacement(video, tmp_path):
    frames, *_ = decode_video(video, tmp_path, IngestionSettings())
    video.write_bytes(video.read_bytes() + b'changed')
    with frames, pytest.raises(ContractError) as error:
        frames[np.array([0, 5])]
    assert error.value.code == 'VIDEO_SOURCE_CHANGED'


@pytest.mark.parametrize('bad_episode', [False, True])
def test_pipeline_closes_store_on_blocked_encoder_and_episode_error(video, tmp_path, monkeypatch, bad_episode):
    instances = []
    original = FrameStore.__init__
    def tracked(self, *args, **kwargs):
        original(self, *args, **kwargs)
        instances.append(self)
    monkeypatch.setattr(FrameStore, '__init__', tracked)
    request = ImportRequest(upload_id='video', format='mp4', source_id='file_01',
        data_origin='real_video', episode_origin='manual',
        episodes=[{'episode_id': 'manual', 'domain_index': 0, 'start_ms': 0,
                   'end_ms': 9000 if bad_episode else 8000}])
    if bad_episode:
        with pytest.raises(ContractError):
            process_import(load_bundle(), 'anonymous_observation', request, video, output_dir=tmp_path/'out')
    else:
        result = process_import(load_bundle(), 'anonymous_observation', request, video, output_dir=tmp_path/'out')
        assert result['status'] == 'blocked'
    assert len(instances) == 1 and instances[0].closed
    assert instances[0].reader is None
    assert not list((tmp_path/'out').glob('decode-*'))
