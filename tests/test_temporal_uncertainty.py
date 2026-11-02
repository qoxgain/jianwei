"""Known, fabricated session aggregates validate uncertainty, never field effects."""
from copy import deepcopy
import json

import numpy as np
import pytest

from packages.config.errors import ContractError
from packages.config.loader import load_bundle
from packages.contracts.research import ResearchProtocol
from packages.knowledge.service import content_hash
from packages.temporal import engine
from packages.temporal.uncertainty import METRICS, summarize_sessions


def policy(**changes):
    return ResearchProtocol.model_validate({
        'protocol_id': 'fabricated_statistics_fixture',
        'registered_by': 'fixture_author',
        'registered_at': '2026-10-01T10:00:00+00:00',
        'min_independent_sessions': 3,
        'minimum_relative_improvement': .1,
        'bootstrap_replicates': 1000,
        'bootstrap_seed': 17,
        **changes,
    }).model_dump(mode='json')


def aggregates(count=4, *, split='test', origin='real_video'):
    entries, sources = [], {}
    for index in range(count):
        source = f'{index + 1:064x}'
        entries.append({'source_hash': source, 'split': split,
            'provenance': {'capture_session_id': f'session_{index}',
                'root_source_sha256': source}})
        # Relative improvements are constant, while absolute errors differ by
        # session and horizon. This makes a known paired-CI check independent of
        # bootstrap implementation details.
        sources[source] = {}
        for model, scale in zip(engine.MODELS, (1., .8, .5, .7, 1.5)):
            base = (index + 1) * scale
            sources[source][model] = {
                'normalized_feature_mse': [base, base * 2],
                'original_feature_cosine': [.3, .5],
                'anonymous_state_nll': [base * .2, base * .3],
                'anonymous_state_brier': [.1, .2],
            }
    return ({'split': split, 'windows': count * 1000, 'per_source': sources},
            {'data_origin': origin, 'entries': entries})


def summarize(count=4, *, split='test', origin='real_video', configured=None):
    metrics, snapshot = aggregates(count, split=split, origin=origin)
    return summarize_sessions(metrics, snapshot, configured or policy(), 'ridge', split)


def assert_code(code, operation):
    with pytest.raises(ContractError) as raised:
        operation()
    assert raised.value.code == code


def test_equal_session_paired_intervals_and_all_horizon_metrics():
    result = summarize()
    assert result['independent_sessions'] == result['resampling_units'] == 4
    assert len(result['per_session']) == 4
    macro = result['session_macro_metrics']['ridge']
    assert set(macro) == set(METRICS) | {'source_macro_mse'}
    assert macro['normalized_feature_mse']['mean'] == [1.25, 2.5]
    assert macro['source_macro_mse']['mean'] == 1.875
    for name in METRICS:
        assert len(macro[name]['confidence_interval']['lower']) == 2
        assert len(macro[name]['confidence_interval']['upper']) == 2
    for control, expected in (('persistence', .5), ('shuffled_ridge', 2/3)):
        comparison = result['comparisons'][control]
        assert comparison['relative_improvement'] == pytest.approx(expected)
        assert comparison['confidence_interval']['lower'] == pytest.approx(expected)
        assert comparison['confidence_interval']['upper'] == pytest.approx(expected)
        assert comparison['status'] == 'passed'
    assert result['gate']['status'] == 'passed'
    assert result['gate']['real_effect_pass']
    assert not result['gate']['auto_activated'] and not result['gate']['p04_admitted']
    json.dumps(result, allow_nan=False)


def test_source_order_and_correlated_window_counts_do_not_change_intervals():
    metrics, snapshot = aggregates()
    first = summarize_sessions(metrics, snapshot, policy(), 'ridge', 'test')
    metrics['per_source'] = dict(reversed(list(metrics['per_source'].items())))
    metrics['windows'] = 10_000_000
    snapshot['entries'].reverse()
    for entry in snapshot['entries']:
        entry.update(rows=100000, eligible_rows=99999)
    assert summarize_sessions(metrics, snapshot, policy(), 'ridge', 'test') == first


