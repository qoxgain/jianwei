import argparse
import json
from learning_core import validate_manifest

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('manifest');args=ap.parse_args()
    with open(args.manifest,encoding='utf-8') as f: rows=[json.loads(line) for line in f if line.strip()]
    print(json.dumps(validate_manifest(rows),ensure_ascii=False))
