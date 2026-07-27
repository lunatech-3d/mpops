"""User creation and administration operations."""

from __future__ import annotations

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

    def create_user(self, username: str, password: str, role: str = "operator", actor: Session | None = None) -> int:
        display_name = username.strip()
        if not display_name or len(display_name) > 100:
            raise ValueError("Username must contain between 1 and 100 characters")
        if role not in VALID_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(VALID_ROLES))}")
        password_hash = hash_password(password, self.auth.settings.password_iterations)
        with self.auth.connect() as connection:
            if actor is None:
                count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                if count != 0 or role != "admin":
                    raise AuthorizationError("Only the initial administrator can be created without a session")
            elif actor.role != "admin":
                raise AuthorizationError("Administrator role required")
            cursor = connection.execute(
                """INSERT INTO users (username, username_key, password_hash, role, is_active, created_at)
                   VALUES (?, ?, ?, ?, 1, ?)""",
                (display_name, display_name.casefold(), password_hash, role, utc_now_iso()),
            )
            user_id = int(cursor.lastrowid)
            record_event(connection, "user_created", actor_user_id=actor.user_id if actor else user_id,
                         subject_user_id=user_id, details={"role": role})
        return user_id

    def set_active(self, user_id: int, active: bool, actor: Session) -> None:
        if actor.role != "admin":
            raise AuthorizationError("Administrator role required")
        if actor.user_id == user_id and not active:
            raise ValueError("Administrators cannot deactivate their own account")
        with self.auth.connect() as connection:
            cursor = connection.execute("UPDATE users SET is_active = ? WHERE id = ?", (active, user_id))
            if cursor.rowcount != 1:
                raise LookupError("User not found")
            record_event(connection, "user_activated" if active else "user_deactivated",
                         actor_user_id=actor.user_id, subject_user_id=user_id)

