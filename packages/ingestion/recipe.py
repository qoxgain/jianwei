"""Versioned production dependency closure; no source pixels or feature values."""
from functools import lru_cache
import hashlib
from importlib import metadata
from pathlib import Path
import platform
import shutil
import subprocess

from packages.knowledge.service import content_hash

VERSION = 'p036-feature-production-v2'
ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_snapshot():
    paths = sorted(p for package in ('ingestion','encoders','contracts','config','knowledge','migrations')
                   for p in (ROOT/'packages'/package).glob('*.py'))
    paths += [ROOT/'requirements.lock', ROOT/'requirements-encoder.txt']
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


@lru_cache(maxsize=16)
def _tool(path, size, modified_ns):
    # The executable comes from the configured runtime PATH, never an upload.
    output = subprocess.run([path,'-version'],capture_output=True,text=True,timeout=5,check=True).stdout
    lines = output.splitlines()
    return {'executable_sha256':digest(path), 'version':lines[0],
            'libraries':[line.strip() for line in lines if line.lstrip().startswith('lib')]}


def runtime_snapshot():
    tools = {}
    for name in ('ffmpeg','ffprobe'):
        path = shutil.which(name)
        try:
            stat = Path(path).stat() if path else None
            tools[name] = _tool(path,stat.st_size,stat.st_mtime_ns) if stat else {'status':'unavailable'}
        except (OSError,subprocess.SubprocessError):
            tools[name] = {'status':'unavailable'}
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
