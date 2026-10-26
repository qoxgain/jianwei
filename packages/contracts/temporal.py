"""Bounded P0.3 research. Requests reference registered imports, never paths."""
from typing import Literal
from pydantic import Field, model_validator
from .models import Strict
from .scenes import Identifier
from .dataset import DatasetManifest


class TemporalSettings(Strict):
    dataset_manifest: DatasetManifest | None = None
    protocol_version: Literal['p03-causal-v1'] = 'p03-causal-v1'
    seed: int = Field(default=23, ge=0, le=2**32-1)
    projection_dim: int = Field(default=16, ge=2, le=64)
    cluster_count: int = Field(default=4, ge=2, le=32)
    cluster_iterations: int = Field(default=20, ge=1, le=100)
    min_segment_windows: int = Field(default=3, ge=1, le=100)
    change_quantile: float = Field(default=0.8, gt=0, lt=1)
    target_tolerance_ms: int = Field(default=1000, ge=0, le=10000)
    max_gap_ms: int = Field(default=2000, ge=0, le=10000)
    max_rows: int = Field(default=12000, ge=32, le=100000)
    max_dimension: int = Field(default=1024, ge=2, le=4096)
    max_feature_values: int = Field(default=8000000, ge=100, le=32000000)
    max_pairs_per_split: int = Field(default=512, ge=8, le=4096)
    min_pairs_per_split: int = Field(default=8, ge=2, le=10000)
    min_train_sources: int = Field(default=2, ge=2, le=100)
    min_calibration_sources: int = Field(default=1, ge=1, le=100)
    min_test_sources: int = Field(default=1, ge=1, le=100)
    max_abs_feature: float = Field(default=1000000, gt=0, le=1e12)
    gradient_clip_norm: float = Field(default=5.0, gt=0, le=100)
    worker_poll_seconds: float = Field(default=0.25, gt=0, le=60)

    @model_validator(mode='after')
    def limits(self):
        if self.min_pairs_per_split > self.max_pairs_per_split:
            raise ValueError('最小样本数超过每分区上限')
        return self


class TrainRequest(Strict):
    import_ids: list[Identifier] = Field(min_length=3, max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.import_ids)) != len(self.import_ids):
            raise ValueError('重复导入ID')
        return self


class EvaluationRequest(Strict):
    model_run_id: Identifier
    import_ids: list[Identifier] = Field(min_length=1, max_length=100)
    sealed_test: Literal[True]

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.import_ids)) != len(self.import_ids):
            raise ValueError('重复导入ID')
        return self


class ReadinessRequest(Strict):
    import_ids: list[Identifier] = Field(min_length=1, max_length=100)
    model_run_id: Identifier | None = None

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.import_ids)) != len(self.import_ids):
            raise ValueError('重复导入ID')
        return self
