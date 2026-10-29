"""Bounded local MP4 probing and actual RGB decoding; no inference placeholders."""
from pathlib import Path
import json
import shutil
import subprocess
import os
import selectors
import time
import math
import re
from fractions import Fraction
import numpy as np
from packages.config.errors import ContractError


GEOMETRY_VERSION = 'display-square-pixels-v1'


def _notify(progress, stage, completed=0, total=None, unit=None):
    if progress is not None:
        progress(stage, completed=completed, total=total, unit=unit)


def _run(args, timeout, output=None, progress=None):
    """Poll an owned probe process so cancellation also interrupts slow HEVC probes."""
    started = time.monotonic()
    try:
        process = subprocess.Popen(args, stdout=output or subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise ContractError('VIDEO_TOOLS_MISSING', '请安装 FFmpeg 与 FFprobe 后重试视频导入') from None
    try:
        while True:
            _notify(progress, 'probe')
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise ContractError('VIDEO_TIMEOUT', '视频探测超过时间限制，请缩短视频或调整有界探测预算')
            try:
                stdout, _ = process.communicate(timeout=min(.25, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise ContractError('VIDEO_DECODE_FAILED', '视频损坏或编码不受支持，完整解码失败')
        return stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def _right_angle(value):
    angle = float(value)
    if not math.isfinite(angle) or abs(angle / 90 - round(angle / 90)) > 1e-5:
        raise ContractError('VIDEO_GEOMETRY_UNSUPPORTED', '仅支持 0、±90、180 度显示旋转；其他矩阵须人工检查')
    normalized = int(round(angle / 90) * 90) % 360
    return -90 if normalized == 270 else normalized


def video_geometry(stream, long_edge):
    """Coded pixels -> display orientation/SAR -> square-pixel bounded RGB.

    Rotation is FFprobe's counterclockwise display angle. FFmpeg autorotation
    is explicitly disabled; our right-angle filter is the only orientation step.
    ROI coordinates are normalized in the resulting display image, not in coded
    camera pixels. Unsupported mirrors, scaling/shear or arbitrary rotations are
    blocked instead of being silently discarded.
    """
    width, height = int(stream['width']), int(stream['height'])
    if min(width, height, long_edge) <= 0:
        raise ContractError('VIDEO_GEOMETRY_INVALID', '视频显示尺寸无效')
    raw_sar = stream.get('sample_aspect_ratio')
    if raw_sar in (None, '', 'N/A', '0:1'):
        sar, origin = Fraction(1), 'assumed_square_unspecified'
    else:
        try:
            sar = Fraction(str(raw_sar).replace(':', '/'))
            if sar <= 0 or sar.numerator > 1000000 or sar.denominator > 1000000:
                raise ValueError
        except (ValueError, ZeroDivisionError, OverflowError):
            raise ContractError('VIDEO_GEOMETRY_INVALID', '视频像素宽高比无效') from None
        origin = 'stream'
    rotation = None
    for item in stream.get('side_data_list', []):
        if 'rotation' not in item and 'displaymatrix' not in item:
            continue
        values = None
        if 'displaymatrix' in item:
            lines = [line.split(':', 1)[1] for line in item['displaymatrix'].splitlines() if ':' in line]
            values = [int(value) for line in lines for value in re.findall(r'-?\d+', line)]
            if len(values) != 9:
                raise ContractError('VIDEO_GEOMETRY_INVALID', '视频显示矩阵无法解析')
        angle = _right_angle(item.get('rotation', math.degrees(math.atan2(values[3], values[0])) if values else 0))
        if rotation is not None and rotation != angle:
            raise ContractError('VIDEO_GEOMETRY_INVALID', '视频存在冲突的显示旋转')
        rotation = angle
        if values:
            radians = math.radians(angle)
            c, s = round(math.cos(radians) * 65536), round(math.sin(radians) * 65536)
            expected = [c, -s, 0, s, c, 0, 0, 0, 1 << 30]
            if any(abs(a - b) > 8 for a, b in zip(values, expected)):
                raise ContractError('VIDEO_GEOMETRY_UNSUPPORTED', '显示矩阵含镜像、平移或缩放，当前旋转契约不支持')
    tag = stream.get('tags', {}).get('rotate')
    if tag is not None:
        tag_angle = _right_angle(tag)
        if rotation is not None and rotation != tag_angle:
            raise ContractError('VIDEO_GEOMETRY_INVALID', '视频旋转标签与显示矩阵冲突')
        rotation = tag_angle
    rotation = rotation or 0
    display_w, display_h = Fraction(width) * sar, Fraction(height)
    if rotation in (-90, 90):
        display_w, display_h = display_h, display_w
    scale = min(Fraction(1), Fraction(long_edge) / max(display_w, display_h))
    out_w, out_h = max(1, round(display_w * scale)), max(1, round(display_h * scale))
    return {'version': GEOMETRY_VERSION, 'coded_width': width, 'coded_height': height,
            'sample_aspect_ratio': f'{sar.numerator}:{sar.denominator}', 'sar_origin': origin,
            'rotation_degrees_ccw': rotation, 'display_width': float(display_w), 'display_height': float(display_h),
            'decoded_width': out_w, 'decoded_height': out_h,
            'roi_coordinates': 'normalized_display_after_rotation_and_sar',
            'transform': 'ffmpeg_noautorotate_then_right_angle_then_bilinear_scale_setsar1'}


def geometry_filter(geometry):
    rotation = geometry['rotation_degrees_ccw']
    orientation = {0: [], 90: ['transpose=cclock'], -90: ['transpose=clock'], 180: ['hflip', 'vflip']}[rotation]
    return ','.join([*orientation, f"scale={geometry['decoded_width']}:{geometry['decoded_height']}:flags=bilinear", 'setsar=1'])



class _RGBReader:
    """One bounded pipe frame at a time, with a deadline covering the whole pass."""
    def __init__(self, path, width, height, settings, geometry=None, progress=None, stage="decode"):
        self.frame_bytes = width * height * 3
        self.shape = (height, width, 3)
        self.remaining_seconds = float(getattr(settings, "decode_timeout_seconds", None) or settings.probe_timeout_seconds)
        self.progress, self.stage = progress, stage
        self.process = subprocess.Popen([
            'ffmpeg', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', 'mov',
            '-xerror', '-noautorotate', '-i', str(path), '-map', '0:v:0', '-an', '-sn', '-dn',
            '-vf', geometry_filter(geometry) if geometry is not None else f'scale={width}:{height}:flags=bilinear,setsar=1', '-vsync', '0',
            '-frames:v', str(settings.max_frames + 1), '-threads', '1',
            '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def read(self):
        started = time.monotonic()
        self.deadline = started + self.remaining_seconds
        try:
            return self._read()
        finally:
            # Waiting for an encoder between samples is not decoder time.
            self.remaining_seconds -= time.monotonic() - started

    def _read(self):
        data = bytearray(self.frame_bytes)
        view = memoryview(data)
        offset = 0
        while offset < self.frame_bytes:
            _notify(self.progress, self.stage)
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise ContractError('VIDEO_TIMEOUT', '视频解码超过时间限制，请缩短视频或调整有界解码预算')
            if not self.selector.select(min(.25, remaining)):
                continue
            chunk = os.read(self.process.stdout.fileno(), self.frame_bytes - offset)
            if not chunk:
                remaining = max(.01, self.deadline - time.monotonic())
                try:
                    code = self.process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    raise ContractError('VIDEO_TIMEOUT', '视频处理超过时间限制，请缩短视频后重试') from None
                if code:
                    raise ContractError('VIDEO_DECODE_FAILED', '视频损坏或编码不受支持，完整解码失败')
                if offset:
                    raise ContractError('VIDEO_FRAME_MISMATCH', '实际完整解码帧数与原始 PTS 不一致')
                return None
            view[offset:offset + len(chunk)] = chunk
            offset += len(chunk)
        return np.frombuffer(data, dtype=np.uint8).reshape(self.shape)

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class FrameStore:
    """Decode by original frame index, retaining only the current clip window.

    The first pass already checked every source frame. The lazy second pass
    never writes RGB to disk. Overlapping windows reuse retained frames; a
    backwards caller explicitly restarts decoding instead of returning wrong
    pixels. Pipeline visits episodes in source order to avoid such restarts.
    """
    def __init__(self, path, count, width, height, settings, geometry=None, progress=None):
        self.path, self.settings = Path(path), settings
        self.geometry, self.progress = geometry, progress
        self.shape = (count, height, width, 3)
        self.frame_bytes = width * height * 3
        self.reader = None
        self.cache = {}
        self.cursor = 0
        self.closed = False
        stat = self.path.stat()
        self.identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        self.resources = {'strategy': 'two_pass_bounded_rgb_v1',
                          'quality_pass_frames': count, 'sampling_pass_frames': 0,
                          'rgb_temporary_disk_bytes': 0, 'buffer_peak_bytes': self.frame_bytes * 3,
                          'sampling_restarts': 0,
                          'buffer_peak_bytes_kind': 'conservative_allocation_bound_not_rss'}

    def require_budget(self, indices, extra_bytes=0):
        indices = np.asarray(indices, dtype=np.int64)
        if not indices.size or indices.ndim != 1 or np.any(indices < 0) or np.any(indices >= self.shape[0]):
            raise ContractError('VIDEO_FRAME_INDEX', '视频采样帧索引无效')
        # Cache window + pipe frame + read buffer + selected copy + caller tensor.
        required = (int(indices.max() - indices.min()) + 3 + len(indices)) * self.frame_bytes + extra_bytes
        if required > self.settings.max_expanded_bytes:
            raise ContractError('VIDEO_DECODE_SIZE_LIMIT', '当前采样窗口超过内存限额，请减少片段帧数或长度')
        self.resources['buffer_peak_bytes'] = max(self.resources['buffer_peak_bytes'], required)

    def __getitem__(self, key):
        if self.closed:
            raise ContractError('VIDEO_CLOSED', '视频帧读取器已经关闭')
        indices, *slices = key if isinstance(key, tuple) else (key,)
        indices = np.asarray(indices, dtype=np.int64)
        self.require_budget(indices)
        left, right = int(indices.min()), int(indices.max())
        if left < self.cursor and left not in self.cache:
            if self.reader:
                self.reader.close()
            self.reader, self.cursor, self.cache = None, 0, {}
            self.resources['sampling_restarts'] += 1
        self.cache = {i: frame for i, frame in self.cache.items() if i >= left}
        if self.reader is None:
            stat = self.path.stat()
            if self.identity != (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns):
                raise ContractError('VIDEO_SOURCE_CHANGED', '视频在解码期间发生变化，请重新导入')
            self.reader = _RGBReader(self.path, self.shape[2], self.shape[1], self.settings, self.geometry, self.progress, 'sampling_decode')
        while self.cursor <= right:
            frame = self.reader.read()
            if frame is None:
                raise ContractError('VIDEO_FRAME_MISMATCH', '实际解码帧数与原始 PTS 不一致')
            if self.cursor >= left:
                self.cache[self.cursor] = frame
            self.cursor += 1
            self.resources['sampling_pass_frames'] += 1
            if self.cursor % 100 == 0 or self.cursor == right + 1:
                _notify(self.progress, 'sampling_decode', self.cursor, self.shape[0], 'frames')
        result = np.stack([self.cache[int(i)] for i in indices])
        return result[(slice(None), *slices)] if slices else result

    def close(self):
        if self.reader is not None:
            self.reader.close()
            self.reader = None
        self.cache.clear()
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def close_frames(pixels):
    """Also accepts legacy ndarray test doubles and external memmaps."""
    if hasattr(pixels, 'close'):
        pixels.close()
    elif getattr(pixels, '_mmap', None) is not None:
        pixels._mmap.close()

def decode_video(path: Path, directory: Path, settings, progress=None):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ContractError('VIDEO_TOOLS_MISSING', '请安装 FFmpeg 与 FFprobe 后重试视频导入')
    common = ['-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', 'mov']
    started = time.monotonic()
    try:
        metadata = json.loads(_run(['ffprobe', *common, '-select_streams', 'v:0', '-show_entries',
            'stream=width,height,sample_aspect_ratio,duration:stream_side_data=rotation,displaymatrix:stream_tags=rotate:format=duration', '-of', 'json', str(path)], settings.probe_timeout_seconds, progress=progress))
        stream = metadata['streams'][0]
        width, height = int(stream['width']), int(stream['height'])
        duration = float(stream.get('duration', metadata.get('format', {}).get('duration', 0)))
        if not np.isfinite(duration) or duration <= 0 or duration > settings.max_duration_seconds:
            raise ContractError('VIDEO_DURATION_LIMIT', '视频时长未知、为空或超过配置限制')
        if min(width, height) < 1 or width * height > settings.max_pixels:
            raise ContractError('VIDEO_RESOLUTION_LIMIT', '视频分辨率超过配置限制')
        frames_data = json.loads(_run(['ffprobe', *common, '-select_streams', 'v:0', '-read_intervals',
            f'%+#{settings.max_frames + 1}', '-show_frames', '-show_entries',
            'frame=pts_time,pkt_duration_time,duration_time', '-of', 'json', str(path)], settings.probe_timeout_seconds, progress=progress))
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
        geometry = video_geometry(stream, max(settings.decode_width, settings.encoder_input_size))
        out_w, out_h = geometry['decoded_width'], geometry['decoded_height']
        probe_finished = time.monotonic()
        _notify(progress, 'decode', 0, len(frames), 'frames')
        frame_bytes = out_w * out_h * 3
        # A frame, its pipe buffer and float64 luma workspace must fit. The
        # configured budget applies to active memory, not the entire video.
        if frame_bytes * 3 + out_w * out_h * 16 > settings.max_expanded_bytes:
            raise ContractError('VIDEO_DECODE_SIZE_LIMIT', '单帧解码数据超过内存限额')
        means = np.empty(len(frames), dtype=np.float64)
        stds = np.empty(len(frames), dtype=np.float64)
        with _RGBReader(path, out_w, out_h, settings, geometry, progress) as reader:
            for index in range(len(frames)):
                frame = reader.read()
                if frame is None:
                    raise ContractError('VIDEO_FRAME_MISMATCH', '实际完整解码帧数与原始 PTS 不一致')
                gray = frame.mean(axis=2)
                means[index], stds[index] = float(gray.mean()), float(gray.std())
                if index % 100 == 0 or index + 1 == len(frames):
                    _notify(progress, 'decode', index + 1, len(frames), 'frames')
            if reader.read() is not None:
                raise ContractError('VIDEO_FRAME_MISMATCH', '实际完整解码帧数与原始 PTS 不一致')
        pixels = FrameStore(path, len(frames), out_w, out_h, settings, geometry, progress)
        pixels.resources['buffer_peak_bytes'] = frame_bytes * 3 + out_w * out_h * 16
        good = (means >= settings.quality_luma_min) & (means <= settings.quality_luma_max) & (stds >= settings.quality_std_min)
        quality = {'method': 'decoded_luma_and_contrast_v1', 'decoded_frames': len(frames),
                   'dark_frames': int((means < settings.quality_luma_min).sum()),
                   'bright_frames': int((means > settings.quality_luma_max).sum()),
                   'low_contrast_frames': int((stds < settings.quality_std_min).sum()),
                   'score': float(good.mean()), 'semantic_accuracy': None}
        return pixels, pts, durations, good.astype(np.float32), quality, {
            'width': width, 'height': height, 'decoded_width': out_w, 'decoded_height': out_h,
            'container_duration_ms': int(round(duration * 1000)), 'decode_resources': pixels.resources,
            'geometry': geometry, **({'stage_timings_seconds': {
                'probe': round(probe_finished - started, 6),
                'decode': round(time.monotonic() - probe_finished, 6)}} if progress else {})}
    except ContractError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, OSError, OverflowError):
        raise ContractError('VIDEO_INVALID', '无法解析有效的 MP4 视频与原始 PTS') from None


def clip_tensor(pixels, indices, roi, input_size):
    """Fixed ROI after bilinear bounded decode, nearest resize, RGB [0,1]."""
    if isinstance(pixels, FrameStore):
        # Float conversion and division can overlap; include both tensors.
        pixels.require_budget(indices, len(indices) * input_size * input_size * 3 * 4 * 2)
    height, width = pixels.shape[1:3]
    x1, y1, x2, y2 = roi
    left, top = int(x1*width), int(y1*height)
    right, bottom = max(left+1, int(np.ceil(x2*width))), max(top+1, int(np.ceil(y2*height)))
    crop = pixels[np.asarray(indices), top:bottom, left:right, :]
    ys = np.linspace(0, crop.shape[1]-1, input_size).round().astype(int)
    xs = np.linspace(0, crop.shape[2]-1, input_size).round().astype(int)
    resized = crop[:, ys][:, :, xs]
    return np.ascontiguousarray(resized.transpose(0, 3, 1, 2)[None], dtype=np.float32) / 255.0
