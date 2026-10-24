"""Bounded local MP4 probing and actual RGB decoding; no inference placeholders."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import numpy as np
from packages.config.errors import ContractError


def _run(args, timeout, output=None):
    try:
        result = subprocess.run(args, stdout=output or subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False)
    except FileNotFoundError:
        raise ContractError('VIDEO_TOOLS_MISSING', '请安装 FFmpeg 与 FFprobe 后重试视频导入') from None
    except subprocess.TimeoutExpired:
        raise ContractError('VIDEO_TIMEOUT', '视频处理超过时间限制，请缩短视频后重试') from None
    if result.returncode:
        raise ContractError('VIDEO_DECODE_FAILED', '视频损坏或编码不受支持，完整解码失败')
    return result.stdout


def decode_video(path: Path, directory: Path, settings):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ContractError('VIDEO_TOOLS_MISSING', '请安装 FFmpeg 与 FFprobe 后重试视频导入')
    common = ['-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', 'mov']
    try:
        metadata = json.loads(_run(['ffprobe', *common, '-select_streams', 'v:0', '-show_entries',
            'stream=width,height,duration:format=duration', '-of', 'json', str(path)], settings.probe_timeout_seconds))
        stream = metadata['streams'][0]
        width, height = int(stream['width']), int(stream['height'])
        duration = float(stream.get('duration', metadata.get('format', {}).get('duration', 0)))
        if not np.isfinite(duration) or duration <= 0 or duration > settings.max_duration_seconds:
            raise ContractError('VIDEO_DURATION_LIMIT', '视频时长未知、为空或超过配置限制')
        if min(width, height) < 1 or width * height > settings.max_pixels:
            raise ContractError('VIDEO_RESOLUTION_LIMIT', '视频分辨率超过配置限制')
        frames_data = json.loads(_run(['ffprobe', *common, '-select_streams', 'v:0', '-read_intervals',
            f'%+#{settings.max_frames + 1}', '-show_frames', '-show_entries',
            'frame=pts_time,pkt_duration_time,duration_time', '-of', 'json', str(path)], settings.probe_timeout_seconds))
        frames = frames_data['frames']
        if len(frames) < 2 or len(frames) > settings.max_frames:
            raise ContractError('VIDEO_FRAME_LIMIT', '视频帧数为空、过少或超过配置限制')
        if any('pts_time' not in frame for frame in frames):
            raise ContractError('VIDEO_PTS_MISSING', '视频缺少原始 PTS，无法建立可信时间域')
        pts_float = np.array([float(f['pts_time']) * 1000 for f in frames], dtype=np.float64)
        if not np.isfinite(pts_float).all() or np.any(pts_float < 0) or np.any(pts_float > 2**53 - 1):
            raise ContractError('VIDEO_PTS_INVALID', '视频原始 PTS 无效')
        pts = np.rint(pts_float).astype(np.int64)
        positive_steps = np.diff(pts_float)
        positive_steps = positive_steps[positive_steps > 0]
        if len(positive_steps) == 0:
            raise ContractError('VIDEO_PTS_INVALID', '视频 PTS 无法建立有效帧间隔')
        fallback = max(1.0, float(np.median(positive_steps)))
        raw_duration = np.array([float(f.get('duration_time', f.get('pkt_duration_time', 0))) * 1000 for f in frames])
        if not np.isfinite(raw_duration).all() or np.any(raw_duration < 0):
            raise ContractError('VIDEO_PTS_INVALID', '视频帧持续时间无效')
        raw_duration = np.where(raw_duration > 0, raw_duration, fallback)
        # Round endpoints, not each duration independently: at 16 fps a 62.5ms
        # frame must not create a spurious millisecond at the end of the file.
        durations = np.maximum(1, np.rint(pts_float + raw_duration).astype(np.int64) - pts)
        size = max(settings.decode_width, settings.encoder_input_size)
        scale = min(1.0, size / max(width, height))
        out_w, out_h = max(1, round(width * scale)), max(1, round(height * scale))
        expected_size = len(frames) * out_w * out_h * 3
        if expected_size > settings.max_expanded_bytes:
            raise ContractError('VIDEO_DECODE_SIZE_LIMIT', '解码数据超过内存/磁盘限额，请缩短视频')
        raw_path = directory / 'decoded.rgb'
        _run(['ffmpeg', *common, '-xerror', '-i', str(path), '-map', '0:v:0', '-an', '-sn', '-dn',
              '-vf', f'scale={out_w}:{out_h}:flags=bilinear', '-vsync', '0', '-frames:v', str(settings.max_frames + 1),
              '-threads', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-fs', str(settings.max_expanded_bytes + 1),
              '-y', str(raw_path)], settings.probe_timeout_seconds)
        if raw_path.stat().st_size != expected_size:
            raise ContractError('VIDEO_FRAME_MISMATCH', '实际完整解码帧数与原始 PTS 不一致，拒绝生成错位特征')
        pixels = np.memmap(raw_path, dtype=np.uint8, mode='r', shape=(len(frames), out_h, out_w, 3))
        means = np.empty(len(frames), dtype=np.float64)
        stds = np.empty(len(frames), dtype=np.float64)
        for index, frame in enumerate(pixels):
            gray = frame.mean(axis=2)
            means[index], stds[index] = float(gray.mean()), float(gray.std())
        good = (means >= settings.quality_luma_min) & (means <= settings.quality_luma_max) & (stds >= settings.quality_std_min)
        quality = {'method': 'decoded_luma_and_contrast_v1', 'decoded_frames': len(frames),
                   'dark_frames': int((means < settings.quality_luma_min).sum()),
                   'bright_frames': int((means > settings.quality_luma_max).sum()),
                   'low_contrast_frames': int((stds < settings.quality_std_min).sum()),
                   'score': float(good.mean()), 'semantic_accuracy': None}
        return pixels, pts, durations, good.astype(np.float32), quality, {
            'width': width, 'height': height, 'decoded_width': out_w, 'decoded_height': out_h,
            'container_duration_ms': int(round(duration * 1000))}
    except ContractError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, OSError, OverflowError):
        raise ContractError('VIDEO_INVALID', '无法解析有效的 MP4 视频与原始 PTS') from None


def clip_tensor(pixels, indices, roi, input_size):
    """Fixed ROI after bilinear bounded decode, nearest resize, RGB [0,1]."""
    height, width = pixels.shape[1:3]
    x1, y1, x2, y2 = roi
    left, top = int(x1*width), int(y1*height)
    right, bottom = max(left+1, int(np.ceil(x2*width))), max(top+1, int(np.ceil(y2*height)))
    crop = pixels[np.asarray(indices), top:bottom, left:right, :]
    ys = np.linspace(0, crop.shape[1]-1, input_size).round().astype(int)
    xs = np.linspace(0, crop.shape[2]-1, input_size).round().astype(int)
    resized = crop[:, ys][:, :, xs]
    return np.ascontiguousarray(resized.transpose(0, 3, 1, 2)[None], dtype=np.float32) / 255.0
