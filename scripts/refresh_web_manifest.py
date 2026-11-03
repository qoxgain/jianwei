"""Refresh hashes of explicitly reviewed browser inputs; never discover uploads."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'apps/web/browser-files.json'


def main():
    manifest=json.loads(TARGET.read_text())
    for path in manifest['files']:
        file=ROOT/path;stat=file.lstat()
        if file.is_symlink() or stat.st_nlink!=1 or not file.is_file():raise ValueError('Not an independent reviewed regular file: '+path)
        manifest['files'][path]=hashlib.sha256(file.read_bytes()).hexdigest()
    TARGET.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print('Refreshed explicit reviewed list; review this manifest diff before publishing')


if __name__=='__main__':main()
