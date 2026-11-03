"""Deterministic source-first max-min allocation, with mandatory episode coverage."""
from collections import defaultdict
import numpy as np
from packages.config.errors import ContractError


def allocate(capacities, minimums, budget):
    result = dict(minimums)
    remaining = min(budget, sum(capacities.values())) - sum(result.values())
    while remaining > 0:
        available = [k for k in capacities if result[k] < capacities[k]]
        key = min(available, key=lambda k: (result[k], str(k)))
        result[key] += 1
        remaining -= 1
    return result


def sample(windows, sequences, budget, coverage):
    groups = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for window in windows:
        source = sequences[window.sequence].manifest['source_hash']
        groups[window.split][source][window.sequence].append(window)
    selected = []
    for split, sources in sorted(groups.items()):
        capacities = {source: sum(map(len, episodes.values())) for source, episodes in sources.items()}
        minimums = {source: len(episodes) for source, episodes in sources.items()}
        required = sum(minimums.values())
        summary = {'budget': budget, 'candidate_windows': sum(capacities.values()),
                   'required_sources': len(sources), 'required_sequences': required,
                   'selected_windows': 0, 'source_coverage': 0., 'sources': []}
        coverage[split] = summary
        if budget < required:
            raise ContractError('TEMPORAL_WINDOW_BUDGET',
                f'{split}窗口预算{budget}不足以覆盖{len(sources)}个来源的{required}个有效周期/时间域/实体序列；未读取特征',
                [{'path': f'temporal.max_pairs_per_split', 'message': f'required_at_least={required}; configured={budget}'}])
        quotas = allocate(capacities, minimums, budget)
        for source, episodes in sorted(sources.items()):
            # Stable sequence keys preserve allocation under source/import order
            # changes; separate entities and time domains remain separate units.
            def key(si):
                seq = sequences[si]; first = seq.batches[0]
                return (str(getattr(first, 'episode_id', '')), str(getattr(first, 'time_domain_id', '')),
                        str(getattr(first, 'entity_id', '')), first.pts_start_ms, seq.batches[-1].pts_end_ms)
            ordered = sorted(episodes, key=key)
            ids = {i: si for i, si in enumerate(ordered)}
            counts = {i: len(episodes[si]) for i, si in ids.items()}
            episode_quotas = allocate(counts, {i: 1 for i in counts}, quotas[source])
            source_summary = {'source_hash': source, 'candidate_windows': capacities[source],
                              'selected_windows': quotas[source], 'sequences': []}
            for i, si in ids.items():
                group, quota = episodes[si], episode_quotas[i]
                selected.extend(group[j] for j in np.linspace(0, len(group)-1, quota, dtype=int))
                source_summary['sequences'].append({'episode_id': getattr(sequences[si].batches[0], 'episode_id', None),
                    'time_domain_id': getattr(sequences[si].batches[0], 'time_domain_id', None),
                    'entity_id': getattr(sequences[si].batches[0], 'entity_id', None),
                    'candidate_windows': len(group), 'selected_windows': quota,
                    'resource_subsampled': len(group)-quota})
            summary['sources'].append(source_summary)
        summary.update(selected_windows=sum(quotas.values()), source_coverage=1.)
    return selected
