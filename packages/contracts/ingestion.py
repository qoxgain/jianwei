"""P0.2 bounded import requests; no semantic labels are required."""
from datetime import datetime
from typing import Literal, Annotated
from pydantic import Field, model_validator
from .models import Strict
from .scenes import Identifier
from .learning import ContextValue, FeatureSpace, Sha
from .capture import Acquisition, ViewInterval

Split = Literal['unassigned', 'train', 'calibration', 'test']

class ImportEpisode(Strict):
    episode_id: Identifier
    domain_index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode='after')
    def interval(self):
        if self.end_ms <= self.start_ms: raise ValueError('周期结束必须晚于开始')
        return self

class ClockAnchor(Strict):
    domain_index: int = Field(ge=0)
    pts_ms: int = Field(ge=0)
    capture_utc: str

    @model_validator(mode='after')
    def timestamp(self):
        try:
            value = datetime.fromisoformat(self.capture_utc.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('采集锚点必须使用 ISO8601 时间') from None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('采集锚点必须包含时区')
        return self

class ImportRequest(Strict):
    upload_id: Identifier
    format: Literal['mp4', 'npz']
    source_id: Identifier
    data_origin: Literal['real_video', 'synthetic_features']
    source_upload_id: Identifier | None = None
    source_domain_map: list[Annotated[int, Field(ge=0)]] | None = Field(default=None, max_length=10000)
    encoder_artifact_id: Identifier | None = None
    declared_source_hash: Sha | None = None
    feature_space: FeatureSpace | None = None
    requested_split: Split = 'unassigned'
    context: dict[Identifier, ContextValue] = Field(default_factory=dict)
    episodes: list[ImportEpisode] = Field(default_factory=list, max_length=10000)
    episode_origin: Literal['manual', 'external'] | None = None
    clock_anchors: list[ClockAnchor] = Field(default_factory=list, max_length=10000)
    acquisition: Acquisition | None = None
    view_intervals: list[ViewInterval] = Field(default_factory=list, max_length=10000)

    @model_validator(mode='after')
    def format_requirements(self):
        if self.format == 'npz' and (self.feature_space is None or self.encoder_artifact_id is None):
            raise ValueError('NPZ 导入需要特征空间与编码器指纹文件')
        if self.format == 'mp4' and (self.feature_space is not None or self.encoder_artifact_id is not None or self.source_upload_id is not None):
            raise ValueError('MP4 特征空间由服务器配置的冻结编码器产生')
        if len({v.domain_index for v in self.clock_anchors}) != len(self.clock_anchors):
            raise ValueError('同一时间域只能有一个采集锚点')
        if len({v.episode_id for v in self.episodes}) != len(self.episodes):
            raise ValueError('周期 ID 重复')
        return self

class IngestionSettings(Strict):
    max_upload_bytes: int = Field(default=67108864, ge=1024, le=2147483648)
    max_expanded_bytes: int = Field(default=134217728, ge=1024, le=2147483648)
    max_rows: int = Field(default=100000, ge=1, le=1000000)
    max_duration_seconds: int = Field(default=600, ge=1, le=86400)
    max_frames: int = Field(default=18000, ge=2, le=1000000)
    max_pixels: int = Field(default=8294400, ge=64, le=67108864)
    probe_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    decode_timeout_seconds: int = Field(default=180, ge=1, le=7200)
    encode_timeout_seconds: int = Field(default=1800, ge=1, le=86400)
    upload_timeout_seconds: int = Field(default=900, ge=1, le=7200)
    max_staging_bytes: int = Field(default=2147483648, ge=1024, le=17179869184)
    decode_width: int = Field(default=160, ge=16, le=1024)
    max_gap_ms: int = Field(default=2000, ge=1, le=600000)
    encoder_input_size: int = Field(default=224, ge=16, le=1024)
    encoder_threads: int = Field(default=1, ge=1, le=32)
    worker_poll_seconds: float = Field(default=0.25, gt=0, le=60)
    quality_luma_min: float = Field(default=4.0, ge=0, le=255)
    quality_luma_max: float = Field(default=251.0, ge=0, le=255)
    quality_std_min: float = Field(default=2.0, ge=0, le=128)

    @model_validator(mode='after')
    def luma(self):
        if self.quality_luma_min >= self.quality_luma_max:
            raise ValueError('亮度质量范围无效')
        return self