def test_missing_protocol_has_means_but_no_interval_or_release():
    metrics, snapshot = aggregates()
    result = summarize_sessions(metrics, snapshot, None, 'ridge', 'test')
    assert result['gate']['status'] == 'blocked'
    assert result['gate']['reasons'] == ['protocol_not_registered']
    assert result['uncertainty']['status'] == 'protocol_not_registered'
    assert result['session_macro_metrics']['ridge']['source_macro_mse']['mean'] == 1.875
    assert all(item['confidence_interval'] is None
               for model in result['session_macro_metrics'].values() for item in model.values())
    assert all(c['confidence_interval'] is None for c in result['comparisons'].values())


def test_single_or_below_preregistered_minimum_has_no_interval():
    for count in (1, 2):
        result = summarize(count)
        assert result['gate']['status'] == 'blocked'
        assert result['gate']['reasons'] == ['insufficient_independent_sessions']
        assert result['uncertainty']['status'] == 'insufficient_sessions'
        assert all(item['confidence_interval'] is None
                   for model in result['session_macro_metrics'].values() for item in model.values())


def test_calibration_is_never_final_and_synthetic_is_never_real_evidence():
    calibrated = summarize(split='calibration')
    assert calibrated['gate']['status'] == 'not_final'
    assert not calibrated['gate']['real_effect_pass']
    for split in ('calibration', 'test'):
        synthetic = summarize(split=split, origin='synthetic_features')
        assert synthetic['gate']['status'] == 'mechanism_only'
        assert synthetic['independent_sessions'] == 0
        assert synthetic['resampling_units'] == 4
        assert all(p['capture_session_id'] is None for p in synthetic['per_session'])
        assert not synthetic['gate']['real_effect_pass']


def test_both_preregistered_controls_must_pass_and_threshold_is_strict():
    metrics, snapshot = aggregates()
    # Selected model improves on persistence but loses to the shuffled control.
    for item in metrics['per_source'].values():
        item['shuffled_ridge']['normalized_feature_mse'] = [.1, .2]
    result = summarize_sessions(metrics, snapshot, policy(), 'ridge', 'test')
    assert result['comparisons']['persistence']['status'] == 'passed'
    assert result['comparisons']['shuffled_ridge']['status'] == 'failed'
    assert result['gate']['status'] == 'failed'
    assert summarize(configured=policy(minimum_relative_improvement=.5))['gate']['status'] == 'failed'
    assert summarize_sessions(*aggregates(), policy(), 'persistence', 'test')['gate']['status'] == 'failed'


@pytest.mark.parametrize('only_one_zero', [False, True])
def test_zero_baseline_or_zero_resampled_mean_is_undefined_not_infinite(only_one_zero):
    metrics, snapshot = aggregates(count=2)
    for i, item in enumerate(metrics['per_source'].values()):
        if not only_one_zero or i == 0:
            item['persistence']['normalized_feature_mse'] = [0., 0.]
    result = summarize_sessions(metrics, snapshot, policy(min_independent_sessions=2), 'ridge', 'test')
    comparison = result['comparisons']['persistence']
    assert comparison['status'] == 'undefined_baseline'
    assert comparison['confidence_interval'] is None
    assert result['gate']['status'] == 'blocked'
    assert result['gate']['reasons'] == ['undefined_control_baseline']
    if not only_one_zero:
        assert comparison['relative_improvement'] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('change,code', [
    ('same_session', 'TEMPORAL_DUPLICATE_SESSION'),
    ('same_root', 'TEMPORAL_DUPLICATE_SOURCE'),
    ('missing_session', 'TEMPORAL_SESSION_REQUIRED'),
    ('missing_root', 'TEMPORAL_SESSION_REQUIRED'),
    ('missing_source', 'TEMPORAL_METRICS_INCOMPLETE'),
    ('extra_source', 'TEMPORAL_METRICS_INCOMPLETE'),
    ('duplicate_source', 'TEMPORAL_METRICS_INCOMPLETE'),
])
def test_source_identity_and_exact_coverage_are_required(change, code):
    metrics, snapshot = aggregates()
    entries = snapshot['entries']
    if change == 'same_session':
        entries[1]['provenance']['capture_session_id'] = entries[0]['provenance']['capture_session_id']
    elif change == 'same_root':
        entries[1]['provenance']['root_source_sha256'] = entries[0]['provenance']['root_source_sha256']
    elif change == 'missing_session':
        entries[0]['provenance'].pop('capture_session_id')
    elif change == 'missing_root':
        entries[0]['provenance'].pop('root_source_sha256')
    elif change == 'missing_source':
        metrics['per_source'].pop(entries[0]['source_hash'])
    elif change == 'extra_source':
        metrics['per_source']['f' * 64] = deepcopy(next(iter(metrics['per_source'].values())))
    else:
        entries.append(deepcopy(entries[0]))
    assert_code(code, lambda: summarize_sessions(metrics, snapshot, policy(), 'ridge', 'test'))


