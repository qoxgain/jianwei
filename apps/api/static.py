"""Serve only members of the reviewed build, never an arbitrary dist directory."""
import hashlib,json,os,stat
from pathlib import Path
from fastapi.staticfiles import StaticFiles


class ReviewedStaticFiles(StaticFiles):
    def __init__(self,directory):
        super().__init__(directory=directory,html=True,follow_symlink=False)
        self.root=Path(directory).resolve();self.members={}
        manifest=self.root/'build-members.json'
        try:
            info=manifest.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError
            value=json.loads(manifest.read_text())
            if value['version']!='reviewed-web-build-v1':raise ValueError
            for name,digest in value['files'].items():
                if name.startswith('/') or '..' in Path(name).parts or not isinstance(digest,str) or len(digest)!=64:raise ValueError
            self.members=value['files']
        except (OSError,ValueError,KeyError,TypeError):pass  # API remains usable; missing/invalid frontend fails closed.
    def lookup_path(self,path):
        full,info=super().lookup_path(path)
        if info is None:return '',None
        if stat.S_ISDIR(info.st_mode):return (full,info) if path in ('','.','/') else ('',None)
        name=Path(path).as_posix()
        expected=self.members.get(name)
        if expected is None or info.st_nlink!=1:return '',None
        target=self.root/name
        try:
            raw=target.lstat()
            if not stat.S_ISREG(raw.st_mode) or target.resolve()!=target or raw.st_nlink!=1:return '',None
            with target.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            after=target.stat()
            if actual!=expected or (raw.st_ino,raw.st_size,raw.st_mtime_ns,raw.st_ctime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):return '',None
        except OSError:return '',None
        return full,info
