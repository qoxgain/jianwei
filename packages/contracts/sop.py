"""Draft normative references and human review, independent of observed truth."""
from typing import Literal
from pydantic import Field, model_validator, field_validator
from .models import Strict
from .scenes import Identifier
from .learning import Sha


class SopSettings(Strict):
    scene_id: Identifier
    asset_root: str = '.'
    catalog_path: str
    outline_path: str
    mapping_path: str
    media_chunk_bytes: int = Field(default=1048576,ge=4096,le=8388608)
    text_timeout_seconds: int = Field(default=30,ge=1,le=300)
    preview_enabled: bool = True
    preview_long_edge: int = Field(default=640,ge=112,le=1920)
    preview_timeout_seconds: int = Field(default=600,ge=1,le=3600)
    preview_disk_budget_bytes: int = Field(default=536870912,ge=1048576,le=2147483648)
    preview_threads: int = Field(default=1,ge=1,le=8)


class MappingEntry(Strict):
    asset_id: Identifier
    section_ids: list[Identifier] = Field(max_length=100)
    notes: str = Field(default='',max_length=4000)


class RevisionRequest(Strict):
    base_revision: Sha
    reviewer: Identifier
    reason: str = Field(min_length=1,max_length=2000)
    mappings: list[MappingEntry] = Field(min_length=1,max_length=100)
    disputes: list[str] = Field(default_factory=list,max_length=100)

    @field_validator('disputes')
    @classmethod
    def meaningful_disputes(cls, value):
        if any(not item.strip() or len(item)>2000 for item in value):
            raise ValueError('争议项需要非空内容且不超过2000字')
        return value

    @field_validator('reason')
    @classmethod
    def reason_nonblank(cls, value):
        if not value.strip(): raise ValueError('审核理由不能为空白')
        return value


class DecisionRequest(Strict):
    revision: Sha
    action: Literal['approve','revoke']
    reviewer: Identifier
    reason: str = Field(min_length=1,max_length=2000)

    @field_validator('reason')
    @classmethod
    def reason_nonblank(cls, value):
        if not value.strip(): raise ValueError('审核理由不能为空白')
        return value


class EvidenceRecord(Strict):
    revision: Sha
    kind: Literal['observation','prediction','normative_expectation','human_annotation','semantic_candidate']
    asset_id: Identifier
    section_id: Identifier
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    producer: Identifier
    note: str = Field(min_length=1,max_length=8000)
    evidence_type: Literal['visible_operation','closeup_detail','instrument_record']
    evidence_reference: str | None = Field(default=None,max_length=2000)
    @field_validator('note', 'evidence_reference')
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip(): raise ValueError('证据说明和引用不能只有空白')
        return value

    @model_validator(mode='after')
    def interval(self):
        if self.end_ms<=self.start_ms: raise ValueError('证据区间结束必须晚于开始')
        if self.kind in {'prediction','semantic_candidate'} and self.evidence_type=='instrument_record':
            raise ValueError('预测或机器候选不能冒充仪器记录')
        return self
