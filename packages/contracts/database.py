"""Explicit SQLite policy; WAL is opt-in for a supported local filesystem."""
from typing import Literal
from pydantic import Field
from .models import Strict


class SQLiteSettings(Strict):
    journal_mode: Literal['DELETE','WAL'] = 'DELETE'
    synchronous: Literal['FULL','EXTRA'] = 'FULL'
    busy_timeout_ms: int = Field(default=10000, ge=100, le=120000)
    integrity_check_on_start: bool = True
    min_free_bytes: int = Field(default=16777216, ge=0, le=10737418240)
