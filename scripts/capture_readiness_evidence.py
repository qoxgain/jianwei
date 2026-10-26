"""Capture metadata-only research admission in four isolated synthetic scenes.

The same eight synthetic sources are reused; they are not 32 field recordings.
No training or sealed feature reads are requested by this script.
"""
import argparse
from pathlib import Path
import shutil
import tempfile

from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.config.loader import PROJECT_ROOT
from scripts.capture_temporal_evidence import wait, write_json


def capture(output, fixture=None):
    reports = {}
    with tempfile.TemporaryDirectory(prefix='jianwei-readiness-') as temporary:
        root = Path(temporary)
        for name in ('config', 'knowledge'):
            shutil.copytree(PROJECT_ROOT/name, root/name)
        app = create_app(root/'config/system.yaml')
        with TestClient(app) as client:
            for scene in app.state.bundle.scenes:
                prefix = f'/api/scenes/{scene}/temporal'
                response = client.post(prefix+'/example')
                response.raise_for_status()
                groups = response.json()['groups']
                identifiers = [identifier for group in groups.values() for identifier in group]
                for identifier in identifiers:
                    wait(client, f'/api/scenes/{scene}/imports/{identifier}')
                response = client.post(prefix+'/readiness', json={'import_ids': identifiers})
                response.raise_for_status()
                report = response.json()
                assert report['can_submit_train'] and not report['can_submit_evaluate']
                assert report['partitions']['train']['reviewed_independent_groups'] == 4
                assert report['partitions']['calibration']['reviewed_independent_groups'] == 2
                assert report['partitions']['test']['reviewed_independent_groups'] == 2
                assert not client.get(prefix+'/runs').json()['items']
                reports[scene] = report
                write_json(output/scene/'readiness.json', report)
                print(f'{scene}: metadata ready; evaluation requires model; no jobs', flush=True)
    summary = {'stage': 'P0.3.2', 'scope': 'Eight synthetic sources reused across four scenes; not field evidence.',
               'training_executed': False, 'test_values_read': False, 'scenes': reports}
    write_json(output/'readiness.json', summary)
    if fixture:
        write_json(fixture, {'scenes': reports})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--ui-fixture', type=Path)
    args = parser.parse_args()
    capture(args.output_dir, args.ui_fixture)
