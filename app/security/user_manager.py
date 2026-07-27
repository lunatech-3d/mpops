"""User creation and administrator-only account management operations."""
from __future__ import annotations

import sqlite3
from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.passwords import hash_password

VALID_ROLES = frozenset({"admin", "operator", "viewer"})


class AuthorizationError(PermissionError):
    pass


class UserManager:
    def __init__(self, auth: AuthService):
        self.auth = auth

    @staticmethod
    def _require_admin(actor: Session) -> None:
        if actor.role != "admin":
            raise AuthorizationError("Administrator role required")

    def create_user(self, username: str, password: str, role: str = "operator",
                    actor: Session | None = None, display_name: str | None = None) -> int:
        clean = username.strip()
        if not clean or len(clean) > 100:
            raise ValueError("Username must contain between 1 and 100 characters")
        if role not in VALID_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(VALID_ROLES))}")
        encoded = hash_password(password, self.auth.settings.password_iterations)
        try:
            with self.auth.connect() as connection:
                if actor is None:
                    if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] or role != "admin":
                        raise AuthorizationError("Only the initial administrator can be created without a session")
                else:
                    self._require_admin(actor)
                cursor = connection.execute(
                    """INSERT INTO users (username, username_key, password_hash, display_name, role,
                       is_active, created_at, created_by) VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                    (clean, clean.casefold(), encoded, (display_name or clean).strip(), role,
                     utc_now_iso(), actor.user_id if actor else None))
                user_id = int(cursor.lastrowid)
                record_event(connection, "user_created", actor_user_id=actor.user_id if actor else user_id,
                             subject_user_id=user_id, details={"role": role})
                return user_id
        except sqlite3.IntegrityError as exc:
            raise ValueError("A user with that username already exists") from exc

    def list_users(self, actor: Session, search: str = "", active: bool | None = None) -> list[dict]:
        self._require_admin(actor)
        clauses, values = [], []
        if search.strip():
            clauses.append("(username_key LIKE ? OR lower(coalesce(display_name, '')) LIKE ?)")
            term = f"%{search.strip().casefold()}%"
            values.extend((term, term))
        if active is not None:
            clauses.append("is_active = ?")
            values.append(int(active))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.auth.connect() as connection:
            rows = connection.execute(
                "SELECT id, username, display_name, role, is_active, created_at, last_login_at, updated_at "
                f"FROM users{where} ORDER BY username_key", values)
            return [dict(row) for row in rows]

    def get_user(self, user_id: int, actor: Session) -> dict:
        self._require_admin(actor)
        with self.auth.connect() as connection:
            row = connection.execute(
                "SELECT id, username, display_name, role, is_active, created_at, last_login_at, updated_at "
                "FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise LookupError("User not found")
        return dict(row)

    def update_user(self, user_id: int, *, display_name: str, role: str, actor: Session) -> None:
        self._require_admin(actor)
        name = display_name.strip()
        if not name or len(name) > 100:
            raise ValueError("Display name must contain between 1 and 100 characters")
        if role not in VALID_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(VALID_ROLES))}")
        with self.auth.connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET display_name=?, role=?, updated_at=?, updated_by=? WHERE id=?",
                (name, role, utc_now_iso(), actor.user_id, user_id))
            if cursor.rowcount != 1:
                raise LookupError("User not found")
            record_event(connection, "user_updated", actor_user_id=actor.user_id,
                         subject_user_id=user_id, details={"role": role})

    def reset_password(self, user_id: int, password: str, actor: Session) -> None:
        self._require_admin(actor)
        encoded = hash_password(password, self.auth.settings.password_iterations)
        with self.auth.connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET password_hash=?, updated_at=?, updated_by=? WHERE id=?",
                (encoded, utc_now_iso(), actor.user_id, user_id))
            if cursor.rowcount != 1:
                raise LookupError("User not found")
            record_event(connection, "password_reset", actor_user_id=actor.user_id, subject_user_id=user_id)

    def set_active(self, user_id: int, active: bool, actor: Session) -> None:
        self._require_admin(actor)
        if actor.user_id == user_id and not active:
            raise ValueError("Administrators cannot deactivate their own account")
        with self.auth.connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET is_active=?, updated_at=?, updated_by=? WHERE id=?",
                (int(active), utc_now_iso(), actor.user_id, user_id))
            if cursor.rowcount != 1:
                raise LookupError("User not found")
            record_event(connection, "user_activated" if active else "user_deactivated",
                         actor_user_id=actor.user_id, subject_user_id=user_id)
