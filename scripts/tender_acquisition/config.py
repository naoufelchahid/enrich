"""Configuration for the independent tender-company workflow."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    db_path: Path = Path('bdd/workflow/tender_acquisition.sqlite')
    max_companies: int = 20
    max_documents: int = 200
    max_run_minutes: int = 120
    first_run_months: int = 12
    daily_window_days: int = 3

    @classmethod
    def from_env(cls) -> 'Config':
        def positive(name: str, default: int) -> int:
            value = int(os.getenv(name, str(default)))
            if value < 1:
                raise ValueError(f'{name} must be positive')
            return value

        return cls(
            db_path=Path(os.getenv('TENDER_ACQUISITION_DB', 'bdd/workflow/tender_acquisition.sqlite')),
            max_companies=positive('TENDER_MAX_COMPANIES', 20),
            max_documents=positive('TENDER_MAX_DOCUMENTS', 200),
            max_run_minutes=positive('TENDER_MAX_RUN_MINUTES', 120),
            first_run_months=positive('TENDER_FIRST_RUN_MONTHS', 12),
            daily_window_days=positive('TENDER_DAILY_WINDOW_DAYS', 3),
        )
