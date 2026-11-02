"""Paired, equal-session uncertainty for a previously selected forecasting model.

Only per-source aggregates enter this module. Window counts never set bootstrap
sample size. The caller must supply the model/protocol frozen before sealed-test
replay; this module neither selects models nor admits P0.4 or activates anything.
"""
from __future__ import annotations

from collections.abc import Mapping
import numpy as np

from packages.config.errors import ContractError
from packages.knowledge.service import content_hash

METRICS = ('normalized_feature_mse', 'original_feature_cosine',
           'anonymous_state_nll', 'anonymous_state_brier')
CONTROLS = ('persistence', 'shuffled_ridge')
# Numerical guard on normalized MSE, not an effect-size acceptance threshold.
BASELINE_ZERO_TOLERANCE = 1e-12


def fail(code, message):
    raise ContractError(code, message)


def _mean(values, axis=None):
    try:
        with np.errstate(over='raise', invalid='raise'):
            result = np.mean(values, axis=axis)
        if not np.isfinite(result).all():
            raise FloatingPointError
        return result
    except FloatingPointError:
        fail('TEMPORAL_METRICS_INVALID', '会话均值超出有限数值范围')


def _protocol(value):
    if value is None:
        return None
    from pydantic import ValidationError
    from packages.contracts.research import ResearchProtocol
    try:
        raw = value.model_dump(mode='json') if hasattr(value, 'model_dump') else value
        return ResearchProtocol.model_validate(raw).model_dump(mode='json')
    except (ValidationError, ValueError, TypeError):
        fail('TEMPORAL_PROTOCOL_INVALID', '会话统计需要有效且已固定的研究协议')


def _inputs(metrics, snapshot, split, selected_model):
    from .engine import MODELS
    if split not in {'calibration', 'test'} or metrics.get('split') != split:
        fail('TEMPORAL_METRIC_SCOPE', '会话统计只能处理对应校准或封存测试分区')
    if selected_model not in MODELS[:-1]:
        fail('TEMPORAL_METRIC_MODEL', '必须使用校准集已选定的模型，打乱对照不能作为候选')
    origin = snapshot.get('data_origin')
    if origin not in {'real_video', 'synthetic_features'}:
        fail('TEMPORAL_METRIC_SCOPE', '会话统计缺少有效数据来源类型')
    try:
        entries = [e for e in snapshot['entries'] if e['split'] == split]
        hashes = [e['source_hash'] for e in entries]
        sources = metrics['per_source']
        if (not 1 <= len(entries) <= 100 or len(set(hashes)) != len(hashes)
                or not isinstance(sources, Mapping) or set(sources) != set(hashes)):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        fail('TEMPORAL_METRICS_INCOMPLETE', '统计必须覆盖该分区全部选定来源且不能重复或越界')
    records, seen_sessions, seen_roots = [], set(), set()
    horizons = None
    for entry in sorted(entries, key=lambda e: e['source_hash']):
        source = entry['source_hash']
        provenance = entry.get('provenance') or {}
        if origin == 'real_video':
            session = provenance.get('capture_session_id')
            if not isinstance(session, str) or not session.strip():
                fail('TEMPORAL_SESSION_REQUIRED', '真实来源统计必须有已核验的采集会话')
            root = provenance.get('root_source_sha256')
            if not isinstance(root, str) or len(root) != 64:
                fail('TEMPORAL_SESSION_REQUIRED', '真实来源统计必须有已核验的根视频来源')
            if root in seen_roots:
                fail('TEMPORAL_DUPLICATE_SOURCE', '同一原始根的派生文件不能增加独立来源数')
            seen_roots.add(root)
        else:
            # Synthetic source identifiers are reproducibility units only; they
            # are never presented as verified real acquisition sessions.
            session = 'synthetic:' + source
        if session in seen_sessions:
            fail('TEMPORAL_DUPLICATE_SESSION', '同一采集会话只接受一份完整来源，不能重复加权')
        seen_sessions.add(session)
        values = {}
        try:
            item = sources[source]
            if not isinstance(item, Mapping) or set(item) != set(MODELS):
                raise ValueError
            for model in MODELS:
                if set(item[model]) != set(METRICS):
                    raise ValueError
                values[model] = {}
                for name in METRICS:
                    raw = item[model][name]
                    if not isinstance(raw, (list, tuple)) or any(isinstance(x, (bool, str)) for x in raw):
                        raise ValueError
                    vector = np.asarray(raw, dtype=np.float64)
                    if vector.ndim != 1 or not 1 <= len(vector) <= 8 or not np.isfinite(vector).all():
                        raise ValueError
                    if horizons is None:
                        horizons = len(vector)
                    if len(vector) != horizons:
                        raise ValueError
                    if name == 'original_feature_cosine':
                        if np.any(np.abs(vector) > 1 + 1e-6):
                            raise ValueError
                    elif np.any(vector < 0):
                        raise ValueError
                    values[model][name] = vector.tolist()
        except (KeyError, TypeError, ValueError, OverflowError):
            fail('TEMPORAL_METRICS_INVALID', '每个来源需提供完整模型、相同跨度及有限有效指标')
        records.append({'capture_session_id': session if origin == 'real_video' else None,
                        'resampling_unit': session, 'source_hash': source, 'metrics': values,
                        'source_macro_mse': {model: float(_mean(values[model]['normalized_feature_mse']))
                                             for model in MODELS}})
    # Stable ordering makes output and seeded resampling invariant to dictionaries
    # or import-request order. No timestamp/window duplication changes the units.
    records.sort(key=lambda r: r['resampling_unit'])
    return records, origin, MODELS


