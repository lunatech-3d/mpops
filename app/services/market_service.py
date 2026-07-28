"""Market lookup-table operations."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError


class MarketService:
    """Create, retrieve, update, and activate/deactivate operational markets."""

    VALID_STATUSES = {"Active", "Inactive"}

    def __init__(self, auth: AuthService):
        self.auth = auth

    @staticmethod
    def _require_admin(session: Session | None) -> None:
        if session is None or session.role != "admin":
            raise AuthorizationError("Administrator role required")

    @staticmethod
    def _positive_id(value: Any, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} must be a positive integer")
        return value

    @staticmethod
    def _clean_name(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("Market name must be text")
        value = value.strip()
        if not value:
            raise ValueError("Market name is required")
        if len(value) > 255:
            raise ValueError("Market name is too long")
        return value

    @staticmethod
    def _clean_state(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("State must be text")
        value = value.strip().upper()
        if len(value) != 2 or not value.isalpha():
            raise ValueError("State must be a two-letter abbreviation")
        return value

    @classmethod
    def _clean_status(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("Status must be text")
        value = value.strip().title()
        if value not in cls.VALID_STATUSES:
            raise ValueError("Status must be Active or Inactive")
        return value

    @staticmethod
    def _require_market(connection: sqlite3.Connection, market_id: int) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM Markets WHERE market_id=?", (market_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Market not found")
        return row

    def list_markets(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        if type(include_inactive) is not bool:
            raise ValueError("include_inactive must be a boolean")
        where = "" if include_inactive else " WHERE status='Active'"
        with self.auth.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM Markets" + where +
                " ORDER BY state COLLATE NOCASE, market_name COLLATE NOCASE, market_id"
            )
            return [dict(row) for row in rows]

    def search_markets(self, query: str, include_inactive: bool = False) -> list[dict[str, Any]]:
        if not isinstance(query, str):
            raise ValueError("query must be text")
        if type(include_inactive) is not bool:
            raise ValueError("include_inactive must be a boolean")
        query = query.strip()
        if not query:
            return self.list_markets(include_inactive)
        where = " WHERE (market_name LIKE ? COLLATE NOCASE OR state LIKE ? COLLATE NOCASE)"
        if not include_inactive:
            where += " AND status='Active'"
        like_query = f"%{query}%"
        with self.auth.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM Markets" + where +
                " ORDER BY state COLLATE NOCASE, market_name COLLATE NOCASE, market_id",
                (like_query, like_query),
            )
            return [dict(row) for row in rows]

    def get_market(self, market_id: int) -> dict[str, Any] | None:
        self._positive_id(market_id, "market_id")
        with self.auth.connection() as connection:
            row = connection.execute(
                "SELECT * FROM Markets WHERE market_id=?", (market_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_market(
        self,
        session: Session,
        market_name: str,
        state: str,
        status: str = "Active",
    ) -> int:
        self._require_admin(session)
        name = self._clean_name(market_name)
        state_code = self._clean_state(state)
        clean_status = self._clean_status(status)
        try:
            with self.auth.connection() as connection:
                cursor = connection.execute(
                    "INSERT INTO Markets (market_name,state,status,created_at,created_by) "
                    "VALUES (?,?,?,?,?)",
                    (name, state_code, clean_status, utc_now_iso(), session.user_id),
                )
                market_id = int(cursor.lastrowid)
                record_event(
                    connection,
                    "market_created",
                    actor_user_id=session.user_id,
                    details={
                        "market_id": market_id,
                        "market_name": name,
                        "state": state_code,
                        "status": clean_status,
                    },
                )
                return market_id
        except sqlite3.IntegrityError as exc:
            raise ValueError("A market with that name already exists") from exc

    def update_market(
        self,
        session: Session,
        market_id: int,
        market_name: str,
        state: str,
        status: str,
    ) -> dict[str, Any]:
        self._require_admin(session)
        self._positive_id(market_id, "market_id")
        name = self._clean_name(market_name)
        state_code = self._clean_state(state)
        clean_status = self._clean_status(status)
        try:
            with self.auth.connection() as connection:
                before = dict(self._require_market(connection, market_id))
                connection.execute(
                    "UPDATE Markets SET market_name=?,state=?,status=?,updated_at=?,updated_by=? "
                    "WHERE market_id=?",
                    (name, state_code, clean_status, utc_now_iso(), session.user_id, market_id),
                )
                record_event(
                    connection,
                    "market_updated",
                    actor_user_id=session.user_id,
                    details={
                        "market_id": market_id,
                        "before": {
                            "market_name": before["market_name"],
                            "state": before.get("state"),
                            "status": before["status"],
                        },
                        "after": {
                            "market_name": name,
                            "state": state_code,
                            "status": clean_status,
                        },
                    },
                )
                return dict(self._require_market(connection, market_id))
        except sqlite3.IntegrityError as exc:
            raise ValueError("A market with that name already exists") from exc

    def set_market_active(self, session: Session, market_id: int, is_active: bool) -> None:
        self._require_admin(session)
        self._positive_id(market_id, "market_id")
        if type(is_active) is not bool:
            raise ValueError("is_active must be a boolean")
        with self.auth.connection() as connection:
            before = self._require_market(connection, market_id)["status"]
            status = "Active" if is_active else "Inactive"
            connection.execute(
                "UPDATE Markets SET status=?,updated_at=?,updated_by=? WHERE market_id=?",
                (status, utc_now_iso(), session.user_id, market_id),
            )
            record_event(
                connection,
                "market_activated" if is_active else "market_deactivated",
                actor_user_id=session.user_id,
                details={"market_id": market_id, "before": before, "after": status},
            )
