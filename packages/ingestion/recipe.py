"""Versioned production dependency closure; no source pixels or feature values."""
import hashlib
from importlib import metadata
from pathlib import Path
import platform
import shutil
import subprocess

from packages.knowledge.service import content_hash

VERSION = 'p037-a1-feature-production-v4'
ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_snapshot():
    paths = sorted(p for package in ('ingestion','encoders','contracts','config','knowledge','migrations')
                   for p in (ROOT/'packages'/package).glob('*.py'))
    paths += [ROOT/'requirements.lock', ROOT/'requirements-encoder.txt']
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


def _tool(path, size, modified_ns):
    # The executable comes from the configured runtime PATH, never an upload.
    before=digest(path)
    try:
        output = subprocess.run([path,'-version'],capture_output=True,text=True,timeout=5,check=True).stdout
        after=digest(path)
    except (OSError,subprocess.SubprocessError) as error:
        from packages.config.errors import ContractError
        raise ContractError('FEATURE_TOOL_PROBE_FAILED','工具首次哈希成功后探测异常，停止生产和缓存复用',
            [{'path':'runtime.tools','message':'phase=version_probe; exception='+type(error).__name__}]) from None
    if before!=after:
        from packages.config.errors import ContractError
        raise ContractError('FEATURE_TOOL_CHANGED','工具在版本核验期间发生变化，停止生产/缓存复用')
    lines = output.splitlines()
    if not lines:
        from packages.config.errors import ContractError
        raise ContractError('FEATURE_TOOL_PROBE_FAILED','工具版本探测未返回有效记录')
    return {'executable_sha256':after, 'version':lines[0],
            'libraries':[line.strip() for line in lines if line.lstrip().startswith('lib')]}


def runtime_snapshot():
    tools = {}
    for name in ('ffmpeg','ffprobe'):
        path = shutil.which(name)
        try:
            stat = Path(path).stat() if path else None
            tools[name] = _tool(path,stat.st_size,stat.st_mtime_ns) if stat else {'status':'unavailable'}
        except (OSError,subprocess.SubprocessError) as error:
            from packages.config.errors import ContractError
            raise ContractError('FEATURE_TOOL_PROBE_FAILED','工具已定位但核验失败，不能按初始缺失复用缓存',
                [{'path':'runtime.tools.'+name,'message':type(error).__name__}]) from None
    packages = {}
    for name in ('numpy','onnx','onnxruntime','pydantic','rfc8785'):
        try:packages[name]=metadata.version(name)
        except metadata.PackageNotFoundError:packages[name]=None
    return {'python':platform.python_version(),'machine':platform.machine(),
            'system':platform.system(),'packages':packages,'tools':tools,
            'execution_provider':'CPUExecutionProvider','execution_mode':'ORT_SEQUENTIAL'}


def production_snapshot():
    payload = {'version':VERSION,'source_files':source_snapshot(),'runtime':runtime_snapshot()}
    return {**payload,'sha256':content_hash(payload)}
