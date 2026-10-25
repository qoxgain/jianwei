"""A deterministic ingestion transaction producing portable provenance records.

The database worker owns job state and source-group split assignment. This module
only reads registered input paths, writes an atomic feature cache, and returns an
auditable manifest. External NPZ vectors never imply an executed visual encoder.
"""
from __future__ import annotations
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import numpy as np

from packages.config.errors import ContractError
from packages.config.loader import resolve_path
from packages.contracts.ingestion import ImportRequest
from packages.contracts.learning import FeatureBatch, FeatureSpace
from packages.knowledge.service import content_hash
from .npz import read_npz
from .time import time_domains, build_episodes
from .video import decode_video, clip_tensor, close_frames


def file_hash(path):
    try:
        with Path(path).open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    except OSError:
        raise ContractError('IMPORT_FILE_MISSING', '导入文件不存在或无法读取，请重新上传') from None


def validate_input(bundle, scene_id, request):
    if getattr(bundle.config, 'ingestion', None) is None:
        raise ContractError('INGESTION_DISABLED', '当前配置未启用数据导入')
    if scene_id not in bundle.scenes:
        raise ContractError('SCENE_NOT_FOUND', '场景不存在')
    spec = bundle.scenes[scene_id]
    source = next((s for s in bundle.profile.sources if s.id == request.source_id), None)
    if source is None or scene_id not in getattr(source, 'scene_ids', [getattr(source, 'scene_id', None)]):
        raise ContractError('SOURCE_SCENE_MISMATCH', '来源未注册或未绑定当前场景')
    if request.data_origin == 'synthetic_features':
        if bundle.profile.mode != 'demo' or source.type != 'synthetic':
            raise ContractError('SYNTHETIC_SOURCE_FORBIDDEN', '合成输入仅可通过 demo 中注册的合成来源导入')
        if request.source_upload_id is not None:
            raise ContractError('SYNTHETIC_SOURCE_CONFLICT', '合成特征不能绑定真实原视频作为来源')
    elif source.type == 'synthetic':
        raise ContractError('SOURCE_ORIGIN_MISMATCH', '真实文件必须选择文件来源，不能使用合成来源')
    required = set(spec.session_policy.required_context_keys)
    if not required <= request.context.keys():
        raise ContractError('CONTEXT_REQUIRED', '缺少场景要求的上下文字段',
                            [{'path': f'context.{key}', 'message': '必填字段'} for key in sorted(required-request.context.keys())])
    if not request.context.keys() <= spec.context_schema.keys():
        raise ContractError('CONTEXT_UNKNOWN', '上下文包含场景未声明的字段')
    allowed = {'string': (str,), 'integer': (int,), 'number': (int, float), 'boolean': (bool,)}
    for key, value in request.context.items():
        if type(value) not in allowed[spec.context_schema[key].type]:
            raise ContractError('CONTEXT_TYPE', '上下文字段类型与场景契约不一致',
                                [{'path': f'context.{key}', 'message': spec.context_schema[key].type}])
        if key in required and isinstance(value, str) and not value.strip():
            raise ContractError('CONTEXT_REQUIRED', '必填上下文字符串不能为空')
    return spec, source


