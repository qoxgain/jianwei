"""Run B.1 frozen-feature extraction using the sole system.yaml configuration."""
import argparse,json
from pathlib import Path
from packages.config.loader import load_bundle
from packages.local_motion.extraction import extract


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();manifest=extract(load_bundle(a.config),a.output,lambda n,total:print(json.dumps({'rows':n,'total':total}),flush=True))
    print(json.dumps({'status':'succeeded','rows':len(manifest['rows']),'recipe_hash':manifest['recipe_hash'],'evidence_class':manifest['evidence_class']}))


if __name__=='__main__':main()
