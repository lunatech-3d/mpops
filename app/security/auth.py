"""Database initialization and authentication service."""

from __future__ import annotations

import os
import sqlite3
import importlib.util
from collections.abc import Iterator
from contextlib import contextmanager
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
    display_name: str | None = None

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

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize_database(self) -> None:
        with self.connection() as connection:
            has_tables = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            if not has_tables:
                connection.executescript(self.settings.schema_path.read_text(encoding="utf-8"))
                return
            connection.execute("CREATE TABLE IF NOT EXISTS SchemaMigrations "
                               "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            connection.commit()
            applied = {row[0] for row in connection.execute("SELECT name FROM SchemaMigrations")}
            migrations = sorted((*self.settings.migrations_path.glob("[0-9]*.sql"),
                                 *self.settings.migrations_path.glob("[0-9]*.py")))
            for migration in migrations:
                if migration.name in applied:
                    continue
                try:
                    connection.execute("PRAGMA foreign_keys = OFF")
                    connection.execute("BEGIN")
                    if migration.suffix == ".sql":
                        for statement in migration.read_text(encoding="utf-8").split(";"):
                            if statement.strip():
                                connection.execute(statement)
                    else:
                        spec = importlib.util.spec_from_file_location("mpops_migration", migration)
                        module = importlib.util.module_from_spec(spec)
                        assert spec and spec.loader
                        spec.loader.exec_module(module)
                        module.migrate(connection)
                    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise RuntimeError(f"foreign-key check failed: {violations}")
                    connection.execute("INSERT INTO SchemaMigrations(name, applied_at) VALUES (?, ?)",
                                       (migration.name, utc_now_iso()))
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    connection.execute("PRAGMA foreign_keys = ON")

    def authenticate(self, username: str, password: str) -> Session:
        normalized = username.strip()
        with self.connection() as connection:
            user = connection.execute(
                "SELECT id, username, password_hash, display_name, role, is_active FROM Users "
                "WHERE username = ? COLLATE NOCASE",
                (normalized,),
            ).fetchone()
            if user is None or not user["is_active"] or not verify_password(password, user["password_hash"]):
                record_event(connection, "login_failed", details={"username": normalized})
                # Persist the audit event before raising: sqlite's context manager
                # rolls back a transaction when an exception leaves the block.
                connection.commit()
                raise AuthenticationError("Invalid username or password")
            connection.execute("UPDATE Users SET last_login_at = ? WHERE id = ?", (utc_now_iso(), user["id"]))
            record_event(connection, "login_succeeded", actor_user_id=user["id"])
        return Session(user["id"], user["username"], user["role"], user["display_name"])
