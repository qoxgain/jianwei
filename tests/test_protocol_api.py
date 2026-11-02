"""Read-only protocol HTTP surface; no field records are fabricated."""
import json

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.api.main import create_app
from infra.models import AuditEvent, ImportJob, TemporalRun
from packages.temporal import engine
from tests.test_temporal_pipeline import setup_config


def test_four_scene_protocol_preview_is_stable_read_only_and_scoped(tmp_path, monkeypatch):
    path = setup_config(tmp_path)
    app = create_app(path)
    with TestClient(app) as client:
        def counts():
            with app.state.database.session() as session:
                return [session.scalar(select(func.count()).select_from(table))
                        for table in (AuditEvent, ImportJob, TemporalRun)]
        before = counts()
        originals = {p: p.read_bytes() for p in [path, *tmp_path.glob('knowledge/scenes/*.yaml')]}
        def forbidden(*args, **kwargs):
            raise AssertionError('Protocol preview must not open vectors or fit')
        monkeypatch.setattr(np, 'load', forbidden)
        monkeypatch.setattr(engine, 'train', forbidden)
        for scene in app.state.bundle.scenes:
            url = f'/api/scenes/{scene}/temporal/protocol'
            response = client.get(url)
            assert response.status_code == 200, response.text
            report = response.json()
            assert report['scene_id'] == scene and report['status'] == 'blocked'
            assert report['protocol'] is None
            assert report['metadata_only'] and not report['feature_values_read']
            assert not report['training_executed'] and not report['jobs_created']
            assert 'temporal.research_protocol' in report['missing_fields']
            assert client.get(url).json() == report
            json.dumps(report, allow_nan=False)
        assert client.get('/api/scenes/unknown_scene/temporal/protocol').status_code in (404, 422)
        assert counts() == before
        assert all(p.read_bytes() == raw for p, raw in originals.items())
