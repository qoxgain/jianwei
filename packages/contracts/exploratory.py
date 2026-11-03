"""Opt-in retrospective research controls, never formal sealed-test admission."""
from typing import Literal
from pydantic import Field, model_validator
from .models import Strict


class ExploratorySettings(Strict):
    enabled: bool = False
    evidence_class: Literal['retrospective_development'] = 'retrospective_development'
    formal_research_access: Literal[False] = False
    seeds: list[int] = Field(min_length=3,max_length=10)
    history_lengths: list[int] = Field(min_length=1,max_length=4)
    guard_ms: int = Field(ge=2000,le=60000)
    minimum_windows: int = Field(ge=8,le=512)
    dilations: list[int] = Field(min_length=1,max_length=4)
    motion_quantile: float = Field(gt=0,lt=1)
    trace_limit: int = Field(ge=10,le=300)

    @model_validator(mode='after')
    def bounded(self):
        if len(set(self.seeds))!=len(self.seeds) or any(type(s) is not int or not 0<=s<2**32 for s in self.seeds):
            raise ValueError('Research seeds must be distinct bounded integers')
        if len(set(self.history_lengths))!=len(self.history_lengths) or any(h not in (1,8,16,32) for h in self.history_lengths):
            raise ValueError('Supported histories are 1/8/16/32, no duplicate cohort')
        if self.dilations != [2**i for i in range(len(self.dilations))]:
            raise ValueError('Causal multiscale dilations must start at one and double')
        return self
