"""Application configuration with Matterport Ops-specific defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database_path: Path
    schema_path: Path = PROJECT_ROOT / "database" / "schema" / "001_initial.sql"
    migrations_path: Path = PROJECT_ROOT / "database" / "migrations"
    password_iterations: int = 600_000
    reporting_copy: bool = False


def get_settings() -> Settings:
    """Load settings from the ``MPOPS_`` environment namespace."""
    database_path = Path(os.environ.get("MPOPS_DB_PATH", "C:/sqlite/mpops/database/mpops.db"))
    iterations = int(os.environ.get("MPOPS_PASSWORD_ITERATIONS", "600000"))
    if iterations < 100_000:
        raise ValueError("MPOPS_PASSWORD_ITERATIONS must be at least 100000")
    reporting_copy = os.environ.get("MPOPS_REPORTING_COPY", "").strip().lower() in {"1", "true", "yes", "on"}
    return Settings(
        database_path=database_path,
        password_iterations=iterations,
        reporting_copy=reporting_copy,
    )
