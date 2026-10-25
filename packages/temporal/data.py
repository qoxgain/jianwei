"""Snapshot admission and causal window references. No labels or SOP inputs."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import numpy as np
from sqlalchemy import select
from infra.models import ImportJob, SourceSplit
from packages.config.errors import ContractError
from packages.contracts.learning import FeatureBatch, FeatureSpace
from packages.ingestion.pipeline import file_hash
from packages.knowledge.service import content_hash
from .provenance import source_provenance


def fail(code, message):
    raise ContractError(code, message)


def snapshot_imports(bundle, database, scene_id, identifiers, allowed_splits):
    settings = bundle.config.temporal
    entries, seen, groups, keys, counts = [], set(), set(), set(), Counter()
    total_rows = total_values = 0
    with database.session() as session:
        for identifier in sorted(identifiers):
            job = session.scalar(select(ImportJob).where(ImportJob.id == identifier,
                ImportJob.tenant_id == bundle.profile.tenant_id, ImportJob.scene_id == scene_id))
            if job is None: fail('IMPORT_NOT_FOUND', '研究输入不属于当前租户/场景')
            if job.status != 'succeeded' or not job.manifest_json: fail('TEMPORAL_IMPORT_NOT_READY', '研究只接受成功且有特征的导入')
            m = json.loads(job.manifest_json)
            if m.get('manifest_version') != '3.1' or not m.get('feature_cache'): fail('TEMPORAL_MANIFEST_INVALID', '数据清单版本或特征缓存缺失')
            if (m['tenant_id'], m['scene_id']) != (bundle.profile.tenant_id, scene_id):
                fail('TEMPORAL_SCOPE_MISMATCH', '清单不属于当前租户/场景')
            if m['split'] not in allowed_splits: fail('TEMPORAL_SPLIT_FORBIDDEN', '训练仅接受train/calibration；封存test必须使用独立评估入口')
            if m.get('source_verification') not in {'verified','synthetic'}: fail('TEMPORAL_SOURCE_UNVERIFIED', '原始来源未核验，不能用于研究训练/评估')
            if m['data_origin'] == 'synthetic_features' and bundle.profile.mode != 'demo': fail('TEMPORAL_SYNTHETIC_FORBIDDEN', '合成研究仅用于demo')
            if m['source_hash'] in seen: fail('TEMPORAL_DUPLICATE_SOURCE', '同源存在多个导入；请选择一个完整导入，避免重复加权或拼接')
            seen.add(m['source_hash'])
            split = session.scalar(select(SourceSplit).where(SourceSplit.tenant_id == bundle.profile.tenant_id,
                                                           SourceSplit.source_hash == m['source_hash']))
            if split is None or split.split != m['split']: fail('TEMPORAL_SPLIT_CHANGED', '来源分区与导入快照不一致，请重新导入')
            provenance = source_provenance(bundle, m)
            group = provenance['capture_session_id'] if provenance else m['source_hash']
            if group in groups:
                fail('TEMPORAL_DUPLICATE_SESSION', '同一采集会话只可选择一个完整导入；截片或转码不能增加独立来源数')
            groups.add(group)
            space = FeatureSpace.model_validate(m['feature_space'])
            if space.identifier() != m['feature_space_id']: fail('TEMPORAL_SPACE_INVALID', '特征空间摘要错误')
            keys.add((m['tenant_id'], m['scene_id'], m['view_id'], m['feature_space_id'], m['data_origin']))
            total_rows += m['total_rows']; total_values += m['total_rows']*space.dimension
            if space.dimension > settings.max_dimension or total_rows > settings.max_rows or total_values > settings.max_feature_values:
                fail('TEMPORAL_RESOURCE_LIMIT', '研究输入超过配置的行数、维度或数值总量上限')
            entries.append({'import_id': identifier, 'manifest_hash': content_hash(m), 'cache_hash': m['feature_cache']['sha256'],
                'source_hash': m['source_hash'], 'source_group_id': m['source_group_id'], 'split': m['split'],
                'rows': m['total_rows'], 'eligible_rows': m['eligible_rows'],
                'episode_count': sum(e['complete'] for e in m['episodes']),
                'encoder_evidence': m['encoder_evidence'], 'import_config_hash': job.config_hash})
            if provenance is not None:
                entries[-1]['provenance'] = provenance
            counts[m['split']] += 1
    if len(keys) != 1: fail('TEMPORAL_SCOPE_MISMATCH', '禁止混合租户、场景、视角、特征空间或真实/合成来源')
    requirements = {'train':settings.min_train_sources, 'calibration':settings.min_calibration_sources,
                    'test':settings.min_test_sources}
    for split in allowed_splits:
        if counts[split] < requirements[split]: fail('TEMPORAL_INSUFFICIENT_SOURCES', f'{split}独立采集来源不足，需要至少{requirements[split]}个')
    tenant, scene, view, feature, origin = keys.pop()
    # Import IDs are random database identifiers. Numerical fitting order must
    # follow immutable source content so a fresh database reproduces weights.
    entries.sort(key=lambda entry: entry['source_hash'])
    snapshot = {'protocol_version':settings.protocol_version, 'tenant_id':tenant, 'scene_id':scene,
                'view_id':view, 'feature_space_id':feature, 'data_origin':origin, 'entries':entries,
                'source_counts':dict(counts)}
    return {**snapshot, 'hash':content_hash(snapshot)}


@dataclass
class Sequence:
    manifest: dict
    vectors: np.ndarray
    batches: list
    rows: list[int]
    max_abs_feature: float = 1e6


@dataclass(frozen=True)
class Window:
    sequence: int
    history: tuple[int, ...]
    targets: tuple[int, ...]
    issued_ms: int
    split: str


def load_sequences(bundle, database, import_root, snapshot):
    """Validate cache bytes before mmap. Only IDs fixed by the snapshot are read."""
    sequences = []
    for entry in snapshot['entries']:
        with database.session() as session:
            row = session.scalar(select(ImportJob).where(ImportJob.id == entry['import_id'],
                ImportJob.tenant_id == snapshot['tenant_id'], ImportJob.scene_id == snapshot['scene_id']))
            if row is None or row.status != 'succeeded': fail('TEMPORAL_SNAPSHOT_CHANGED', '研究输入已不可用')
            m = json.loads(row.manifest_json)
        if content_hash(m) != entry['manifest_hash']: fail('TEMPORAL_SNAPSHOT_CHANGED', '数据清单已变化，不能复用旧快照')
        path = Path(import_root) / entry['import_id'] / 'vectors.npy'
        if file_hash(path) != entry['cache_hash']: fail('TEMPORAL_CACHE_CORRUPTED', '特征缓存与快照不一致')
        try: vectors = np.load(path, allow_pickle=False, mmap_mode='r')
        except (ValueError, OSError): fail('TEMPORAL_CACHE_INVALID', '特征缓存不能读取')
        if vectors.shape != (m['total_rows'],m['feature_space']['dimension']) or vectors.dtype.kind != 'f': fail('TEMPORAL_CACHE_INVALID', '特征形状与清单不一致')
        # Validate values when exposed to the model or observed as a future target.
        # The mmap itself and byte digest do not fit or summarize future vectors.
        groups = defaultdict(list)
        for raw in m['feature_batches']:
            batch = FeatureBatch.model_validate(raw)
            if (batch.tenant_id,batch.scene_id,batch.feature_space_id,batch.split,batch.source_hash) != (
                snapshot['tenant_id'],snapshot['scene_id'],snapshot['feature_space_id'],entry['split'],entry['source_hash']):
                fail('TEMPORAL_BATCH_MISMATCH', '特征记录与来源快照不一致')
            groups[(batch.episode_id,batch.time_domain_id,batch.entity_id)].append(batch)
        for batches in groups.values():
            batches.sort(key=lambda b:b.pts_start_ms)
            rows = [int(b.vector_uri.rsplit('#row=',1)[1]) for b in batches]
            if len(set(rows))!=len(rows) or any(i<0 or i>=len(vectors) for i in rows): fail('TEMPORAL_ROW_INVALID', '特征行引用错误')
            for b,i in zip(batches,rows):
                if b.vector_uri != f"features://{entry['cache_hash']}#row={i}" or not m['rows'][i]['eligible']:
                    fail('TEMPORAL_ROW_INVALID', '特征行引用或质量屏蔽不一致')
                if (b.pts_start_ms,b.pts_end_ms,b.episode_id,b.time_domain_id)!=(m['rows'][i]['start_ms'],m['rows'][i]['end_ms'],m['rows'][i]['episode_id'],m['rows'][i]['time_domain_id']):
                    fail('TEMPORAL_ROW_INVALID', '特征时间/周期与源记录不一致')
            sequences.append(Sequence(m,vectors,batches,rows,bundle.config.temporal.max_abs_feature))
    return sequences


def has_barrier(seq, left, right, settings):
    """One continuity policy for windows, transitions and discovery segments."""
    if any(seq.rows[j] != seq.rows[j-1]+1 or
           seq.batches[j].pts_start_ms <= seq.batches[j-1].pts_start_ms or
           seq.batches[j].pts_start_ms-seq.batches[j-1].pts_end_ms > settings.max_gap_ms
           for j in range(left+1, right+1)):
        return True
    start, end = seq.batches[left].pts_start_ms, seq.batches[right].pts_end_ms
    return any(d['start_ms'] < end and d['end_ms'] > start and
               d.get('time_domain_id', seq.batches[left].time_domain_id) == seq.batches[left].time_domain_id
               for d in seq.manifest.get('discarded_clips', []))


def window_refs(sequences, learning, settings):
    windows, rejected = [], Counter()
    horizons = [round(h*1000) for h in learning.forecast_horizons_seconds]
    for si, seq in enumerate(sequences):
        starts = np.array([b.pts_start_ms for b in seq.batches])
        for end in range(learning.history_windows-1,len(starts)):
            history = tuple(range(end-learning.history_windows+1,end+1))
            issued = max(seq.batches[i].pts_end_ms for i in history)
            targets = tuple(int(np.searchsorted(starts,issued+h)) for h in horizons)
            if any(t>=len(starts) for t in targets): rejected['future_not_available']+=1; continue
            if any(starts[t]-(issued+h)>settings.target_tolerance_ms for t,h in zip(targets,horizons)):
                rejected['target_outside_tolerance']+=1; continue
            if has_barrier(seq, history[0], max(targets), settings):
                rejected['quality_or_continuity_gap']+=1; continue
            if any(not issued < seq.batches[t].pts_start_ms < seq.batches[t].pts_end_ms for t in targets):
                fail('TEMPORAL_CAUSALITY', '目标区间与已见历史重叠')
            windows.append(Window(si,history,targets,issued,seq.batches[0].split))
    # Deterministic even sampling per sequence avoids silently taking only the
    # first source. Never move windows between preassigned partitions.
    selected=[]
    for split in sorted({w.split for w in windows}):
        by_sequence=defaultdict(list)
        for w in windows:
            if w.split==split: by_sequence[w.sequence].append(w)
        candidates=[]
        for group in by_sequence.values():
            quota=max(1,settings.max_pairs_per_split//len(by_sequence))
            candidates.extend(group[i] for i in np.linspace(0,len(group)-1,min(len(group),quota),dtype=int))
        selected.extend(candidates[:settings.max_pairs_per_split])
        rejected['resource_subsampled']+=sum(map(len,by_sequence.values()))-len(candidates[:settings.max_pairs_per_split])
    return selected, dict(rejected)


def values(sequence, indices):
    selected=np.array(sequence.vectors[[sequence.rows[i] for i in indices]],dtype=np.float64)
    if not np.isfinite(selected).all() or np.max(np.abs(selected))>sequence.max_abs_feature:
        fail('TEMPORAL_FEATURE_RANGE','特征数值无效或超过稳定计算范围')
    return selected
