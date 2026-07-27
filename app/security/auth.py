"""Database initialization and authentication service."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.passwords import verify_password


class AuthenticationError(ValueError):
    """Raised when supplied credentials cannot be authenticated."""


@dataclass(frozen=True)
class Session:
    user_id: int
    username: str
    role: str

    def apply_to_environment(self) -> None:
        os.environ["MPOPS_USER_ID"] = str(self.user_id)
        os.environ["MPOPS_USERNAME"] = self.username
        os.environ["MPOPS_ROLE"] = self.role

    @staticmethod
    def clear_environment() -> None:
        for name in ("MPOPS_USER_ID", "MPOPS_USERNAME", "MPOPS_ROLE"):
            os.environ.pop(name, None)


class AuthService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize_database()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize_database(self) -> None:
        schema = self.settings.schema_path.read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema)
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {row[0] for row in connection.execute("SELECT name FROM schema_migrations")}
            for migration in sorted(self.settings.migrations_path.glob("[0-9]*.sql")):
                if migration.name not in applied:
                    connection.executescript(migration.read_text(encoding="utf-8"))
                    connection.execute(
                        "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                        (migration.name, utc_now_iso()),
                    )

    def authenticate(self, username: str, password: str) -> Session:
        normalized = username.strip().casefold()
        with self.connect() as connection:
            user = connection.execute(
                "SELECT id, username, password_hash, role, is_active FROM users WHERE username_key = ?",
                (normalized,),
            ).fetchone()
            if user is None or not user["is_active"] or not verify_password(password, user["password_hash"]):
                record_event(connection, "login_failed", details={"username": normalized})
                # Persist the audit event before raising: sqlite's context manager
                # rolls back a transaction when an exception leaves the block.
                connection.commit()
                raise AuthenticationError("Invalid username or password")
            connection.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utc_now_iso(), user["id"]))
            record_event(connection, "login_succeeded", actor_user_id=user["id"])
        return Session(user["id"], user["username"], user["role"])
