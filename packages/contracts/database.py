"""Explicit SQLite policy; WAL is opt-in for a supported local filesystem."""
from typing import Literal
from pydantic import Field
from .models import Strict


class SQLiteBackupSettings(Strict):
    timeout_seconds: float = Field(default=10, ge=.05, le=600)
    pages_per_step: int = Field(default=64, ge=1, le=4096)
    retry_seconds: float = Field(default=.01, ge=.001, le=.1)


class SQLiteSettings(Strict):
    journal_mode: Literal['DELETE','WAL'] = 'DELETE'
    synchronous: Literal['FULL','EXTRA'] = 'FULL'
    busy_timeout_ms: int = Field(default=10000, ge=100, le=120000)
    integrity_check_on_start: bool = True
    min_free_bytes: int = Field(default=16777216, ge=0, le=10737418240)
    backup: SQLiteBackupSettings = Field(default_factory=SQLiteBackupSettings)
