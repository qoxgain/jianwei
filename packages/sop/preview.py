"""Bounded local H.264 viewing derivatives; never inputs to feature extraction.

Original timestamps, display orientation and the original SHA remain explicit.
No media is published by this module. The configured data directory owns caches.
"""
from __future__ import annotations
from contextlib import contextmanager
from fractions import Fraction
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from packages.config.errors import ContractError
from packages.ingestion.video import GEOMETRY_VERSION, geometry_filter, video_geometry
from packages.knowledge.service import content_hash


def _fail(code, message):
    raise ContractError(code, message)


def _sha(path, deadline=None):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1048576), b''):
            if deadline is not None: _remaining(deadline)
            digest.update(block)
    return digest.hexdigest()


def _remaining(deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0:_fail('PREVIEW_TIMEOUT','兼容预览准备超时，请稍后重试或查看原片')
    return remaining


def _json_probe(path, entries, deadline, *, packets=False, max_frames=None):
    command=['ffprobe','-v','error','-protocol_whitelist','file,pipe','-select_streams','v:0']
    if packets:
        command.extend(['-read_intervals',f'%+#{max_frames + 1}','-show_packets'])
    command.extend(['-show_entries',entries,'-of','json',str(path)])
    try:
        result=subprocess.run(command,capture_output=True,timeout=_remaining(deadline),check=True)
        return json.loads(result.stdout)
    except ContractError:
        raise
    except subprocess.TimeoutExpired:
        _fail('PREVIEW_TIMEOUT','兼容预览探测超过配置时间限制')
    except (OSError,subprocess.CalledProcessError,ValueError):
        _fail('PREVIEW_PROBE_FAILED','无法读取注册原片或兼容预览的时间与显示信息')


def _packet_times(path, deadline, max_frames):
    # Packet PTS in MP4 represent displayed frame times; demuxing avoids a third
    # full RGB decode. Sorting accounts for codec-specific B-frame decode order.
    raw=_json_probe(path,'stream=time_base:packet=pts',deadline,packets=True,max_frames=max_frames)
    try:
        timebase=Fraction(raw['streams'][0]['time_base'])
        values=sorted(int(packet['pts'])*timebase for packet in raw['packets'])
        if not values or timebase<=0:raise ValueError
        if len(values)>max_frames:_fail('PREVIEW_FRAME_LIMIT','兼容预览帧数超过 ingestion 配置上限')
        return values
    except ContractError:
        raise
    except (KeyError,ValueError,IndexError,ZeroDivisionError):
        _fail('PREVIEW_TIMESTAMPS_INVALID','原片时间戳不完整，不能生成可精确回查的兼容预览')


def _disk(root):
    return sum(p.stat().st_size for p in root.iterdir() if p.is_file())


@contextmanager
def _locked(root, deadline):
    with (root/'.preview.lock').open('a') as lock:
        while True:
            try:
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(.1,_remaining(deadline)))
        try:yield
        finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)


