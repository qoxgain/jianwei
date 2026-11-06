"""Run a complete zero-label study or a frozen independent-source query."""
import argparse,json
from pathlib import Path
from packages.config.loader import load_bundle
from packages.unlabeled.engine import run,query


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--parent',type=Path);p.add_argument('--shadow-update',action='store_true');a=p.parse_args();bundle=load_bundle(a.config)
    if a.shadow_update and not a.parent:p.error('--shadow-update requires --parent')
    r=query(a.parent,bundle,a.output,a.shadow_update) if a.parent else run(bundle,a.output)
    print(json.dumps({'status':'executed_zero_label_research','schema':r['schema'],'methods':len(r['methods']),'real_f1':None,'production_activated':False}))


if __name__=='__main__':main()
