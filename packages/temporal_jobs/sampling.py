"""Sampling diagnostics, using the unchanged causal selector and no feature values.

Planning is independent of sealed data. A positive count is a time-coverage
check, never training admission or evidence that a model learned anything.
"""
from bisect import bisect_left

from packages.config.errors import ContractError
from packages.knowledge.service import content_hash
from packages.temporal.data import metadata_sequences, window_refs


def policy(learning, settings):
    result = {
        'history_windows': learning.history_windows,
        'horizons_ms': [round(h * 1000) for h in learning.forecast_horizons_seconds],
        'target_tolerance_ms': settings.target_tolerance_ms,
        'max_gap_ms': settings.max_gap_ms,
        'max_pairs_per_split': settings.max_pairs_per_split,
        'target_rule': 'first_start_at_or_after_history_end_plus_horizon',
    }
    return {**result, 'policy_hash': content_hash(result)}


def grid_plan(video, learning, settings):
    """Exact integer-ms arithmetic for a hypothetical uninterrupted uniform grid."""
    clip, stride = round(video.clip_seconds * 1000), round(video.stride_seconds * 1000)
    rules = policy(learning, settings)
    if clip <= 0 or stride <= 0 or any(h <= 0 for h in rules['horizons_ms']):
        raise ContractError('TEMPORAL_TIME_PRECISION', '片段、步长和预测跨度必须达到正整数毫秒精度')
    issued = (learning.history_windows - 1) * stride + clip
    horizons = []
    for horizon in rules['horizons_ms']:
        target = ((issued + horizon + stride - 1) // stride) * stride
        overshoot = target - (issued + horizon)
        horizons.append({'horizon_ms': horizon, 'target_start_offset_ms': target,
                         'overshoot_ms': overshoot,
                         'fits_tolerance': overshoot <= settings.target_tolerance_ms})
    result = {
        'schema_version': 'p034-grid-plan-v1',
        'basis': 'configured_uniform_grid_not_observed_data',
        'clip_ms': clip, 'stride_ms': stride, 'frames_per_clip': video.frames_per_clip,
        'policy': rules, 'history_span_ms': issued, 'horizons': horizons,
        'minimum_episode_ms': max(h['target_start_offset_ms'] for h in horizons) + clip,
        'continuous_grid': stride - clip <= settings.max_gap_ms,
        'compatible_grid': (stride - clip <= settings.max_gap_ms
                            and all(h['fits_tolerance'] for h in horizons)),
        'configuration_changed': False, 'test_data_used': False,
        'note': '理想网格规划不代表实际可用窗口；配置变更不会改变已有缓存，改变采样须重提特征。'
    }
    return {**result, 'plan_hash': content_hash(result)}


def _range(values):
    unique = set(values)
    return {'min': min(unique) if unique else None, 'max': max(unique) if unique else None,
            'uniform': len(unique) == 1}


def analyze(manifest, learning, settings):
    """Diagnose development/train/calibration metadata, never tune against test."""
    if manifest.get('split') == 'test':
        raise ContractError('TEMPORAL_SEALED_DIAGNOSTICS',
                            '封存测试不提供采样选参诊断；请用已固定候选模型的独立评估预检')
    sequences = metadata_sequences(manifest, settings)
    windows, rejected = window_refs(sequences, learning, settings)
    rules = policy(learning, settings)
    candidates = sum(max(0, len(s.batches) - learning.history_windows + 1) for s in sequences)
    per_horizon = []
    for horizon in learning.forecast_horizons_seconds:
        single = learning.model_copy(update={'forecast_horizons_seconds': [horizon]})
        matched, reasons = window_refs(sequences, single, settings)
        example = None
        for seq in sequences:
            if len(seq.batches) < learning.history_windows:
                continue
            issued = max(b.pts_end_ms for b in seq.batches[:learning.history_windows])
            requested = issued + round(horizon * 1000)
            starts = [b.pts_start_ms for b in seq.batches]
            index = bisect_left(starts, requested)
            actual = int(starts[index]) if index < len(starts) else None
            example = {'issued_ms': issued, 'requested_start_ms': requested,
                       'actual_start_ms': actual,
                       'overshoot_ms': actual - requested if actual is not None else None}
            break
        per_horizon.append({'horizon_ms': round(horizon * 1000),
                            'uncapped_causal_windows': len(matched) + reasons.get('resource_subsampled', 0),
                            'rejected_windows': reasons, 'first_history_example': example})
    messages = []
    if not candidates:
        messages.append('同一连续序列内的有效特征不足历史长度，尚无候选历史窗口。')
    if rejected.get('target_outside_tolerance'):
        messages.append('未来目标超出时间容差；先固定采样与预测跨度，不能靠临时放宽容差通过验收。')
    if rejected.get('future_not_available'):
        messages.append('部分历史窗口之后没有足够未来特征，尾部不能补造训练对。')
    if rejected.get('quality_or_continuity_gap'):
        messages.append('部分窗口跨越质量、缺行或连续性屏障，保持排除。')
    if windows:
        messages.append('已有因果窗口仅证明时间覆盖；来源、会话、视角和独立分区仍需单独核验。')
    result = {
        'schema_version': 'p034-sampling-diagnostics-v1',
        'source_hash': manifest['source_hash'], 'feature_space_id': manifest['feature_space_id'],
        'manifest_hash': content_hash(manifest), 'policy': rules,
        'status': 'has_causal_windows' if windows else 'no_causal_windows',
        'eligible_rows': manifest['eligible_rows'], 'sequence_count': len(sequences),
        'short_history_sequences': sum(len(s.batches) < learning.history_windows for s in sequences),
        'candidate_histories': candidates, 'causal_windows': len(windows),
        'uncapped_causal_windows': len(windows) + rejected.get('resource_subsampled', 0),
        'rejected_windows': rejected, 'horizons': per_horizon,
        'observed_clip_ms': _range(b.pts_end_ms - b.pts_start_ms for s in sequences for b in s.batches),
        'observed_start_step_ms': _range(s.batches[i].pts_start_ms - s.batches[i-1].pts_start_ms
                                       for s in sequences for i in range(1, len(s.batches))),
        'observation_windows': sum(e.get('kind') == 'observation_window' for e in manifest['episodes']),
        'verified_business_cycles': sum(e.get('business_cycle_verified') is True for e in manifest['episodes']),
        'feature_values_read': False, 'training_executed': False,
        'training_admission_checked': False, 'messages': messages,
    }
    return {**result, 'report_hash': content_hash(result)}
