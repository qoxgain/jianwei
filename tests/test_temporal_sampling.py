"""Timing diagnostics never fit a model, read vectors, or admit unreviewed sources."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from packages.config.errors import ContractError
from packages.config.loader import load_bundle
from packages.temporal import engine
from packages.temporal.data import metadata_sequences, window_refs
from packages.temporal_jobs.sampling import analyze, grid_plan

ROOT = Path(__file__).resolve().parents[1]
RECORDED = ROOT/'docs/delivery/p0.3.3'


@pytest.fixture
def bundle():
    return load_bundle(ROOT/'config/system.yaml')


def recorded(asset='garment_test'):
    return json.loads((RECORDED/asset/'manifest.json').read_text())


def test_grid_planning_explains_sparse_failure_without_changing_policy(bundle):
    dense = grid_plan(bundle.config.video, bundle.config.learning, bundle.config.temporal)
    assert dense['compatible_grid'] and dense['history_span_ms'] == 5500
    assert dense['minimum_episode_ms'] == 12500
    sparse_video = bundle.config.video.model_copy(update={'stride_seconds': 2})
    sparse = grid_plan(sparse_video, bundle.config.learning, bundle.config.temporal)
    assert not sparse['compatible_grid'] and sparse['horizons'][0]['overshoot_ms'] == 1500
    assert not sparse['configuration_changed'] and not sparse['test_data_used']
    assert sparse['policy'] == dense['policy']
    assert sparse['plan_hash'] != dense['plan_hash']
    aligned = bundle.config.learning.model_copy(update={'forecast_horizons_seconds': [2,4,6]})
    alternative = grid_plan(sparse_video, aligned, bundle.config.temporal)
    assert alternative['compatible_grid'] and alternative['minimum_episode_ms'] == 24000


def test_grid_also_checks_continuity_and_millisecond_precision(bundle):
    video = bundle.config.video.model_copy(update={'stride_seconds': 5})
    plan = grid_plan(video, bundle.config.learning, bundle.config.temporal)
    assert not plan['continuous_grid'] and not plan['compatible_grid']
    learning = bundle.config.learning.model_copy(update={'forecast_horizons_seconds': [.0001]})
    with pytest.raises(ContractError, match='毫秒'):
        grid_plan(video, learning, bundle.config.temporal)


def test_nine_recorded_sources_have_zero_pairs_without_opening_features(bundle, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('diagnostics must never read feature values or train')
    monkeypatch.setattr(np, 'load', forbidden)
    monkeypatch.setattr(engine, 'train', forbidden)
    paths = sorted(RECORDED.glob('*/manifest.json'))
    assert len(paths) == 9
    total = 0
    for path in paths:
        before = path.read_bytes(); manifest = json.loads(before)
        original = deepcopy(manifest)
        result = analyze(manifest, bundle.config.learning, bundle.config.temporal)
        assert result['causal_windows'] == 0 and not result['training_admission_checked']
        assert not result['feature_values_read'] and not result['training_executed']
        assert result['verified_business_cycles'] == 0
        assert result == analyze(manifest, bundle.config.learning, bundle.config.temporal)
        assert manifest == original and path.read_bytes() == before
        total += result['eligible_rows']
    assert total == 695


def test_actual_long_and_short_sources_have_distinct_blockers(bundle):
    long = analyze(recorded(), bundle.config.learning, bundle.config.temporal)
    assert long['rejected_windows'] == {'target_outside_tolerance':37,'future_not_available':8}
    assert long['horizons'][0]['first_history_example']['overshoot_ms'] == 1500
    assert long['observed_start_step_ms'] == {'min':2000,'max':2000,'uniform':True}
    short = analyze(recorded('garment_test_short'), bundle.config.learning, bundle.config.temporal)
    assert short['rejected_windows'] == {'future_not_available':3}
    assert short['horizons'][-1]['first_history_example']['actual_start_ms'] is None


def test_alternative_protocol_is_metadata_only_and_respects_caps_and_barriers(bundle):
    # A policy comparison on unassigned development data, not a claim of training.
    learning = bundle.config.learning.model_copy(update={'forecast_horizons_seconds':[2,4,6]})
    counts = [analyze(json.loads(p.read_text()),learning,bundle.config.temporal)['causal_windows']
              for p in sorted(RECORDED.glob('*/manifest.json'))]
    assert counts == [57,40,62,52,66,42,46,37,0]
    manifest = recorded()
    capped = bundle.config.temporal.model_copy(update={'max_pairs_per_split':8})
    report = analyze(manifest, learning, capped)
    assert report['causal_windows'] == 8 and report['uncapped_causal_windows'] == 37
    manifest['discarded_clips'] = [{'start_ms':0,'end_ms':1000000}]
    report = analyze(manifest, learning, capped)
    assert report['causal_windows'] == 0
    assert report['rejected_windows']['quality_or_continuity_gap'] == 37
    expected,reasons = window_refs(metadata_sequences(manifest,capped),learning,capped)
    assert report['causal_windows'] == len(expected) and report['rejected_windows'] == reasons


def test_sealed_test_cannot_be_used_for_sampling_diagnostics(bundle):
    manifest = recorded();manifest['split'] = 'test'
    with pytest.raises(ContractError) as exc:
        analyze(manifest,bundle.config.learning,bundle.config.temporal)
    assert exc.value.code == 'TEMPORAL_SEALED_DIAGNOSTICS'


def test_cli_reports_zero_and_refuses_input_overwrite(tmp_path):
    source = RECORDED/'garment_test_short/manifest.json'
    out = tmp_path/'audit.json'
    args = [sys.executable,'-m','scripts.audit_temporal_sampling','--manifest',str(source),'--output']
    result = subprocess.run([*args,str(out)],cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert result.returncode == 2, result.stderr
    report = json.loads(out.read_text())
    assert report['metadata_only'] and report['sources'][0]['causal_windows'] == 0
    before = source.read_bytes()
    result = subprocess.run([*args,str(source)],cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert result.returncode == 2 and 'must not overwrite' in result.stderr
    assert source.read_bytes() == before
