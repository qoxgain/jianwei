"""Explicit preregistration; configuration defaults never impersonate approval."""
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator

from .dataset import _timestamp
from .learning import Sha
from .models import Strict
from .scenes import Identifier


class ResearchProtocol(Strict):
    protocol_id: Identifier
    registered_by: Identifier
    registered_at: AwareDatetime
    primary_metric: Literal['source_macro_mse'] = 'source_macro_mse'
    min_independent_sessions: int = Field(ge=2, le=100)
    confidence_level: float = Field(default=.95, ge=.8, le=.999)
    bootstrap_replicates: int = Field(default=2000, ge=200, le=10000)
    bootstrap_seed: int = Field(default=23, ge=0, le=2**32-1)
    minimum_relative_improvement: float = Field(ge=0, lt=1)
    controls: list[Literal['persistence', 'shuffled_ridge']] = Field(
        default_factory=lambda: ['persistence', 'shuffled_ridge'], min_length=2, max_length=2)
    # When a registry records an original and its derivatives, explicitly choose
    # one representation per session. Keep the complete lineage in the registry.
    source_sha256s: list[Sha] | None = Field(default=None, min_length=4, max_length=1000)

    _registered = field_validator('registered_at', mode='before')(_timestamp)

    @field_validator('controls')
    @classmethod
    def both_controls(cls, value):
        if value != ['persistence', 'shuffled_ridge']:
            raise ValueError('必须预注册 persistence 和 shuffled_ridge 两个固定对照')
        return value

    @field_validator('source_sha256s')
    @classmethod
    def unique_sources(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError('协议来源不能重复')
        return sorted(value) if value is not None else value
