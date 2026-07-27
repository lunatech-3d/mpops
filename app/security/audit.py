"""Append-only security audit logging."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.date_utils import utc_now_iso


def record_event(
    connection: sqlite3.Connection,
    action: str,
    *,
    actor_user_id: int | None = None,
    subject_user_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """INSERT INTO AuditLog
           (occurred_at, actor_user_id, subject_user_id, action, details_json)
           VALUES (?, ?, ?, ?, ?)""",
        (utc_now_iso(), actor_user_id, subject_user_id, action, json.dumps(details or {}, sort_keys=True)),
    )