def preview_video(path: Path, source_sha: str, bundle, settings) -> tuple[Path, dict]:
    """Return a verified local preview and its immutable lineage metadata.

    The caller must resolve a registered source, never a user-supplied path.
    Cache budget is shared across this tenant's previews; generation is serialized
    with a filesystem lock. Failures remove only the current temporary derivative.
    """
    if settings is None or not settings.preview_enabled:
        _fail('PREVIEW_DISABLED','当前配置未开启本地兼容预览，请查看原片')
    if bundle.config.ingestion is None:
        _fail('PREVIEW_DISABLED','兼容预览需要注册 ingestion 资源上限')
    max_frames = bundle.config.ingestion.max_frames
    path=Path(path)
    deadline=time.monotonic()+settings.preview_timeout_seconds
    tenant=hashlib.sha256(bundle.profile.tenant_id.encode()).hexdigest()
    root=bundle.paths['datasets']/'viewing-previews'/tenant
    root.mkdir(parents=True,exist_ok=True)
    recipe={'contract':'local-h264-preview-v1','geometry_version':GEOMETRY_VERSION,
        'implementation_sha256':_sha(Path(__file__)),'long_edge':settings.preview_long_edge,
        'threads':settings.preview_threads,'video_codec':'libx264','pixel_format':'yuv420p',
        'time_policy':'copy_original_pts_and_input_timebase_vfr','audio':'omitted'}
    key=content_hash({'source_sha256':source_sha,'recipe':recipe})
    target=root/(key+'.mp4');sidecar=root/(key+'.json')
    with _locked(root,deadline):
        for stale in root.glob('.preview-*.mp4'):stale.unlink()
        if _sha(path,deadline)!=source_sha:
            _fail('PREVIEW_SOURCE_CHANGED','原片内容与注册 SHA 不一致，已停止兼容预览')
        if target.is_file() and sidecar.is_file():
            try:
                metadata=json.loads(sidecar.read_text())
                if (metadata['source_sha256']==source_sha and metadata['recipe']==recipe
                        and target.stat().st_size==metadata['preview_bytes']
                        and _sha(target,deadline)==metadata['preview_sha256']):
                    return target,metadata
            except ContractError:raise
            except (KeyError,ValueError):pass
        raw=_json_probe(path,'stream=width,height,sample_aspect_ratio,time_base:stream_tags=rotate:stream_side_data=rotation,displaymatrix',deadline)
        try:stream=raw['streams'][0];geometry=video_geometry(stream,settings.preview_long_edge)
        except ContractError:raise
        except (KeyError,IndexError,ValueError):
            _fail('PREVIEW_GEOMETRY_INVALID','注册原片的显示方向或宽高比无法安全转换')
        source_times=_packet_times(path,deadline,max_frames)
        # yuv420p encoders require even dimensions. Pad at most one display pixel
        # per edge, preserving the scaled image instead of stretching its aspect.
        filters=geometry_filter(geometry)+',pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1'
        fd,name=tempfile.mkstemp(prefix='.preview-',suffix='.mp4',dir=root);os.close(fd);temporary=Path(name)
        process=None
        try:
            command=['ffmpeg','-v','error','-y','-protocol_whitelist','file,pipe','-threads',str(settings.preview_threads),
                '-filter_threads',str(settings.preview_threads),'-copyts','-noautorotate','-display_rotation:v:0','0','-i',str(path),
                '-map','0:v:0','-an','-sn','-dn','-vf',filters,'-fps_mode','passthrough',
                '-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p',
                '-threads',str(settings.preview_threads),'-enc_time_base',stream['time_base'],
                '-avoid_negative_ts','disabled','-map_metadata','-1','-metadata:s:v:0','rotate=0',
                '-movflags','+faststart','-fs',str(max(1,settings.preview_disk_budget_bytes-_disk(root))),str(temporary)]
            process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            while process.poll() is None:
                _remaining(deadline)
                if _disk(root)>settings.preview_disk_budget_bytes:
                    _fail('PREVIEW_DISK_LIMIT','兼容预览缓存超过配置的磁盘预算，请清理后重试')
                time.sleep(min(.1,_remaining(deadline)))
            if process.returncode:
                _fail('PREVIEW_ENCODING_FAILED','兼容预览转换失败，请查看原片或检查 FFmpeg H.264 支持')
            if _disk(root)>settings.preview_disk_budget_bytes:
                _fail('PREVIEW_DISK_LIMIT','兼容预览缓存超过配置的磁盘预算，请清理后重试')
            if _packet_times(temporary,deadline,max_frames)!=source_times:
                _fail('PREVIEW_TIMESTAMPS_CHANGED','兼容预览未保留原片显示时间戳，已拒绝回查')
            if _sha(path,deadline)!=source_sha:
                _fail('PREVIEW_SOURCE_CHANGED','生成期间原片内容发生变化，已丢弃兼容预览')
            output_info=_json_probe(temporary,'stream=width,height,sample_aspect_ratio:stream_side_data=rotation',deadline)['streams'][0]
            if any(item.get('rotation',0) != 0 for item in output_info.get('side_data_list',[])):
                _fail('PREVIEW_GEOMETRY_INVALID','兼容预览仍带旋转元数据，已拒绝重复旋转')
            preview_sha=_sha(temporary,deadline)
            metadata={'source_sha256':source_sha,'preview_sha256':preview_sha,'preview_bytes':temporary.stat().st_size,
                'recipe':recipe,'geometry':geometry,'display_width':geometry['decoded_width']+(geometry['decoded_width']%2),
                'display_height':geometry['decoded_height']+(geometry['decoded_height']%2),
                'decoded_frames':len(source_times),'original_pts_preserved':True,
                'first_pts_seconds':float(source_times[0]),'last_pts_seconds':float(source_times[-1]),
                'lineage':'derived_for_local_browser_viewing_only','feature_input':False,
                'source_file_modified':False,'audio_included':False}
            content=json.dumps(metadata,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
            if _disk(root)+len(content.encode())>settings.preview_disk_budget_bytes:
                _fail('PREVIEW_DISK_LIMIT','兼容预览及谱系记录超过配置的磁盘预算')
            _remaining(deadline)
            temporary.replace(target)
            sidecar_temporary=sidecar.with_suffix('.json.tmp')
            try:sidecar_temporary.write_text(content);sidecar_temporary.replace(sidecar)
            finally:sidecar_temporary.unlink(missing_ok=True)
            return target,metadata
        except FileNotFoundError:
            _fail('PREVIEW_TOOLS_MISSING','兼容预览需要 FFmpeg 和 FFprobe')
        finally:
            if process is not None and process.poll() is None:
                process.kill();process.wait()
            temporary.unlink(missing_ok=True)
