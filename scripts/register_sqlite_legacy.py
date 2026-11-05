"""Offline, hash-bound registration of a reviewed OLD BUSINESS DB, never a backup restore."""
import argparse,json
from pathlib import Path
from infra.database import Database
from packages.config.loader import load_bundle,database_url


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--profile',default='demo')
    p.add_argument('--reviewer',required=True);p.add_argument('--reference',required=True);p.add_argument('--expected-sha256',required=True)
    p.add_argument('--writers-stopped',action='store_true',required=True);p.add_argument('--consumption-reviewed',action='store_true',required=True)
    p.add_argument('--legacy-business',action='store_true',required=True);a=p.parse_args()
    bundle=load_bundle(a.config,a.profile);db=Database(database_url(bundle),getattr(bundle.config,'sqlite',None))
    try:
        result=db.register_legacy({'reviewer':a.reviewer,'reference':a.reference,'expected_sha256':a.expected_sha256,
            'writers_stopped':a.writers_stopped,'consumption_reviewed':a.consumption_reviewed,'legacy_business':a.legacy_business})
        print(json.dumps({'state':result['role'],'database_uuid':result['database_uuid'],'origin':result['origin'],
            'restored_from_backup':False,'isolation_cleared':False}))
    finally:db.close()


if __name__=='__main__':main()
