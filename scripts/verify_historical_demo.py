"""Verify the frozen P0.3.6 archive against its own historical browser evidence."""
import hashlib,json,zipfile
from pathlib import Path
from scripts.check_public_research_artifacts import inspect_file
ROOT=Path(__file__).resolve().parents[1]


def main():
 root=ROOT/'docs/demo/p0.3.6';path=root/'jianwei-p0.3.6-demo.zip';inspect_file(path)
 outer=json.loads((root/'manifest.json').read_text());browser=json.loads((ROOT/'docs/delivery/p0.3.6/browser/results.json').read_text())
 assert hashlib.sha256(path.read_bytes()).hexdigest()==outer['sha256']
 with zipfile.ZipFile(path) as archive:
  assert archive.testzip() is None
  manifest=json.loads(archive.read('manifest.json'))
  assert set(archive.namelist())==set(manifest['files'])|{'manifest.json'}
  for name,entry in manifest['files'].items():assert hashlib.sha256(archive.read(name)).hexdigest()==entry['sha256']
  assert hashlib.sha256(archive.read('index.html')).hexdigest()==browser['build_sha256']
 print(json.dumps({'passed':True,'stage':'P0.3.6','historical_archive_unchanged':True,'current_frontend_not_substituted':True}))


if __name__=='__main__':main()
