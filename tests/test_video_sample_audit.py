import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts.audit_video_samples import audit_asset, checked_path, full_decode, probe_video, verify_bytes

ROOT = Path(__file__).resolve().parents[1]


def asset(data=b'original bytes'):
    return {'asset_id': 'test', 'path': 'sample.bin', 'kind': 'normative_document',
            'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def test_materialized_bytes_and_missing_asset(tmp_path):
    path = tmp_path/'sample.bin'
    assert verify_bytes(path, asset())['reason'] == 'missing_asset'
    path.write_bytes(b'original bytes')
    assert audit_asset(asset(), tmp_path)['status'] == 'passed'


def test_lfs_pointer_is_blocked_even_if_pointer_hash_matches(tmp_path):
    data = b'version https://git-lfs.github.com/spec/v1\noid sha256:' + b'0'*64 + b'\nsize 123\n'
    path = tmp_path/'sample.bin'
    path.write_bytes(data)
    result = audit_asset(asset(data), tmp_path)
    assert result['status'] == 'blocked'
    assert result['reason'] == 'lfs_pointer_not_materialized'


@pytest.mark.parametrize('changed', [b'changed! bytes', b'changed length'])
def test_changed_bytes_fail(tmp_path, changed):
    (tmp_path/'sample.bin').write_bytes(changed)
    assert audit_asset(asset(), tmp_path)['reason'] == 'byte_identity_mismatch'


@pytest.mark.parametrize('relative', ['../escape.mp4', '/absolute.mp4'])
def test_asset_path_cannot_escape_repository(tmp_path, relative):
    with pytest.raises(ValueError):
        checked_path(tmp_path, relative)


def test_rotated_probe_has_display_dimensions_and_omits_tags(monkeypatch):
    payload = {'streams': [{'codec_name': 'hevc', 'width': 1920, 'height': 1080,
        'avg_frame_rate': '30000/1001', 'nb_frames': '9',
        'tags': {'location': 'private'}, 'side_data_list': [{'rotation': -90}]}],
        'format': {'duration': '0.3', 'tags': {'location': 'private'}}}
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(payload)))
    result = probe_video(Path('sample.mp4'), 10)
    assert (result['display_width'], result['display_height']) == (1080, 1920)
    assert 'private' not in json.dumps(result)


def test_incomplete_decode_is_not_success(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, 'frame=8\nprogress=continue\n'))
    with pytest.raises(ValueError):
        full_decode(Path('sample.mp4'), 10)


def test_decode_count_disagreement_fails(tmp_path, monkeypatch):
    import scripts.audit_video_samples as module
    (tmp_path/'sample.bin').write_bytes(b'original bytes')
    record = {**asset(), 'kind': 'video'}
    monkeypatch.setattr(module, 'probe_video', lambda *a: {'declared_frames': 9})
    monkeypatch.setattr(module, 'full_decode', lambda *a: {'status': 'passed', 'decoded_frames': 8})
    result = audit_asset(record, tmp_path, decode=True)
    assert result['status'] == result['full_decode']['status'] == 'failed'


def test_catalog_is_not_a_sealed_training_manifest():
    catalog = json.loads((ROOT/'datasets/video_samples/catalog.json').read_text())
    assert catalog['is_training_dataset_manifest'] is False
    assert len({a['asset_id'] for a in catalog['assets']}) == 10
    videos = [a for a in catalog['assets'] if a['kind'] == 'video']
    assert len(videos) == 9
    for item in videos:
        assert item['provenance']['split'] == 'unassigned'
        assert item['provenance']['review_status'] == 'pending'
        assert item['provenance']['capture_session_id'] is None
        assert item['media']['creation_time_is_verified_capture_time'] is False