def _interval(samples, confidence):
    bounds = np.quantile(samples, [(1-confidence)/2, 1-(1-confidence)/2], axis=0)
    if not np.isfinite(bounds).all():
        fail('TEMPORAL_METRICS_INVALID', '会话区间估计出现非有限结果')
    return {'lower': bounds[0].tolist(), 'upper': bounds[1].tolist()}


def summarize_sessions(metrics, snapshot, protocol, selected_model, split):
    """Describe session metrics and assess the frozen model against two controls.

    Percentile bootstrap draws whole independent sessions with replacement. All
    metrics/models use the same draw weights; relative improvement is a ratio of
    session means, not an average of per-window ratios. Near-zero baseline means
    in either the observed data or any bootstrap draw make that comparison
    undefined; such draws are not silently dropped to obtain a passing interval.
    """
    protocol = _protocol(protocol)
    records, origin, models = _inputs(metrics, snapshot, split, selected_model)
    count = len(records)
    enough = protocol is not None and count >= protocol['min_independent_sessions']
    status = 'estimated' if enough else 'protocol_not_registered' if protocol is None else 'insufficient_sessions'
    confidence = protocol['confidence_level'] if protocol else None
    draws = protocol['bootstrap_replicates'] if protocol else None
    seed = protocol['bootstrap_seed'] if protocol else None
    weights = None
    if enough:
        # At most 10,000 x 100 counts (~8 MiB); each subsequent multiplication
        # produces at most 10,000 x 8 means. Never allocate B x sessions x rows.
        weights = np.random.default_rng(seed).multinomial(count, np.full(count, 1/count), size=draws) / count
    macro, primary, bootstrap_primary = {}, {}, {}
    for model in models:
        macro[model] = {}
        for metric in METRICS:
            values = np.asarray([r['metrics'][model][metric] for r in records], dtype=np.float64)
            mean = _mean(values, axis=0)
            interval = _interval(weights @ values, confidence) if enough else None
            macro[model][metric] = {'mean': mean.tolist(), 'confidence_interval': interval}
        primary[model] = np.asarray([r['source_macro_mse'][model] for r in records])
        if enough:
            bootstrap_primary[model] = weights @ primary[model]
        macro[model]['source_macro_mse'] = {
            'mean': float(_mean(primary[model])),
            'confidence_interval': _interval(bootstrap_primary[model], confidence) if enough else None}
    comparisons = {}
    threshold = protocol['minimum_relative_improvement'] if protocol else None
    for control in CONTROLS:
        baseline = float(_mean(primary[control]))
        candidate = float(_mean(primary[selected_model]))
        undefined = baseline <= BASELINE_ZERO_TOLERANCE
        point = None if undefined else float((baseline-candidate)/baseline)
        if point is not None and not np.isfinite(point):
            fail('TEMPORAL_METRICS_INVALID', '相对会话改进超出有限数值范围')
        interval = None
        comparison_status = status
        if enough and not undefined:
            sampled_baseline = bootstrap_primary[control]
            undefined = bool(np.any(sampled_baseline <= BASELINE_ZERO_TOLERANCE))
            if not undefined:
                improvement = (sampled_baseline-bootstrap_primary[selected_model])/sampled_baseline
                if not np.isfinite(improvement).all():
                    undefined = True
                else:
                    interval = _interval(improvement, confidence)
                    comparison_status = 'passed' if interval['lower'] > threshold else 'failed'
        if undefined:
            comparison_status = 'undefined_baseline'
        comparisons[control] = {'baseline_mean': baseline, 'selected_model_mean': candidate,
            'relative_improvement': point, 'confidence_interval': interval, 'status': comparison_status}
    reasons = []
    if origin == 'synthetic_features':
        gate_status = 'mechanism_only'; reasons.append('synthetic_data_not_real_effect_evidence')
    elif protocol is None:
        gate_status = 'blocked'; reasons.append('protocol_not_registered')
    elif split != 'test':
        gate_status = 'not_final'; reasons.append('calibration_not_final_test')
    elif not enough:
        gate_status = 'blocked'; reasons.append('insufficient_independent_sessions')
    elif any(c['status'] == 'undefined_baseline' for c in comparisons.values()):
        gate_status = 'blocked'; reasons.append('undefined_control_baseline')
    elif all(c['status'] == 'passed' for c in comparisons.values()):
        gate_status = 'passed'
    else:
        gate_status = 'failed'; reasons.append('pre_registered_control_threshold_not_met')
    return {'contract_version': 'p035-session-bootstrap-v1', 'split': split, 'data_origin': origin,
        'protocol_id': protocol['protocol_id'] if protocol else None,
        'protocol_hash': content_hash(protocol) if protocol else None,
        'selected_model': selected_model, 'selection_split': 'calibration',
        'source_files': count, 'independent_sessions': count if origin == 'real_video' else 0,
        'resampling_units': count, 'per_session': records, 'session_macro_metrics': macro,
        'comparisons': comparisons,
        'uncertainty': {'status': status, 'method': 'paired_session_percentile_bootstrap',
            'confidence_level': confidence, 'bootstrap_replicates': draws, 'bootstrap_seed': seed,
            'minimum_sessions': protocol['min_independent_sessions'] if protocol else None,
            'baseline_zero_tolerance': BASELINE_ZERO_TOLERANCE,
            'interval_scope': 'conditional on frozen training/model; sessions equally weighted; not window-level or population-wide certainty'},
        'gate': {'status': gate_status, 'reasons': reasons, 'primary_metric': 'source_macro_mse',
            'minimum_relative_improvement': threshold, 'real_effect_pass': gate_status == 'passed',
            'auto_activated': False, 'p04_admitted': False},
        'limitations': ['会话独立性来自已审核采集记录，不能由文件数或重叠窗口数证明。',
            '置信区间仅描述给定冻结模型与当前会话样本的变动，不保证样本代表性。',
            '指标仅为无标签特征预测和匿名簇代理，不是动作、事件或SOP准确率。',
            '门槛通过不自动激活模型或准入P0.4，仍需研究复核。']}
