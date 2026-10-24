"""Deterministic, explicitly synthetic input for exercising the real import path.

The encoder artifact describes an analytical generator. It is hashed, never
executed as a model, and must not be presented as pretrained visual weights.
"""
from pathlib import Path
import hashlib
import json
import numpy as np

from packages.config.errors import ContractError
from packages.contracts.learning import FeatureSpace
from packages.knowledge.service import content_hash


def build_example(bundle, scene_id: str, directory: Path) -> dict:
    if bundle.profile.mode != 'demo':
        raise ContractError('EXAMPLE_DEMO_ONLY', '合成示例仅供 demo 环境使用')
    sources = [s for s in bundle.profile.sources if s.type == 'synthetic' and scene_id in getattr(s, 'scene_ids', [getattr(s, 'scene_id', None)])]
    if not sources:
        raise ContractError('EXAMPLE_SOURCE_MISSING', '当前场景没有注册合成示例来源')
    scene, source = bundle.scenes[scene_id], sources[0]
    directory.mkdir(parents=True, exist_ok=True)
    dimension = bundle.config.encoder.feature_dim
    recipe = {'version': 'jianwei-analytical-fixture-v1', 'kind': 'synthetic_features',
              'formula': 'sin((row+1)*(column+1)*0.01)*0.25',
              'dimension': dimension, 'rows': 120,
              'notice': 'Analytical test fixture, not a pretrained visual encoder.'}
    encoder_path = directory / 'synthetic-encoder.json'
    encoder_path.write_text(json.dumps(recipe, sort_keys=True, separators=(',', ':')) + '\n')
    encoder_sha = hashlib.sha256(encoder_path.read_bytes()).hexdigest()
    features = (np.sin(np.arange(1, 121, dtype=np.float64)[:, None] *
                       np.arange(1, dimension + 1, dtype=np.float64)[None, :] * 0.01) * 0.25).astype(np.float32)
    input_path = directory / 'example.npz'
    np.savez_compressed(input_path, features=features, pts_ms=np.arange(120, dtype=np.int64) * 1000,
                        durations_ms=np.full(120, 1000, dtype=np.int64))
    space = FeatureSpace(contract_version='3.1', encoder_sha=encoder_sha,
                         preprocessing_sha=content_hash({'input': 'analytical_time_index', 'version': 1}),
                         sampling_sha=content_hash({'stride_ms': 1000, 'duration_ms': 1000}),
                         roi_policy_hash=content_hash({'source_id': source.id, 'roi_xyxy': source.roi_xyxy}),
                         pooling='analytical_fixture_no_visual_pooling', dimension=dimension)
    context = {key: {'string': 'demo_' + key, 'integer': 1, 'number': 1.0, 'boolean': True}[field.type]
               for key, field in scene.context_schema.items()}
    policy = scene.episode_policy.mode
    episodes = []
    origin = None
    if policy not in {'fixed_window', 'manual_or_fixed_window'}:
        origin = 'external' if policy == 'external_cycle' else 'manual'
        episodes = [{'episode_id': f'example_cycle_{i + 1}', 'domain_index': 0,
                     'start_ms': i * 60000, 'end_ms': (i + 1) * 60000} for i in range(2)]
    fields = {'format': 'npz', 'source_id': source.id, 'data_origin': 'synthetic_features',
              'declared_source_hash': content_hash({'synthetic_source': recipe}),
              'feature_space': space.model_dump(), 'requested_split': 'unassigned',
              'context': context, 'episodes': episodes, 'episode_origin': origin,
              'clock_anchors': []}
    return {'input_path': input_path, 'encoder_path': encoder_path, 'request_fields': fields}
