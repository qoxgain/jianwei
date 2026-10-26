"""Preflight checks metadata only; none of these fixtures prove field accuracy."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func

from infra.models import ImportJob, SourceSplit, TemporalRun, AuditEvent
from packages.contracts.dataset import SourceReview, DuplicateReview, DatasetManifest
from packages.temporal import engine
from test_temporal_pipeline import research, wait_for

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '/api/scenes/anonymous_observation/temporal'


def selected(example):
    return {'import_ids': example['train_import_ids'] + example['test_import_ids']}


def counts(app):
    with app.state.database.session() as session:
        return [session.scalar(select(func.count()).select_from(table))
                for table in (TemporalRun, SourceSplit, AuditEvent, ImportJob)]


def test_readiness_opens_no_feature_files_and_has_no_side_effects(research, monkeypatch):
    client, app, example, _ = research
    before = counts(app)
    def forbidden(*args, **kwargs):
        raise AssertionError('Precheck must not open vectors or train')
    monkeypatch.setattr(np, 'load', forbidden)
    monkeypatch.setattr(engine, 'train', forbidden)
    response = client.post(PREFIX+'/readiness', json=selected(example))
    assert response.status_code == 200, response.text
    report = response.json()
    assert report['can_submit_train'] and not report['can_submit_evaluate']
    assert report['metadata_only'] and not report['feature_files_opened']
    assert not report['training_executed'] and not report['test_values_read']
    assert report['partitions']['train']['reviewed_independent_groups'] == 4
    assert report['partitions']['test']['reviewed_independent_groups'] == 2
    assert report['partitions']['train']['causal_windows'] == 32
    assert any(b['code'] == 'TEMPORAL_MODEL_REQUIRED' for b in report['evaluation']['blockers'])
    assert client.post(PREFIX+'/readiness', json=selected(example)).json() == report
    assert counts(app) == before


def test_no_valid_future_windows_blocks_training_even_with_enough_sources(research):
    client, app, example, _ = research
    originals = {}
    try:
        with app.state.database.session() as session:
            for identifier in example['groups']['train']:
                row = session.get(ImportJob, identifier); originals[identifier] = row.manifest_json
                manifest = json.loads(row.manifest_json)
                manifest['feature_batches'] = manifest['feature_batches'][:8]
                manifest['eligible_rows'] = 8
                for i, feature in enumerate(manifest['rows']):
                    feature['eligible'] = i < 8
                row.manifest_json = json.dumps(manifest)
        report = client.post(PREFIX+'/readiness', json=selected(example)).json()
        assert not report['can_submit_train']
        assert report['partitions']['train']['causal_windows'] == 0
        assert any(b['code'] == 'TEMPORAL_INSUFFICIENT_PAIRS' for b in report['train']['blockers'])
    finally:
        with app.state.database.session() as session:
            for identifier, original in originals.items():
                session.get(ImportJob, identifier).manifest_json = original


def test_model_specific_precheck_matches_actual_sealed_evaluation(research, monkeypatch):
    client, app, example, _ = research
    response = client.post(PREFIX+'/train', json={'import_ids':example['train_import_ids']})
    trained = wait_for(client, PREFIX+'/runs/'+response.json()['id'])
    assert trained['status'] == 'succeeded'
    body = selected(example) | {'model_run_id': trained['id']}
    with monkeypatch.context() as patch:
        patch.setattr(np, 'load', lambda *a, **k: pytest.fail('metadata check read a feature/model file'))
        result = client.post(PREFIX+'/readiness', json=body).json()
    assert result['can_submit_train'] and result['can_submit_evaluate']
    evaluated = client.post(PREFIX+'/evaluate', json={'model_run_id':trained['id'],
        'import_ids':example['test_import_ids'], 'sealed_test':True})
    job = wait_for(client,PREFIX+'/runs/'+evaluated.json()['id'])
    assert job['status'] == 'succeeded'
    assert job['report']['snapshot']['hash'] == result['evaluation']['snapshot_hash']
    assert job['report']['test']['windows'] == result['partitions']['test']['causal_windows']


@pytest.mark.parametrize('mutation', ['foreign_scene','unknown_id','duplicate','tenant_injection'])
def test_precheck_scope_and_strict_request(research, mutation):
    client, _, example, _ = research
    body = selected(example); prefix = PREFIX
    if mutation == 'foreign_scene': prefix = '/api/scenes/garment_sop/temporal'
    if mutation == 'unknown_id': body['import_ids'][0] = 'unknown'
    if mutation == 'duplicate': body['import_ids'].append(body['import_ids'][0])
    if mutation == 'tenant_injection': body['tenant_id'] = 'other'
    assert client.post(prefix+'/readiness', json=body).status_code in {404, 422}


def test_precheck_budget_fails_before_loading_full_manifests(research):
    client, app, example, _ = research
    settings=app.state.temporal.settings; original=settings.max_rows
    try:
        settings.max_rows=200
        response=client.post(PREFIX+'/readiness',json=selected(example))
        assert response.status_code==422 and response.json()['detail']['code']=='TEMPORAL_RESOURCE_LIMIT'
    finally:settings.max_rows=original


def test_real_recorded_video_remains_blocked_and_template_does_not_invent_capture_data(research):
    client, app, _, _ = research
    # Re-register committed metadata for this test; do not claim a new encode.
    manifest=json.loads((ROOT/'docs/delivery/p0.3.1/video/manifest.json').read_text())
    with app.state.database.session() as session:
        session.add(ImportJob(id='recorded_video',tenant_id=app.state.bundle.profile.tenant_id,
            scene_id='anonymous_observation',idempotency_key='1'*64,config_hash=manifest['config_hash'],
            knowledge_hash=manifest['knowledge_hash'],request_json='{}',config_snapshot_json='{}',
            status='succeeded',manifest_json=json.dumps(manifest)))
        session.add(SourceSplit(id='2'*64,tenant_id=app.state.bundle.profile.tenant_id,
                                source_hash=manifest['source_hash'],split='unassigned'))
    try:
        before=counts(app);body={'import_ids':['recorded_video']}
        report=client.post(PREFIX+'/readiness',json=body).json()
        assert not report['can_submit_train'] and not report['can_submit_evaluate']
        assert report['partitions']['unassigned']['source_files']==1
        assert report['partitions']['unassigned']['reviewed_independent_groups']==0
        assert 'captured_at' in report['records'][0]['missing_records']
        draft=client.post(PREFIX+'/dataset-template',json=body).json()
        registry=DatasetManifest.model_validate(draft['manifest'])
        source=registry.sources[0]
        assert source.source_sha256==manifest['source_hash'] and source.split=='unassigned'
        assert source.capture_session_id is None and source.captured_at is None and source.camera_id is None
        assert source.provenance_review.status=='pending' and source.near_duplicate_review.status=='pending'
        assert source.view_id==manifest['view_id']
        assert draft['draft_only'] and not draft['configuration_written']
        assert counts(app)==before
    finally:
        with app.state.database.session() as session:
            session.delete(session.get(ImportJob,'recorded_video'));session.delete(session.get(SourceSplit,'2'*64))


def test_cli_reads_existing_database_without_starting_worker(research, tmp_path):
    _, app, example, root=research
    path=tmp_path/'request.json';path.write_text(json.dumps(selected(example)))
    before=counts(app)
    result=subprocess.run([sys.executable,'-m','jianwei','research-check','--config',str(root/'config/system.yaml'),
        '--scene','anonymous_observation','--request',str(path)],cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['can_submit_train']
    assert counts(app)==before


@pytest.mark.parametrize('field',['method','notes'])
def test_review_confirmation_cannot_be_whitespace_only(field):
    fields={'status':'confirmed','reviewer':'reviewer','reviewed_at':'2026-10-26T10:00:00+08:00',
            'method':'capture record comparison','notes':'fixture only, not field evidence',field:'      '}
    with pytest.raises(ValidationError):SourceReview.model_validate(fields)


def test_duplicate_review_confirmation_requires_nonblank_evidence():
    with pytest.raises(ValidationError):
        DuplicateReview.model_validate({'left_sha256':'a'*64,'right_sha256':'b'*64,
            'resolution':'independent','reviewer':'reviewer','reviewed_at':'2026-10-26T10:00:00+08:00','notes':'     '})
