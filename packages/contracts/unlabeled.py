"""Explicit, bounded zero-label development research. No formal-test access."""
from typing import Literal
from pydantic import Field,model_validator
from .models import Strict
from .learning import Sha


class UnlabeledSourceEvidence(Strict):
    source_sha256:Sha
    root_source_sha256:Sha
    kind:Literal['field','generated']
    independence_review:Literal['pending','verified']
    near_duplicate_review:Literal['pending','clear']
    capture_session_id:str|None=None
    view_id:str|None=None
    captured_at:str|None=None


class UnlabeledSettings(Strict):
    enabled:bool=False
    feature_manifest:str
    window_ms:int=Field(ge=500,le=8000)
    sample_fps:int=Field(ge=1,le=32)
    fit_fraction:float=Field(gt=.2,le=.7)
    calibration_fraction:float=Field(gt=.05,le=.3)
    purge_ms:int=Field(ge=0,le=30000)
    projection_dim:int=Field(ge=1,le=16)
    change_quantile:float=Field(gt=.5,lt=1)
    scales:list[int]=Field(min_length=1,max_length=4)
    min_segment_windows:int=Field(ge=2,le=20)
    max_segment_windows:int=Field(ge=4,le=100)
    fixed_segment_windows:int=Field(ge=2,le=60)
    dp_penalty_multiplier:float=Field(gt=0,le=10)
    motif_lengths:list[int]=Field(min_length=1,max_length=5)
    min_repeats:int=Field(ge=3,le=10)
    max_prototypes:int=Field(ge=1,le=20)
    motif_neighbor_quantile:float=Field(gt=0,le=.8)
    motif_radius_factor:float=Field(gt=0,le=3)
    min_shape_rms:float=Field(gt=0,le=2)
    context_weight:float=Field(ge=0,le=1)
    activity_quantile:float=Field(ge=0,lt=.8)
    max_profile_windows:int=Field(ge=16,le=1500)
    seeds:list[int]=Field(min_length=3,max_length=10)
    noise_sigma:float=Field(gt=0,le=.5)
    background_mix:float=Field(gt=0,le=.5)
    boundary_tolerance_ms:int=Field(ge=0,le=3000)
    forecast_history:int=Field(ge=2,le=12)
    forecast_lags:list[int]=Field(min_length=1,max_length=3)
    ridge_lambda:float=Field(gt=0,le=100)
    retrieval_k:int=Field(ge=1,le=10)
    min_gate_stability:float=Field(ge=0,le=1)
    min_gate_null_margin:float=Field(ge=0,le=1)
    min_gate_prediction_gain:float=Field(ge=0,lt=1)
    max_gate_coverage:float=Field(gt=.2,lt=1)
    source_evidence:list[UnlabeledSourceEvidence]=Field(default_factory=list,max_length=100)
    formal_research_access:Literal[False]=False
    production_updates:Literal[False]=False

    @model_validator(mode='after')
    def bounded(self):
        if self.fit_fraction+self.calibration_fraction>=.9:raise ValueError('Replay time must remain')
        if self.max_segment_windows<self.min_segment_windows:raise ValueError('Segment duration bounds reversed')
        for values in [self.scales,self.motif_lengths,self.forecast_lags,self.seeds]:
            if len(set(values))!=len(values):raise ValueError('Duplicate research arm/seed')
        if any(not 1<=v<=8 for v in self.scales) or any(not 2<=v<=16 for v in self.motif_lengths):raise ValueError('Excessive temporal scale')
        if any(not 1<=v<=16 for v in self.forecast_lags):raise ValueError('Excessive forecast lag')
        if len({v.source_sha256 for v in self.source_evidence})!=len(self.source_evidence):raise ValueError('Duplicate source evidence')
        return self