@pytest.mark.parametrize('bad', [[np.nan, 1.], [np.inf, 1.], [-.1, 1.], [True, 1.],
                               ['1', 1.], [None, 1.], [], [[1.], [1.]], [1e308, 1e308]])
def test_invalid_or_overflowing_metrics_fail_closed(bad):
    metrics, snapshot = aggregates()
    next(iter(metrics['per_source'].values()))['ridge']['normalized_feature_mse'] = bad
    assert_code('TEMPORAL_METRICS_INVALID',
        lambda: summarize_sessions(metrics, snapshot, policy(), 'ridge', 'test'))


@pytest.mark.parametrize('change', ['missing_model', 'missing_metric', 'different_horizons', 'cosine_range'])
def test_every_model_and_horizon_must_be_present(change):
    metrics, snapshot = aggregates()
    item = next(iter(metrics['per_source'].values()))
    if change == 'missing_model': item.pop('shuffled_ridge')
    elif change == 'missing_metric': item['ridge'].pop('anonymous_state_brier')
    elif change == 'different_horizons': item['ridge']['anonymous_state_nll'] = [.1]
    else: item['ridge']['original_feature_cosine'] = [1.1, .1]
    assert_code('TEMPORAL_METRICS_INVALID',
        lambda: summarize_sessions(metrics, snapshot, policy(), 'ridge', 'test'))


def test_no_posthoc_model_or_split_selection():
    metrics, snapshot = aggregates()
    assert_code('TEMPORAL_METRIC_MODEL',
        lambda: summarize_sessions(metrics, snapshot, policy(), 'shuffled_ridge', 'test'))
    assert_code('TEMPORAL_METRIC_SCOPE',
        lambda: summarize_sessions(metrics, snapshot, policy(), 'ridge', 'calibration'))
    wrong = policy(); wrong['primary_metric'] = 'best_test_score'
    assert_code('TEMPORAL_PROTOCOL_INVALID',
        lambda: summarize_sessions(metrics, snapshot, wrong, 'ridge', 'test'))


def test_engine_requires_and_persists_matching_freeze_before_training_values(tmp_path, monkeypatch):
    bundle = load_bundle()
    settings = bundle.config.temporal
    settings.research_protocol = ResearchProtocol.model_validate(policy())
    snapshot = {'data_origin': 'real_video', 'hash': 'a' * 64}
    monkeypatch.setattr(engine, 'values', lambda *a: pytest.fail('unregistered feature read'))
    args = ([], snapshot, bundle.config.learning, settings, tmp_path, bundle.config_hash, 'b' * 64)
    assert_code('TEMPORAL_PROTOCOL_REQUIRED', lambda: engine.train(*args))
    frozen = {'training_snapshot_hash': snapshot['hash'], 'protocol': policy(),
              'bindings': {'config_hash': bundle.config_hash, 'knowledge_hash': 'b' * 64,
                           'engine_hash': engine.code_hash()}}
    binding = {'snapshot': frozen, 'hash': content_hash(frozen)}
    bad = deepcopy(binding); bad['snapshot']['protocol']['minimum_relative_improvement'] = .01
    assert_code('TEMPORAL_PROTOCOL_CHANGED', lambda: engine.train(*args, research_protocol=bad))
    assert not (tmp_path/'protocol.json').exists()
    # There are deliberately no observations in this fixture; freezing precedes
    # the expected insufficient-data rejection and does not train a real model.
    assert_code('TEMPORAL_INSUFFICIENT_PAIRS', lambda: engine.train(*args, research_protocol=binding))
    assert json.loads((tmp_path/'protocol.json').read_text()) == binding
