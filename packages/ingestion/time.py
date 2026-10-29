"""Time domains and episode boundaries are never repaired by sorting timestamps."""
from datetime import datetime, timedelta, timezone
import numpy as np
from packages.config.errors import ContractError


def time_domains(pts, duration, settings, anchors):
    groups, gaps, row_domains = [], [], []
    begin = 0
    for i in range(len(pts)):
        reset = i > 0 and pts[i] <= pts[i - 1]
        gap = i > 0 and pts[i] > pts[i - 1] + duration[i - 1] + settings.max_gap_ms
        if reset or gap:
            groups.append((begin, i))
            gaps.append({'after_row': i - 1, 'before_row': i, 'reason': 'pts_reset' if reset else 'source_gap',
                         'previous_end_ms': int(pts[i-1] + duration[i-1]), 'next_start_ms': int(pts[i])})
            begin = i
    groups.append((begin, len(pts)))
    anchor_map = {a.domain_index: a for a in anchors}
    if any(i >= len(groups) for i in anchor_map):
        raise ContractError('CLOCK_DOMAIN', '采集锚点引用了不存在的时间域')
    domains = []
    for index, (left, right) in enumerate(groups):
        start, end = int(pts[left]), int(np.max(pts[left:right] + duration[left:right]))
        if end - start > settings.max_duration_seconds * 1000:
            raise ContractError('DURATION_LIMIT', '时间域时长超过配置限制')
        anchor = anchor_map.get(index)
        utc = None
        if anchor:
            if not start <= anchor.pts_ms <= end:
                raise ContractError('CLOCK_ANCHOR_RANGE', '采集锚点必须位于对应时间域内')
            try:
                utc = (datetime.fromisoformat(anchor.capture_utc.replace('Z', '+00:00')).astimezone(timezone.utc)
                       + timedelta(milliseconds=start-anchor.pts_ms)).isoformat()
            except (ValueError, OverflowError):
                raise ContractError('CLOCK_ANCHOR_RANGE', '采集时钟换算超出有效范围') from None
        domains.append({'domain_index': index, 'time_domain_id': f'd{index}', 'start_ms': start,
                        'end_ms': end, 'first_row': left, 'last_row': right-1,
                        'capture_start_utc': utc, 'clock_status': 'anchored' if utc else 'unknown'})
        row_domains.extend([index] * (right-left))
    return domains, gaps, row_domains


def build_episodes(spec, request, domains):
    policy = spec.episode_policy
    supplied = request.episodes
    required_origin = {'manual_cycle': 'manual', 'external_cycle': 'external'}
    if policy.mode in required_origin and request.episode_origin != required_origin[policy.mode]:
        raise ContractError('EPISODE_ORIGIN', '周期来源不满足场景配置，请使用手工周期或外部周期标注')
    if policy.mode == 'manual_or_external_cycle' and request.episode_origin not in {'manual', 'external'}:
        raise ContractError('EPISODE_ORIGIN', '此场景需要手工或外部完整周期')
    if policy.mode == 'manual_or_fixed_window' and supplied and request.episode_origin != 'manual':
        raise ContractError('EPISODE_ORIGIN', '手工周期必须明确 episode_origin=manual')
    if policy.mode == 'fixed_window' and (supplied or request.episode_origin is not None):
        raise ContractError('EPISODE_POLICY', '固定窗口场景由系统产生周期，不接受手工边界')
    if supplied:
        result = []
        for item in supplied:
            if item.domain_index >= len(domains):
                raise ContractError('EPISODE_DOMAIN', '周期引用了不存在的时间域')
            domain = domains[item.domain_index]
            if not domain['start_ms'] <= item.start_ms < item.end_ms <= domain['end_ms']:
                raise ContractError('EPISODE_RANGE', '周期不能超出时间域或跨越断流、PTS 重置')
            result.append({**item.model_dump(), 'time_domain_id': domain['time_domain_id'], 'complete': True,
                           'origin': request.episode_origin, 'kind': 'business_cycle', 'business_cycle_verified': True})
        ordered = sorted(result, key=lambda x: (x['domain_index'], x['start_ms']))
        for previous, current in zip(ordered, ordered[1:]):
            if previous['domain_index'] == current['domain_index'] and previous['end_ms'] > current['start_ms']:
                raise ContractError('EPISODE_OVERLAP', '同一时间域的完整周期不能重叠')
        return result
    if policy.mode not in {'fixed_window', 'manual_or_fixed_window'}:
        raise ContractError('EPISODES_REQUIRED', '此场景必须提供完整周期边界')
    if request.episode_origin is not None:
        raise ContractError('EPISODES_REQUIRED', '声明了周期来源但未提供周期')
    window = int(round(policy.window_seconds * 1000))
    if window < 1:
        raise ContractError('EPISODE_WINDOW_TOO_SMALL', '固定窗口至少需要 1 毫秒')
    result = []
    # Same bound as the explicit ImportRequest. Tiny windows must not expand
    # a bounded request into millions of episode records.
    if sum((d['end_ms']-d['start_ms']+window-1)//window for d in domains) > 10000:
        raise ContractError('EPISODE_COUNT_LIMIT', '自动周期数量超过导入契约上限，请增大窗口')
    for domain in domains:
        start = domain['start_ms']
        n = 0
        while start < domain['end_ms']:
            end = min(start + window, domain['end_ms'])
            result.append({'episode_id': f"d{domain['domain_index']}_window_{n}",
                           'domain_index': domain['domain_index'], 'time_domain_id': domain['time_domain_id'],
                           'start_ms': start, 'end_ms': end, 'complete': end-start == window, 'origin': 'fixed_window'})
            if policy.observation_contract:
                result[-1].update(kind='observation_window', business_cycle_verified=False,
                    observation_contract=policy.observation_contract,
                    feature_eligible=end-start == window or policy.partial_window == 'extract_only')
            start = end
            n += 1
    return result
