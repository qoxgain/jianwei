"""Compare actual old full-RGB and bounded decoding on the same source.

Run: PYTHONPATH=. .venv/bin/python scripts/benchmark_video_decode.py
Old code is read-only from pinned baseline 6f33b2b. Both workers are isolated;
Linux /proc samples aggregate parent + FFmpeg RSS (not unique/PSS memory).
No source is altered and no semantic/research accuracy is measured.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import numpy as np
from packages.config.loader import load_bundle, PROJECT_ROOT
from packages.contracts.ingestion import ImportRequest
from packages.ingestion.time import time_domains, build_episodes

BASELINE = '6f33b2b'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def worker(mode, source, output, directory):
    from packages.ingestion import video
    if mode == 'baseline':
        code = subprocess.check_output(['git', 'show', f'{BASELINE}:packages/ingestion/video.py'], cwd=PROJECT_ROOT)
        module_path = directory / 'baseline_video.py'
        module_path.write_bytes(code)
        spec = importlib.util.spec_from_file_location('baseline_video', module_path)
        video = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(video)
    bundle = load_bundle()
    settings = bundle.config.ingestion.model_copy(deep=True)
    if mode == 'baseline':
        settings.max_expanded_bytes = 536870912
    started = time.monotonic()
    pixels, pts, durations, scores, quality, info = video.decode_video(source, directory, settings)
    request = ImportRequest(upload_id='benchmark', format='mp4', source_id='file_01', data_origin='real_video')
    domains, gaps, _ = time_domains(pts, durations, settings, [])
    episodes = build_episodes(bundle.scenes['anonymous_observation'], request, domains)
    digest = hashlib.sha256()
    clips = []
    try:
        for episode in episodes:
            if not episode['complete']:
                continue
            domain = domains[episode['domain_index']]
            domain_indices = np.arange(domain['first_row'], domain['last_row']+1)
            domain_pts = pts[domain_indices]
            clip_ms = round(bundle.config.video.clip_seconds * 1000)
            stride_ms = round(bundle.config.video.stride_seconds * 1000)
            for start in range(episode['start_ms'], episode['end_ms']-clip_ms+1, stride_ms):
                end = start + clip_ms
                left, right = np.searchsorted(domain_pts, [start, end], side='left')
                indices = domain_indices[left:right]
                targets = np.linspace(start, end, bundle.config.video.frames_per_clip, endpoint=False)
                selected = indices[np.abs(pts[indices, None]-targets[None, :]).argmin(axis=0)]
                missing = 1-len(np.unique(selected))/len(selected)
                score = float(scores[selected].mean()) * (1-missing)
                if missing > bundle.config.video.missing_frame_fraction_max or score < bundle.config.video.quality_min:
                    continue
                tensor = video.clip_tensor(pixels, selected, [0, 0, 1, 1], settings.encoder_input_size)
                digest.update(tensor.tobytes())
                clips.append({'start_ms': start, 'end_ms': end, 'quality': score, 'indices': selected.tolist()})
        raw = directory / 'decoded.rgb'
        report = {'mode': mode, 'source_sha256': sha(source), 'elapsed_seconds': time.monotonic()-started,
                  'configured_expanded_budget': settings.max_expanded_bytes,
                  'quality': quality, 'video': info, 'domains': domains, 'gaps': gaps, 'episodes': episodes,
                  'pts_sha256': hashlib.sha256(pts.tobytes()).hexdigest(),
                  'durations_sha256': hashlib.sha256(durations.tobytes()).hexdigest(),
                  'frame_scores_sha256': hashlib.sha256(scores.tobytes()).hexdigest(),
                  'clip_tensors_sha256': digest.hexdigest(), 'eligible_clips': len(clips), 'clips': clips,
                  'rgb_temporary_disk_bytes': raw.stat().st_size if raw.exists() else 0}
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
    finally:
        if hasattr(pixels, 'close'):
            pixels.close()
        elif getattr(pixels, '_mmap', None) is not None:
            pixels._mmap.close()


def rss_tree(pid):
    # Some managed /proc mounts omit task/*/children. Stat still exposes each
    # process's parent and resident pages without reading environment/secrets.
    table = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            table[int(entry.name)] = (int(fields[1]), int(fields[21]) * os.sysconf('SC_PAGE_SIZE'))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    total, pending = 0, [pid]
    while pending:
        current = pending.pop()
        total += table.get(current, (0, 0))[1]
        pending.extend(child for child, (parent, _) in table.items() if parent == current)
    return total


def capture(source, output):
    results = {}
    for mode in ('baseline', 'bounded'):
        with tempfile.TemporaryDirectory(prefix=f'jianwei-{mode}-') as temp:
            directory = Path(temp)
            report = directory / 'result.json'
            command = [sys.executable, str(Path(__file__).resolve()), '--worker', mode,
                       '--source', str(source.resolve()), '--output', str(report), '--work', str(directory)]
            process = subprocess.Popen(command, cwd=PROJECT_ROOT)
            peak = 0
            while process.poll() is None:
                peak = max(peak, rss_tree(process.pid))
                time.sleep(.01)
            if process.returncode:
                raise RuntimeError(f'{mode} failed: {process.returncode}')
            results[mode] = json.loads(report.read_text())
            results[mode]['sampled_tree_rss_peak_bytes'] = peak
    checks = {key: results['baseline'][key] == results['bounded'][key] for key in (
        'source_sha256', 'quality', 'domains', 'gaps', 'episodes', 'pts_sha256',
        'durations_sha256', 'frame_scores_sha256', 'clip_tensors_sha256', 'clips', 'eligible_clips')}
    report = {'baseline_commit': BASELINE, 'source_file': source.name,
              'rss_method': '10ms Linux /proc stat resident pages sum over worker + child processes; shared pages may be counted twice',
              'checks': checks, 'all_equivalent': all(checks.values()), 'runs': results,
              'semantic_accuracy': None, 'independent_original_sources': 1}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'all_equivalent': report['all_equivalent'], 'runs': {
        name: {key: data[key] for key in ('eligible_clips', 'elapsed_seconds', 'rgb_temporary_disk_bytes',
              'sampled_tree_rss_peak_bytes')} for name, data in results.items()}}, indent=2))
    if not report['all_equivalent']:
        raise RuntimeError('Decoder comparison failed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=PROJECT_ROOT/'datasets/video_samples/garment_quality/videos/test.mp4')
    parser.add_argument('--output', type=Path, default=PROJECT_ROOT/'docs/delivery/p0.3.1/decode-comparison.json')
    parser.add_argument('--worker', choices=['baseline', 'bounded'])
    parser.add_argument('--work', type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.source, args.output, args.work)
    else:
        capture(args.source, args.output)