def _cache(vectors, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.vectors-', dir=output_dir)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            np.save(stream, vectors, allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        digest = file_hash(temporary)
        os.replace(temporary, output_dir / 'vectors.npy')
        return {'sha256': digest, 'uri': f'features://{digest}',
                'rows': len(vectors), 'dimension': int(vectors.shape[1]), 'dtype': str(vectors.dtype)}
    finally:
        temporary.unlink(missing_ok=True)


def _utc(domain, pts):
    if not domain['capture_start_utc']:
        return None
    try:
        return (datetime.fromisoformat(domain['capture_start_utc']) +
                timedelta(milliseconds=int(pts)-domain['start_ms'])).astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise ContractError('CLOCK_ANCHOR_RANGE', '采集时钟换算超出有效范围') from None


def _episode_for(episodes, domain, start, end):
    for episode in episodes:
        if episode['domain_index'] == domain and episode['start_ms'] <= start < end <= episode['end_ms']:
            return episode
    return None


def _records(bundle, scene_id, request, manifest, space, pts, durations, row_domains, scores, cache):
    rows, batches, rejected = [], [], []
    episodes, domains = manifest['episodes'], manifest['domains']
    for index, (start, duration, domain_index) in enumerate(zip(pts, durations, row_domains)):
        start, end = int(start), int(start + duration)
        domain = domains[domain_index]
        episode = _episode_for(episodes, domain_index, start, end)
        reason = None
        if episode is None: reason = 'outside_complete_episode'
        elif not episode['complete']: reason = 'incomplete_episode'
        elif scores[index] < bundle.config.video.quality_min: reason = 'quality_below_threshold'
        row = {'row': index, 'time_domain_id': domain['time_domain_id'], 'start_ms': start, 'end_ms': end,
               'capture_start_utc': _utc(domain, start), 'capture_end_utc': _utc(domain, end),
               'episode_id': episode['episode_id'] if episode else None, 'quality': float(scores[index]),
               'eligible': reason is None}
        rows.append(row)
        if reason:
            rejected.append({'row': index, 'reason': reason})
            continue
        batch = FeatureBatch(contract_version='3.1', tenant_id=bundle.profile.tenant_id, scene_id=scene_id,
            source_id=request.source_id, source_video_id=manifest['source_video_id'], source_hash=manifest['source_hash'],
            episode_id=episode['episode_id'], time_domain_id=domain['time_domain_id'], entity_id=None,
            pts_start_ms=start, pts_end_ms=end, context=request.context,
            feature_space=space, feature_space_id=space.identifier(), quality=float(scores[index]),
            vector_uri=f"{cache['uri']}#row={index}", data_origin=request.data_origin, split=request.requested_split)
        batches.append(batch.model_dump())
    manifest.update(rows=rows, feature_batches=batches, rejected_rows=rejected,
                    eligible_rows=len(batches), total_rows=len(rows), feature_cache=cache,
                    vector_uri=cache['uri'], feature_space=space.model_dump(), feature_space_id=space.identifier())


def _base(bundle, scene_id, request, source, artifact_hash, source_hash, verification):
    return {'manifest_version': '3.1', 'stage': 'P0.2', 'status': 'succeeded', 'blocker': None,
            'tenant_id': bundle.profile.tenant_id, 'scene_id': scene_id, 'source_id': source.id,
            'source_hash': source_hash, 'source_video_id': 'source_' + source_hash,
            'artifact_hash': artifact_hash, 'source_ref': 'upload:' + (request.source_upload_id or request.upload_id),
            'source_verified': verification == 'verified', 'source_verification': verification,
            'data_origin': request.data_origin, 'split': request.requested_split,
            'view_id': content_hash({'source_id': source.id, 'roi_xyxy': source.roi_xyxy}),
            'roi_xyxy': source.roi_xyxy, 'context': request.context,
            'feature_space': None, 'feature_space_id': None, 'feature_cache': None, 'vector_uri': None,
            'feature_batches': [], 'rows': [], 'rejected_rows': [], 'eligible_rows': 0, 'total_rows': 0,
            'training_executed': False, 'semantic_accuracy': None}


def process_import(bundle, scene_id: str, request: ImportRequest, input_path: Path,
                   encoder_artifact_path: Path | None = None, source_path: Path | None = None,
                   output_dir: Path | None = None) -> dict:
    """Process validated uploads; caller owns final persistence and split locking."""
    spec, source = validate_input(bundle, scene_id, request)
    settings = bundle.config.ingestion
    if output_dir is None:
        raise ContractError('IMPORT_OUTPUT_REQUIRED', '导入需要受管理的输出目录')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_hash = file_hash(input_path)
    if request.format == 'npz':
        if encoder_artifact_path is None:
            raise ContractError('ENCODER_ARTIFACT_REQUIRED', 'NPZ 需要编码器指纹文件；该文件仅计算哈希，不执行')
        if file_hash(encoder_artifact_path) != request.feature_space.encoder_sha:
            raise ContractError('ENCODER_HASH_MISMATCH', '编码器指纹文件的实际 SHA 与特征空间声明不符')
        if request.feature_space.roi_policy_hash != content_hash({'source_id': source.id, 'roi_xyxy': source.roi_xyxy}):
            raise ContractError('ROI_HASH_MISMATCH', '特征空间的 ROI 策略与配置的来源视角不一致')
        vectors, pts, durations = read_npz(input_path, settings, request.feature_space.dimension)
        domains, gaps, row_domains = time_domains(pts, durations, settings, request.clock_anchors)
        if request.data_origin == 'synthetic_features':
            verification = 'synthetic'
            if not request.declared_source_hash:
                raise ContractError('SOURCE_HASH_REQUIRED', '合成特征需要独立的合成源标识 SHA，不能用 NPZ 哈希冒充')
            source_hash = request.declared_source_hash
        elif source_path is not None:
            source_hash, verification = file_hash(source_path), 'verified'
            if request.declared_source_hash and request.declared_source_hash != source_hash:
                raise ContractError('SOURCE_HASH_MISMATCH', '原视频实际 SHA 与来源声明不一致')
            # Verify this really is a decodable video, and intervals fit its time domains.
            with tempfile.TemporaryDirectory(prefix='source-', dir=output_dir) as work, ExitStack() as frames_cleanup:
                pixels, source_pts, source_durations, _, _, _ = decode_video(source_path, Path(work), settings)
                frames_cleanup.callback(close_frames, pixels)
                source_domains, _, _ = time_domains(source_pts, source_durations, settings, [])
            if request.source_domain_map is not None and len(request.source_domain_map) != len(domains):
                raise ContractError('SOURCE_DOMAIN_MAP', '原视频时间域映射必须与特征时间域逐项对应')
            previous = 0
            for index, domain in enumerate(domains):
                minimum = previous + int(index > 0 and gaps[index-1]['reason'] == 'pts_reset')
                candidates = [i for i, original in enumerate(source_domains) if i >= minimum and
                    original['start_ms'] <= domain['start_ms'] < domain['end_ms'] <= original['end_ms']]
                if request.source_domain_map is not None:
                    candidate = request.source_domain_map[index]
                    if candidate not in candidates:
                        raise ContractError('SOURCE_TIME_MISMATCH', '声明的原视频时间域不能覆盖特征时间区间或顺序不一致')
                elif len(candidates) == 1:
                    candidate = candidates[0]
                elif len(candidates) > 1:
                    raise ContractError('SOURCE_DOMAIN_AMBIGUOUS', '原视频存在相同 PTS 的多个时间域，请显式提供 source_domain_map')
                else:
                    raise ContractError('SOURCE_TIME_MISMATCH', '特征时间区间不在原视频对应时间域内')
                original_id = source_domains[candidate]['time_domain_id']
                domain['source_time_domain_id'] = original_id
                domain['local_time_domain_id'] = domain['time_domain_id']
                # Local d0 in two separate NPZ files is not a source-clock
                # identity: each may describe a different PTS-reset domain.
                # Preserve the original clock and the continuous feature span.
                # Using only original_id would instead merge local gap segments.
                # Cross-span pairing requires an explicit continuity rebuild.
                domain['time_domain_id'] = f"{original_id}.segment_{domain['start_ms']}_{domain['end_ms']}"
                previous = candidate
        else:
            source_hash, verification = request.declared_source_hash, 'declared'
            if not source_hash:
                raise ContractError('SOURCE_HASH_REQUIRED', '外部特征缺少原视频时必须声明来源 SHA，且只能保留为未分区')
            if request.requested_split != 'unassigned':
                raise ContractError('SOURCE_UNVERIFIED', '未核验原视频的特征只能保留为未分区，不能用于训练或评测')
        manifest = _base(bundle, scene_id, request, source, artifact_hash, source_hash, verification)
        manifest.update(domains=domains, gaps=gaps, episodes=build_episodes(spec, request, domains),
            quality={'method': 'numeric_validation_only', 'score': 1.0, 'visual_quality_checked': False,
                     'semantic_accuracy': None},
            encoder_evidence={'artifact_sha_verified': True, 'execution': 'not_executed',
                              'vector_production': 'externally_declared',
                              'note': '文件指纹一致不能证明该编码器实际生成了这些外部向量'})
        cache = _cache(vectors, output_dir)
        _records(bundle, scene_id, request, manifest, request.feature_space, pts, durations, row_domains,
                 np.ones(len(pts)), cache)
        return manifest

    source_hash = artifact_hash
    if request.declared_source_hash and request.declared_source_hash != source_hash:
        raise ContractError('SOURCE_HASH_MISMATCH', '原视频实际 SHA 与来源声明不一致')
    verification = 'synthetic' if request.data_origin == 'synthetic_features' else 'verified'
    manifest = _base(bundle, scene_id, request, source, artifact_hash, source_hash, verification)
    manifest['input_format'] = 'mp4'
    with tempfile.TemporaryDirectory(prefix='decode-', dir=output_dir) as work, ExitStack() as frames_cleanup:
        pixels, frame_pts, frame_durations, frame_scores, quality, video_info = decode_video(input_path, Path(work), settings)
        frames_cleanup.callback(close_frames, pixels)
        domains, gaps, frame_domains = time_domains(frame_pts, frame_durations, settings, request.clock_anchors)
        episodes = build_episodes(spec, request, domains)
        manifest.update(domains=domains, gaps=gaps, episodes=episodes, quality=quality, video=video_info,
                        encoder_evidence={'execution': 'not_executed', 'vector_production': 'not_available'})
        try:
            if bundle.config.encoder.adapter != 'onnx':
                raise ContractError('ENCODER_ADAPTER_UNAVAILABLE', '已完成视频解码；请配置受支持的冻结 ONNX 编码器，或导入已有 NPZ 特征')
            if not bundle.config.encoder.weights_sha256:
                raise ContractError('ENCODER_SHA_NOT_PINNED', '冻结编码器须在配置中锁定实际文件 SHA256 后才能提取特征')
            from packages.encoders import load_encoder
            from packages.encoders import pretrained
            tensor_builder = clip_tensor
            official_recipe = bundle.config.encoder.preprocessing_version == pretrained.PREPROCESSING_VERSION
            if official_recipe:
                if (settings.encoder_input_size != pretrained.INPUT_SIZE or
                        bundle.config.video.frames_per_clip != pretrained.FRAMES_PER_CLIP or
                        bundle.config.encoder.feature_dim != pretrained.FEATURE_DIM):
                    raise ContractError('ENCODER_PREPROCESSING_INVALID', '正式 R3D-18 输入须为 16 帧、112×112，输出 512 维；请检查 system.yaml')
                tensor_builder = pretrained.clip_tensor
            encoder = load_encoder(resolve_path(bundle.root, bundle.profile.encoder_weights),
                bundle.config.encoder.weights_sha256, bundle.config.encoder.feature_dim,
                settings.encoder_input_size, settings.encoder_threads)
        except ContractError as error:
            manifest.update(status='blocked', blocker=error.detail())
            return manifest
        clip_ms = int(round(bundle.config.video.clip_seconds * 1000))
        stride_ms = int(round(bundle.config.video.stride_seconds * 1000))
        if min(clip_ms, stride_ms) < 1:
            raise ContractError('VIDEO_SAMPLING_INVALID', '片段长度与步长至少需要 1 毫秒')
        candidate_count = sum(max(0, (e['end_ms']-e['start_ms']-clip_ms)//stride_ms+1)
                              for e in episodes if e['complete'])
        if candidate_count > settings.max_rows:
            raise ContractError('CLIP_COUNT_LIMIT', '候选片段数量超过配置限制，请增加步长或缩短视频')
        vectors, starts, durations, clip_domains, scores = [], [], [], [], []
        discarded = []
        for episode in sorted(episodes, key=lambda e: (e['domain_index'], e['start_ms'])):
            if not episode['complete']:
                continue
            domain_index = episode['domain_index']
            domain = domains[domain_index]
            domain_indices = np.arange(domain['first_row'], domain['last_row']+1)
            domain_pts = frame_pts[domain_indices]
            for start in range(episode['start_ms'], episode['end_ms']-clip_ms+1, stride_ms):
                end = start+clip_ms
                left, right = np.searchsorted(domain_pts, [start, end], side='left')
                indices = domain_indices[left:right]
                if len(indices) == 0:
                    discarded.append({'start_ms': start, 'end_ms': end, 'reason': 'no_frames'})
                    continue
                targets = np.linspace(start, end, bundle.config.video.frames_per_clip, endpoint=False)
                selected = indices[np.abs(frame_pts[indices, None]-targets[None, :]).argmin(axis=0)]
                missing = 1-len(np.unique(selected))/len(selected)
                score = float(frame_scores[selected].mean()) * (1-missing)
                if missing > bundle.config.video.missing_frame_fraction_max or score < bundle.config.video.quality_min:
                    discarded.append({'start_ms': start, 'end_ms': end, 'time_domain_id': domain['time_domain_id'],
                                      'reason': 'quality_or_missing_frames', 'quality': score, 'missing_fraction': missing})
                    continue
                vector = encoder.encode(tensor_builder(pixels, selected, source.roi_xyxy, settings.encoder_input_size))[0]
                vectors.append(vector)
                starts.append(start); durations.append(clip_ms); clip_domains.append(domain_index); scores.append(score)
                if len(vectors) > settings.max_rows or len(vectors)*bundle.config.encoder.feature_dim*4 > settings.max_expanded_bytes:
                    raise ContractError('FEATURE_SIZE_LIMIT', '提取特征超过配置上限，请缩短视频或减少采样')
    manifest['discarded_clips'] = discarded
    if not vectors:
        manifest.update(status='blocked', blocker={'code': 'NO_USABLE_CLIPS', 'message': '没有满足完整周期、片段长度与质量门槛的片段'})
        return manifest
    preprocessing = {'implementation_sha': file_hash(Path(__file__).with_name('video.py')),
                     'version': bundle.config.encoder.preprocessing_version, 'input_size': settings.encoder_input_size,
                     'decode_width': settings.decode_width, 'color': 'RGB_float32_0_1',
                     'resize': 'bounded_bilinear_decode_then_roi_nearest'}
    if official_recipe:
        preprocessing.update(
            recipe_implementation_sha=file_hash(Path(pretrained.__file__)),
            resize='bounded_bilinear_decode_then_roi_float_bilinear_128x171_center112',
            normalization='official_mean_std_embedded_in_onnx')
    manifest['preprocessing'] = preprocessing
    space = FeatureSpace(contract_version='3.1', encoder_sha=encoder.weights_sha,
        preprocessing_sha=content_hash(preprocessing), sampling_sha=content_hash({
            'clip_ms': clip_ms, 'stride_ms': stride_ms, 'frames_per_clip': bundle.config.video.frames_per_clip,
            'method': 'nearest_original_pts_v1'}),
        roi_policy_hash=content_hash({'source_id': source.id, 'roi_xyxy': source.roi_xyxy}),
        pooling=bundle.config.encoder.pooling, dimension=bundle.config.encoder.feature_dim)
    manifest['encoder_evidence'] = {'artifact_sha_verified': True, 'execution': 'frozen_onnx_cpu',
                                    'vector_production': 'executed_from_this_source', 'weights_sha': encoder.weights_sha}
    cache = _cache(np.stack(vectors), output_dir)
    _records(bundle, scene_id, request, manifest, space, np.array(starts), np.array(durations), clip_domains, scores, cache)
    return manifest
