"""Verify sample bytes and optionally decode every video frame, without inference.

Only allowlisted technical metadata is emitted, never arbitrary container tags.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'datasets/video_samples/catalog.json'


def checked_path(root, relative):
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError('asset path must stay inside the repository')
    return path


def verify_bytes(path, asset):
    if not path.is_file():
        return {'status': 'blocked', 'reason': 'missing_asset'}
    with path.open('rb') as source:
        head = source.read(512)
        if head.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
            return {'status': 'blocked', 'reason': 'lfs_pointer_not_materialized'}
        digest = hashlib.sha256(head)
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    actual = {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}
    if actual['bytes'] != asset['bytes'] or actual['sha256'] != asset['sha256']:
        return {'status': 'failed', 'reason': 'byte_identity_mismatch', **actual}
    return {'status': 'passed', **actual}


def probe_video(path, timeout):
    result = subprocess.run([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
        'stream=codec_name,width,height,avg_frame_rate,nb_frames,sample_aspect_ratio:'
        'stream_side_data=rotation:format=duration', '-of', 'json', str(path),
    ], capture_output=True, timeout=timeout, check=True)
    data = json.loads(result.stdout)
    stream = data['streams'][0]
    rotation = next((int(s['rotation']) for s in stream.get('side_data_list', [])
                     if 'rotation' in s), 0)
    width, height = int(stream['width']), int(stream['height'])
    return {
        'codec': stream['codec_name'], 'coded_width': width, 'coded_height': height,
        'rotation_degrees': rotation,
        'display_width': height if abs(rotation) % 180 == 90 else width,
        'display_height': width if abs(rotation) % 180 == 90 else height,
        'display_dimensions_basis': 'rotation only; SAR recorded separately',
        'sample_aspect_ratio': stream.get('sample_aspect_ratio'),
        'fps_ratio': stream['avg_frame_rate'],
        'declared_frames': int(stream['nb_frames']) if stream.get('nb_frames', 'N/A') != 'N/A' else None,
        'duration_seconds': float(data['format']['duration']),
    }


def full_decode(path, timeout):
    started = time.monotonic()
    result = subprocess.run([
        'ffmpeg', '-v', 'error', '-xerror', '-threads', '2', '-i', str(path),
        '-map', '0:v:0', '-an', '-sn', '-dn', '-fps_mode', 'passthrough',
        '-progress', 'pipe:1', '-nostats', '-f', 'null', '-',
    ], capture_output=True, text=True, timeout=timeout, check=True)
    progress = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    count = int(progress.get('frame', 0))
    if progress.get('progress') != 'end' or count <= 0:
        raise ValueError('decoder did not finish with a positive frame count')
    return {'status': 'passed', 'decoded_frames': count,
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'video_stream_only': True, 'writes_transcoded_media': False}


def audit_asset(asset, root, probe=False, decode=False, timeout=600):
    item = {'asset_id': asset['asset_id'], 'path': asset['path'], 'kind': asset['kind']}
    try:
        path = checked_path(root, asset['path'])
        item.update(verify_bytes(path, asset))
        if item['status'] != 'passed' or asset['kind'] != 'video':
            return item
        if probe or decode:
            media = probe_video(path, min(timeout, 60))
            item['media'] = media
            expected = asset.get('media', {})
            keys = ('codec', 'coded_width', 'coded_height', 'rotation_degrees', 'declared_frames')
            if any(key in expected and media[key] != expected[key] for key in keys):
                raise ValueError('probed technical metadata does not match catalog')
        if decode:
            item['full_decode'] = full_decode(path, timeout)
            expected_count = item['media']['declared_frames']
            if expected_count is not None and item['full_decode']['decoded_frames'] != expected_count:
                item['full_decode']['status'] = 'failed'
                raise ValueError('decoded frame count does not match declared frame count')
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        item.update(status='blocked', reason=type(error).__name__)
    except (ValueError, KeyError, IndexError, OSError, subprocess.CalledProcessError) as error:
        item.update(status='failed', reason=type(error).__name__)
    return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=CATALOG)
    parser.add_argument('--asset', action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--full-decode', action='store_true')
    parser.add_argument('--timeout-seconds', type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 3600:
        parser.error('--timeout-seconds must be in [1, 3600]')
    catalog = json.loads(args.catalog.read_text())
    if set(args.asset) - {a['asset_id'] for a in catalog['assets']}:
        parser.error('unknown --asset identifier')
    assets = [a for a in catalog['assets'] if not args.asset or a['asset_id'] in args.asset]
    results = []
    for asset in assets:
        result = audit_asset(asset, ROOT, args.probe, args.full_decode, args.timeout_seconds)
        results.append(result)
        print(f"{result['asset_id']}: {result['status']}", flush=True)
    report = {
        'schema_version': 'sample-asset-audit-v1', 'source_commit': catalog['source_commit'],
        'scope': 'Asset integrity and optional video decode only; no model or SOP accuracy claim.',
        'full_decode_requested': args.full_decode, 'assets': results,
        'counts': {status: sum(r['status'] == status for r in results)
                   for status in ('passed', 'failed', 'blocked')},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return 1 if report['counts']['failed'] else 2 if report['counts']['blocked'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
