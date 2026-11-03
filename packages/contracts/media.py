"""Bounded admission wait; a media response retains its grant until completion."""
from typing import Literal
from pydantic import Field
from .models import Strict

class MediaAccessSettings(Strict):
    wait_timeout_seconds: float = Field(default=5,ge=.05,le=120)
    sealing_wait_seconds: float = Field(default=30,ge=.05,le=600)
    poll_seconds: float = Field(default=.01,ge=.001,le=.1)

class MediaAdmission(Strict):
    state: Literal['development','pending_seal','sealed','seal_failed','cancelled']
    ordinary_media_allowed: bool
    existing_grant_policy: Literal['finish_authorized_stream_before_formal_seal_commit']
