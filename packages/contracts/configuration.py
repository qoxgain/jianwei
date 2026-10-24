"""v3.1 configuration; v3.0 remains a strict, explicit compatibility format."""
from typing import Literal
from pydantic import Field, model_validator
from .models import Strict, SystemConfig, Profile, Encoder
from .scenes import Identifier, SceneSpec
from .ingestion import IngestionSettings

class SourceBinding(Strict):
    id: Identifier
    type: Literal['synthetic', 'file', 'rtsp']
    uri: str
    scene_ids: list[Identifier] = Field(min_length=1)
    roi_xyxy: list[float] = Field(min_length=4, max_length=4)

class SceneProfile(Profile):
    sources: list[SourceBinding]  # An unlabeled scene may start before any source is registered.

class SceneEncoder(Encoder):
    adapter: Literal['existing_features', 'videomae', 'vjepa2', 'onnx']

class SceneConfig(SystemConfig):
    config_version: Literal['3.1']
    ingestion: IngestionSettings | None = None
    encoder: SceneEncoder
    knowledge_file: None = None
    session: None = None
    profiles: dict[str, SceneProfile]
    scenes: dict[Identifier, SceneSpec] = Field(min_length=1)

    @model_validator(mode='after')
    def consistency(self):
        if self.active_profile not in self.profiles: raise ValueError('active_profile不存在')
        if self.active_scene not in self.scenes: raise ValueError('active_scene不存在')
        if set(self.profiles) != set(self.media): raise ValueError('每个profile必须有对应media配置')
        if any(h <= 0 for h in self.learning.forecast_horizons_seconds): raise ValueError('未来窗口起点必须大于0')
        ps = self.release.canary_percentages
        if ps != sorted(set(ps)) or ps[-1] != 100 or ps[0] < 1: raise ValueError('灰度比例递增且以100结束')
        for profile in self.profiles.values():
            ids = [s.id for s in profile.sources]
            if len(ids) != len(set(ids)): raise ValueError('重复source_id')
            if profile.mode == 'live' and not profile.auth_enabled: raise ValueError('live必须开启鉴权')
            for source in profile.sources:
                if len(set(source.scene_ids)) != len(source.scene_ids): raise ValueError('重复场景绑定')
                if not set(source.scene_ids) <= self.scenes.keys(): raise ValueError('来源绑定了不存在的场景')
                x1, y1, x2, y2 = source.roi_xyxy
                if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1): raise ValueError('ROI坐标无效')
                if profile.mode != 'demo' and source.type == 'synthetic': raise ValueError('非demo禁止synthetic')
        return self
