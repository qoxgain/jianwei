from packages.ingestion.capture import feature_window
"""Retrospective, single-source discovery; never a training/evaluation shortcut.

This intentionally accepts only unassigned real video. Its statistics describe
that source and must not be reused as a trained model or generalization result.
"""
from collections import defaultdict
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from packages.config.errors import ContractError
from packages.contracts.learning import FeatureBatch, FeatureSpace
from packages.ingestion.pipeline import file_hash
from packages.knowledge.service import content_hash
from .data import Sequence, has_barrier
from .models import assign, fit_projection, kmeans, project


def fail(code, message):
    raise ContractError(code, message)


def union_intervals(intervals):
    """Count overlapping clips once, independently inside each time domain."""
    result = []
    for start, end in sorted(intervals):
        if not result or start > result[-1][1]:
            result.append([start, end])
        else:
            result[-1][1] = max(end, result[-1][1])
    return result


def explore(manifest, vectors_path, video_path, settings, config_hash):
    if manifest.get('split') != 'unassigned':
        fail('EXPLORATION_SPLIT_FORBIDDEN', '单源探索只接受未分区视频，不能读取训练、校准或封存测试来源')
    if (manifest.get('status') != 'succeeded' or manifest.get('data_origin') != 'real_video'
            or manifest.get('source_verification') != 'verified'):
        fail('EXPLORATION_SOURCE_UNVERIFIED', '需要已成功导入且核验原视频的真实特征')
    if manifest.get('config_hash') != config_hash:
        fail('EXPLORATION_CONFIG_CHANGED', '请使用产生该导入的原始配置快照')
    if file_hash(video_path) != manifest['source_hash']:
        fail('EXPLORATION_SOURCE_CHANGED', '视频字节与导入清单不一致')
    evidence = manifest.get('encoder_evidence', {})
    if (evidence.get('execution') != 'frozen_onnx_cpu'
            or evidence.get('vector_production') != 'executed_from_this_source'
            or evidence.get('artifact_sha_verified') is not True):
        fail('EXPLORATION_ENCODER_UNVERIFIED', '需要实际执行冻结编码器产生的特征；外部声明不能替代执行证据')
    try:
        space = FeatureSpace.model_validate(manifest['feature_space'])
        if (space.identifier() != manifest['feature_space_id']
                or evidence.get('weights_sha') != space.encoder_sha):
            fail('EXPLORATION_SPACE_INVALID', '特征空间或模型哈希不一致')
        cache = manifest['feature_cache']
        rows = manifest['rows']
        if (manifest['total_rows'] > settings.max_rows or space.dimension > settings.max_dimension
                or manifest['total_rows'] * space.dimension > settings.max_feature_values):
            fail('EXPLORATION_RESOURCE_LIMIT', '探索特征超过配置的资源上限')
        if file_hash(vectors_path) != cache['sha256']:
            fail('EXPLORATION_CACHE_CHANGED', '特征缓存字节与导入清单不一致')
        vectors = np.load(vectors_path, allow_pickle=False, mmap_mode='r')
        if (vectors.shape != (manifest['total_rows'], space.dimension) or vectors.dtype.kind != 'f'
                or len(rows) != len(vectors) or cache['rows'] != len(vectors)
                or cache['dimension'] != space.dimension):
            fail('EXPLORATION_CACHE_INVALID', '特征缓存形状与清单不一致')
        groups, seen = defaultdict(list), set()
        episodes = {e['episode_id']: e for e in manifest['episodes']}
        for raw in manifest['feature_batches']:
            batch = FeatureBatch.model_validate(raw)
            if (batch.split, batch.tenant_id, batch.scene_id, batch.source_hash, batch.feature_space_id) != (
                    'unassigned', manifest['tenant_id'], manifest['scene_id'], manifest['source_hash'], space.identifier()):
                fail('EXPLORATION_ROW_INVALID', '特征行来源、分区或作用域不一致')
            index = int(batch.vector_uri.rsplit('#row=', 1)[1])
            if index in seen or not 0 <= index < len(rows):
                fail('EXPLORATION_ROW_INVALID', '特征行引用重复或越界')
            seen.add(index)
            row = rows[index]
            if (batch.vector_uri != f"features://{cache['sha256']}#row={index}" or not row['eligible']
                    or row['row'] != index
                    or (row['start_ms'], row['end_ms'], row['time_domain_id'], row['episode_id']) != (
                        batch.pts_start_ms, batch.pts_end_ms, batch.time_domain_id, batch.episode_id)):
                fail('EXPLORATION_ROW_INVALID', '特征与原始时间、周期、质量记录不一致')
            episode = episodes.get(batch.episode_id)
            if (not feature_window(episode) or episode['time_domain_id'] != batch.time_domain_id
                    or not episode['start_ms'] <= batch.pts_start_ms < batch.pts_end_ms <= episode['end_ms']):
                fail('EXPLORATION_ROW_INVALID', '特征不在完整周期内')
            groups[(batch.episode_id, batch.time_domain_id, batch.entity_id)].append((index, batch))
        eligible = {r['row'] for r in rows if r['eligible']}
        if seen != eligible or len(seen) != manifest['eligible_rows'] or len(seen) < 2:
            fail('EXPLORATION_ROW_INVALID', '有效特征行不完整或少于两行')
        selected = sorted(seen)
        raw_values = np.array(vectors[selected], dtype=np.float64)
    except ContractError:
        raise
    except (KeyError, TypeError, IndexError, ValueError, OSError, ValidationError):
        fail('EXPLORATION_MANIFEST_INVALID', '探索输入清单或特征文件格式无效')
    finally:
        if 'vectors' in locals() and hasattr(vectors, '_mmap'):
            vectors._mmap.close()
    if not np.isfinite(raw_values).all() or np.max(np.abs(raw_values)) > settings.max_abs_feature:
        fail('EXPLORATION_NUMERICAL_FAILURE', '特征包含非有限值或超出配置幅度')
    if not np.any(raw_values.std(0) > 1e-6):
        fail('EXPLORATION_CONSTANT_FEATURES', '特征没有可观察变化，不能生成有意义的匿名候选')
    projection, explained = fit_projection(raw_values, settings.projection_dim)
    z = project(raw_values, projection)
    centers = kmeans(z, min(settings.cluster_count, len(z)), settings.cluster_iterations, settings.seed)
    by_row = {row: point for row, point in zip(selected, z)}
    sequences, diffs = [], []
    for items in groups.values():
        items.sort(key=lambda item: item[1].pts_start_ms)
        sequence = Sequence(manifest, None, [item[1] for item in items], [item[0] for item in items])
        points = np.array([by_row[row] for row in sequence.rows])
        for i in range(1, len(points)):
            if not has_barrier(sequence, i - 1, i, settings):
                diffs.append(float(np.linalg.norm(points[i] - points[i - 1])))
        sequences.append((sequence, points))
    threshold = float(np.quantile(diffs, settings.change_quantile)) if diffs else 0.0
    segments, clips = [], []
    for sequence, points in sequences:
        ids = assign(points, centers)
        for index, batch, cluster in zip(sequence.rows, sequence.batches, ids):
            clips.append({'row': index, 'episode_id': batch.episode_id, 'time_domain_id': batch.time_domain_id,
                          'start_ms': batch.pts_start_ms, 'end_ms': batch.pts_end_ms,
                          'anonymous_cluster_id': f'cluster_{int(cluster)}', 'quality': batch.quality,
                          'vector_uri': batch.vector_uri})
        begin = 0
        for i in range(1, len(points) + 1):
            barrier = i < len(points) and has_barrier(sequence, i - 1, i, settings)
            change = (i < len(points) and np.linalg.norm(points[i] - points[i - 1]) > threshold
                      and i - begin >= settings.min_segment_windows)
            if i == len(points) or barrier or change:
                mean = points[begin:i].mean(0)
                representative = begin + int(np.argmin(np.sum((points[begin:i] - mean) ** 2, axis=1)))
                batch = sequence.batches[representative]
                segments.append({'start_ms': sequence.batches[begin].pts_start_ms,
                                 'end_ms': sequence.batches[i - 1].pts_end_ms,
                                 'episode_id': batch.episode_id, 'time_domain_id': batch.time_domain_id,
                                 'anonymous_cluster_id': f'cluster_{int(assign(mean, centers))}',
                                 'boundary_reason': 'gap' if barrier else 'change' if change else 'episode_end',
                                 'status': 'exploratory_candidate', 'rows': sequence.rows[begin:i],
                                 'representative': {'row': sequence.rows[representative],
                                                    'start_ms': batch.pts_start_ms, 'end_ms': batch.pts_end_ms,
                                                    'vector_uri': batch.vector_uri}})
                begin = i
    coverage = []
    for domain in manifest['domains']:
        intervals = union_intervals([(c['start_ms'], c['end_ms']) for c in clips
                                     if c['time_domain_id'] == domain['time_domain_id']])
        coverage.append({'time_domain_id': domain['time_domain_id'], 'start_ms': domain['start_ms'],
                         'end_ms': domain['end_ms'], 'intervals': intervals,
                         'covered_ms': sum(end - start for start, end in intervals)})
    covered = sum(domain['covered_ms'] for domain in coverage)
    duration = sum(domain['end_ms'] - domain['start_ms'] for domain in coverage)
    cluster_version = content_hash({'source_hash': manifest['source_hash'], 'feature_space_id': space.identifier(),
                                    'cache_hash': cache['sha256'], 'settings': settings.model_dump(),
                                    'projection': {k: v.tolist() for k, v in projection.items()},
                                    'centers': centers.tolist(), 'change_threshold': threshold})
    return {'stage': 'P0.3.1', 'kind': 'single_source_exploration', 'status': 'exploratory_only',
            'source_file': Path(video_path).name, 'source_hash': manifest['source_hash'],
            'source_group_id': manifest.get('source_group_id'), 'source_verification': 'verified',
            'split': 'unassigned', 'independent_original_sources': 1, 'tenant_id': manifest['tenant_id'],
            'scene_id': manifest['scene_id'], 'view_id': manifest['view_id'],
            'config_hash': config_hash, 'manifest_hash': content_hash(manifest),
            'feature_cache': cache, 'feature_space': space.model_dump(), 'feature_space_id': space.identifier(),
            'encoder_evidence': evidence, 'settings': settings.model_dump(), 'cluster_version': cluster_version,
            'fit_scope': 'this unassigned source only; retrospective descriptive statistics',
            'projection': {'dimension': z.shape[1], 'explained_variance_ratio': explained},
            'change_threshold': threshold, 'video': manifest['video'], 'quality': manifest['quality'],
            'eligible_rows': len(clips), 'total_rows': manifest['total_rows'], 'clips': clips, 'segments': segments,
            'coverage': {'covered_ms': covered, 'source_domain_ms': duration,
                         'fraction': covered / duration if duration else 0, 'domains': coverage,
                         'definition': 'union of eligible clip intervals, without counting overlap twice'},
            'observation_windows': [e for e in manifest['episodes'] if e.get('kind') == 'observation_window'],
            'capture_identity': manifest.get('capture_identity'), 'acquisition': manifest.get('acquisition'),
            'view_review': manifest.get('view_review', []),
            'incomplete_episodes': [dict(e, exclusion_reason='incomplete_episode') for e in manifest['episodes'] if not feature_window(e)],
            'rejected_rows': manifest.get('rejected_rows', []), 'discarded_clips': manifest.get('discarded_clips', []),
            'direct_video_seek_supported': len(coverage) == 1 and coverage[0]['start_ms'] == 0,
            'semantic_accuracy': None, 'boundary_accuracy': None, 'forecast_evaluation': None,
            'training_executed': False, 'test_read': False, 'auto_activated': False,
            'real_world_learning_effect_verified': False,
            'limitations': ['匿名簇是本视频内的描述性分组，不是动作类别。',
                            '统计量来自整个未分区视频，不能当作因果预测或独立测试。',
                            '固定窗口不等于人工确认的业务完整周期。',
                            '需要独立采集来源才能验证学习和泛化效果。']}
