"""Physical view identity and human-reviewed intervals, independent of file names."""
from typing import Literal
from datetime import datetime
from pydantic import Field, AwareDatetime, model_validator, field_validator
from .models import Strict
from .scenes import Identifier


class CaptureIdentity(Strict):
    physical_camera_id: Identifier | None = None
    stable_view_id: Identifier | None = None
    review_status: Literal['pending', 'confirmed'] = 'pending'

    @model_validator(mode='after')
    def confirmed(self):
        if self.review_status == 'confirmed' and not (self.physical_camera_id and self.stable_view_id):
            raise ValueError('确认视角需要真实机位和稳定视角标识')
        return self


class Acquisition(Strict):
    capture_session_id: Identifier | None = None
    captured_at: AwareDatetime | None = None
    review_status: Literal['pending', 'confirmed'] = 'pending'

    @field_validator('captured_at', mode='before')
    @classmethod
    def parse_capture_time(cls, value):
        if isinstance(value, str):
            try: return datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError: raise ValueError('采集时间必须为带时区 ISO8601 时间') from None
        return value

    @model_validator(mode='after')
    def confirmed(self):
        if self.review_status == 'confirmed' and not (self.capture_session_id and self.captured_at):
            raise ValueError('确认采集需要真实会话及带时区采集时间')
        return self


class ViewInterval(Strict):
    domain_index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    status: Literal['usable', 'excluded', 'unreviewed']
    reason: str = Field(min_length=1, max_length=1000)
    reviewer: Identifier | None = None

    @model_validator(mode='after')
    def interval(self):
        if self.start_ms >= self.end_ms: raise ValueError('区间结束必须晚于开始')
        if self.status != 'unreviewed' and not self.reviewer: raise ValueError('已审查区间需要审查人')
        return self
