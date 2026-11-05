"""Opt-in, file-only B.1 development experiments; no formal research admission."""
from typing import Literal
from pydantic import Field,model_validator
from .models import Strict
from .scenes import Identifier
from .learning import Sha


class MotionExclusion(Strict):
    start_ms:int=Field(ge=0)
    end_ms:int=Field(gt=0)
    reason:Literal['occluded','outside_view','invalid_frame']

    @model_validator(mode='after')
    def ordered(self):
        if self.end_ms<=self.start_ms:raise ValueError('Exclusion must have positive duration')
        return self


class MotionROI(Strict):
    id:Identifier
    role:Literal['global','hands','interaction','background','offset_control']
    xyxy:list[float]=Field(min_length=4,max_length=4)
    visibility:Literal['unverified','visible','occluded']='unverified'
    encode_r3d:bool=True
    exclusions:list[MotionExclusion]=Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def box(self):
        x1,y1,x2,y2=self.xyxy
        if not 0<=x1<x2<=1 or not 0<=y1<y2<=1:raise ValueError('ROI must be a nonempty in-bounds display-coordinate box')
        if self.role=='global' and self.xyxy!=[0,0,1,1]:raise ValueError('Global control must cover the complete display frame')
        return self


class LocalMotionSettings(Strict):
    enabled:bool=False
    evidence_class:Literal['development_exploration','synthetic_engineering']
    formal_research_access:Literal[False]=False
    source_id:Identifier
    source_sha256:Sha
    display_width:int=Field(ge=16,le=16384)
    display_height:int=Field(ge=16,le=16384)
    start_ms:int=Field(ge=0)
    end_ms:int=Field(gt=0)
    rois:list[MotionROI]=Field(min_length=4,max_length=12)
    window_ms:list[int]=Field(min_length=1,max_length=4)
    sample_fps:list[int]=Field(min_length=1,max_length=4)
    stride_ms:int=Field(ge=100,le=5000)
    encoder_frames:Literal[16]=16
    max_pts_error_ms:int=Field(ge=1,le=250)
    min_valid_fraction:float=Field(gt=0,le=1)
    max_windows:int=Field(ge=1,le=10000)
    motion_size:int=Field(ge=16,le=128)
    block_size:int=Field(ge=4,le=32)
    search_radius:int=Field(ge=1,le=8)
    texture_floor:float=Field(ge=0,lt=1)
    motion_improvement:float=Field(ge=0,lt=1)
    baseline_fraction:float=Field(gt=0,le=.5)
    thresholds:list[float]=Field(min_length=1,max_length=8)
    smoothing_ms:list[int]=Field(min_length=1,max_length=5)
    primary_window_ms:int=Field(ge=500,le=8000)
    primary_fps:int=Field(ge=1,le=32)
    primary_threshold:float=Field(gt=0)
    primary_smoothing_ms:int=Field(ge=0,le=5000)
    refractory_ms:int=Field(ge=100,le=10000)
    candidate_budget:int=Field(ge=1,le=200)
    boundary_tolerance_ms:int=Field(ge=0,le=5000)

    @model_validator(mode='after')
    def consistent(self):
        if self.end_ms<=self.start_ms:raise ValueError('Experiment time range must be positive')
        if len({r.id for r in self.rois})!=len(self.rois):raise ValueError('Duplicate ROI id')
        roles=[r.role for r in self.rois]
        if roles.count('global')!=1 or not {'hands','interaction','background'}<=set(roles):raise ValueError('Global/hands/interaction/background controls are required')
        for values in [self.window_ms,self.sample_fps,self.thresholds,self.smoothing_ms]:
            if len(set(values))!=len(values):raise ValueError('Duplicate experiment arm')
        if any(w<500 or w>8000 for w in self.window_ms) or any(f<1 or f>32 for f in self.sample_fps):raise ValueError('Sampling outside bounded budget')
        if any(not 2<=w*f/1000<=32 or w*f%1000 for w in self.window_ms for f in self.sample_fps):raise ValueError('Each window needs 2–32 integral original samples')
        if self.motion_size%self.block_size or self.search_radius>=self.block_size:raise ValueError('Invalid block motion geometry')
        if any(t<=0 or t>20 for t in self.thresholds) or any(s<0 or s>5000 for s in self.smoothing_ms):raise ValueError('Invalid analysis arm')
        if self.primary_window_ms not in self.window_ms or self.primary_fps not in self.sample_fps or self.primary_threshold not in self.thresholds or self.primary_smoothing_ms not in self.smoothing_ms:raise ValueError('Primary arm must be preregistered in the exploration grid')
        return self
