"""Technician and technician-address application operations."""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError


_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_TECH_FIELDS = frozenset({
    "tech_code", "first_name", "middle_name", "last_name", "suffix", "preferred_name",
    "company_name", "contractor_type", "inactive_reason", "date_of_birth", "ssn_last4",
    "drivers_license_number", "drivers_license_state", "email", "alternate_email",
    "mobile_phone", "home_phone", "work_phone", "emergency_contact_name",
    "emergency_contact_relationship", "emergency_contact_phone", "hire_date",
    "termination_date", "notes", "notes_private",
})
_ADDRESS_FIELDS = frozenset({
    "address_1", "address_2", "city", "state", "zip_code", "is_primary",
    "effective_date", "end_date",
})
_REQUIRED_TECH = frozenset({"tech_code", "first_name", "last_name"})
_REQUIRED_ADDRESS = frozenset({"address_1", "city", "state", "zip_code"})
_TEXT_LIMITS = {field: 255 for field in _TECH_FIELDS | _ADDRESS_FIELDS}
_TEXT_LIMITS["notes"] = 4000
_TEXT_LIMITS["notes_private"] = 4000
_SENSITIVE_FIELDS = frozenset({
    "date_of_birth", "ssn_last4", "drivers_license_number", "drivers_license_state",
    "emergency_contact_name", "emergency_contact_relationship", "emergency_contact_phone",
    "notes_private",
})
_DATE_LABELS = {"date_of_birth": "Date of Birth", "hire_date": "Hire Date",
                "termination_date": "Termination Date"}


class TechnicianService:
    """Manage technicians and addresses using the shared database and audit log."""

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
    def _bool(value: Any, label: str) -> bool:
        if type(value) is not bool:
            raise ValueError(f"{label} must be a boolean")
        return value

    @staticmethod
    def _clean_text(field: str, value: Any, required: bool = False) -> str | None:
        if value is None:
            if required:
                raise ValueError(f"{field} is required")
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} must be text")
        value = value.strip()
        if not value:
            if required:
                raise ValueError(f"{field} is required")
            return None
        if len(value) > _TEXT_LIMITS[field]:
            raise ValueError(f"{field} is too long")
        if field in ("email", "alternate_email") and not _EMAIL.fullmatch(value):
            raise ValueError(f"{'Primary' if field == 'email' else 'Alternate'} email is invalid.")
        if field in _DATE_LABELS:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{_DATE_LABELS[field]} must use YYYY-MM-DD.") from exc
        if field == "ssn_last4" and not re.fullmatch(r"\d{4}", value):
            raise ValueError("SSN — Last 4 Digits must contain exactly four digits.")
        if field == "drivers_license_state":
            if not re.fullmatch(r"[A-Za-z]{2}", value):
                raise ValueError("Driver's License State must be a two-letter abbreviation.")
            value = value.upper()
        if field == "state" and len(value) == 2 and value.isalpha():
            value = value.upper()
        return value

    @classmethod
    def _clean_technician(cls, data: dict[str, Any], *, creating: bool) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("technician data must be a dictionary")
        invalid = set(data) - _TECH_FIELDS
        if invalid:
            raise ValueError(f"Unsupported technician fields: {', '.join(sorted(invalid))}")
        if creating:
            missing = _REQUIRED_TECH - set(data)
            if missing:
                raise ValueError(f"Missing required fields: {', '.join(sorted(missing))}")
        if not creating and not data:
            raise ValueError("At least one technician field is required")
        return {field: cls._clean_text(field, value, field in _REQUIRED_TECH)
                for field, value in data.items()}

    @classmethod
    def _clean_address(cls, data: dict[str, Any], *, creating: bool) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("address data must be a dictionary")
        invalid = set(data) - _ADDRESS_FIELDS
        if invalid:
            raise ValueError(f"Unsupported address fields: {', '.join(sorted(invalid))}")
        if creating:
            missing = _REQUIRED_ADDRESS - set(data)
            if missing:
                raise ValueError(f"Missing required fields: {', '.join(sorted(missing))}")
        if not creating and not data:
            raise ValueError("At least one address field is required")
        clean: dict[str, Any] = {}
        for field, value in data.items():
            clean[field] = (int(cls._bool(value, field)) if field == "is_primary" else
                            cls._clean_text(field, value, field in _REQUIRED_ADDRESS))
        if creating:
            clean.setdefault("is_primary", 1)
        return clean

    @staticmethod
    def _require_technician(connection: sqlite3.Connection, tech_id: int) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM Techs WHERE tech_id=?", (tech_id,)).fetchone()
        if row is None:
            raise LookupError("Technician not found")
        return row

    @staticmethod
    def _require_address(connection: sqlite3.Connection, tech_id: int,
                         address_id: int) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM TechAddresses WHERE address_id=?", (address_id,)).fetchone()
        if row is None:
            raise LookupError("Address not found")
        if row["tech_id"] != tech_id:
            raise LookupError("Address does not belong to technician")
        return row

    def list_technicians(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        """Return technicians ordered by last name, first name, and identifier."""
        self._bool(include_inactive, "include_inactive")
        where = "" if include_inactive else " WHERE status = 'Active'"
        with self.auth.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM Techs" + where +
                " ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE, tech_id")
            return [dict(row) for row in rows]

    def search_technicians(self, query: str, include_inactive: bool = False) -> list[dict[str, Any]]:
        """Case-insensitively search existing technician identity and contact fields."""
        if not isinstance(query, str):
            raise ValueError("query must be text")
        self._bool(include_inactive, "include_inactive")
        query = query.strip()
        if not query:
            return self.list_technicians(include_inactive)
        term = f"%{query}%"
        searchable = ("tech_code", "first_name", "middle_name", "last_name", "suffix",
                      "preferred_name", "company_name", "contractor_type", "email",
                      "alternate_email", "mobile_phone", "home_phone", "work_phone")
        search = " OR ".join(f"coalesce({field}, '') LIKE ? COLLATE NOCASE" for field in searchable)
        where = f" WHERE ({search})" + ("" if include_inactive else " AND status = 'Active'")
        with self.auth.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM Techs" + where +
                " ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE, tech_id",
                [term] * len(searchable))
            return [dict(row) for row in rows]

    def get_technician(self, tech_id: int) -> dict[str, Any] | None:
        """Return one technician, or ``None`` when the identifier does not exist."""
        self._positive_id(tech_id, "tech_id")
        with self.auth.connection() as connection:
            row = connection.execute("SELECT * FROM Techs WHERE tech_id=?", (tech_id,)).fetchone()
            return dict(row) if row else None

    def create_technician(self, session: Session, technician_data: dict[str, Any]) -> int:
        """Create and audit a technician; an administrator session is required."""
        self._require_admin(session)
        clean = self._clean_technician(technician_data, creating=True)
        fields = list(clean)
        try:
            with self.auth.connection() as connection:
                cursor = connection.execute(
                    f"INSERT INTO Techs ({','.join(fields)},created_at,created_by) "
                    f"VALUES ({','.join('?' for _ in fields)},?,?)",
                    [clean[field] for field in fields] + [utc_now_iso(), session.user_id])
                tech_id = int(cursor.lastrowid)
                record_event(connection, "technician_created", actor_user_id=session.user_id,
                             details={"tech_id": tech_id})
                return tech_id
        except sqlite3.IntegrityError as exc:
            raise ValueError("Technician data conflicts with an existing record") from exc

    def update_technician(self, session: Session, tech_id: int,
                          changes: dict[str, Any]) -> dict[str, Any]:
        """Update allowlisted technician fields and return the resulting record."""
        self._require_admin(session)
        self._positive_id(tech_id, "tech_id")
        clean = self._clean_technician(changes, creating=False)
        with self.auth.connection() as connection:
            before = dict(self._require_technician(connection, tech_id))
            assignments = ",".join(f"{field}=?" for field in clean)
            connection.execute(f"UPDATE Techs SET {assignments},updated_at=?,updated_by=? WHERE tech_id=?",
                               [*clean.values(), utc_now_iso(), session.user_id, tech_id])
            ordinary = set(clean) - _SENSITIVE_FIELDS
            details = {"tech_id": tech_id, "fields_changed": sorted(clean)}
            if ordinary:
                details.update(before={k: before[k] for k in ordinary},
                               after={k: clean[k] for k in ordinary})
            record_event(connection, "technician_updated", actor_user_id=session.user_id,
                         details=details)
            return dict(connection.execute("SELECT * FROM Techs WHERE tech_id=?", (tech_id,)).fetchone())

    def deactivate_technician(self, session: Session, tech_id: int,
                              termination_date: str | None = None,
                              inactive_reason: str | None = None) -> None:
        """Atomically store deactivation context and retain the technician record."""
        self._require_admin(session)
        self._positive_id(tech_id, "tech_id")
        clean = self._clean_technician(
            {"termination_date": termination_date, "inactive_reason": inactive_reason}, creating=False)
        with self.auth.connection() as connection:
            before = dict(self._require_technician(connection, tech_id))
            connection.execute(
                "UPDATE Techs SET termination_date=?,inactive_reason=?,status='Inactive',"
                "updated_at=?,updated_by=? WHERE tech_id=?",
                (clean["termination_date"], clean["inactive_reason"], utc_now_iso(),
                 session.user_id, tech_id))
            record_event(connection, "technician_deactivated", actor_user_id=session.user_id,
                         details={"tech_id": tech_id, "before": before["status"],
                                  "after": "Inactive", "fields_changed":
                                  ["termination_date", "inactive_reason", "status"]})

    def set_technician_active(self, session: Session, tech_id: int, is_active: bool) -> None:
        """Set Active/Inactive status without deleting technician history."""
        self._require_admin(session)
        self._positive_id(tech_id, "tech_id")
        active = self._bool(is_active, "is_active")
        with self.auth.connection() as connection:
            before = self._require_technician(connection, tech_id)["status"]
            status = "Active" if active else "Inactive"
            connection.execute("UPDATE Techs SET status=?,updated_at=?,updated_by=? WHERE tech_id=?",
                               (status, utc_now_iso(), session.user_id, tech_id))
            record_event(connection, "technician_activated" if active else "technician_deactivated",
                         actor_user_id=session.user_id,
                         details={"tech_id": tech_id, "before": before, "after": status})

    def list_addresses(self, tech_id: int) -> list[dict[str, Any]]:
        """Return addresses with the primary first and then identifier order."""
        self._positive_id(tech_id, "tech_id")
        with self.auth.connection() as connection:
            self._require_technician(connection, tech_id)
            rows = connection.execute("SELECT * FROM TechAddresses WHERE tech_id=? "
                                      "ORDER BY is_primary DESC,address_id", (tech_id,))
            return [dict(row) for row in rows]

    def get_current_address(self, tech_id: int) -> dict[str, Any] | None:
        """Return the primary address, or the most recently active address.

        Legacy data does not always designate a primary address.  In that case an
        address without an end date is preferred, followed by the latest effective
        date and modification/creation timestamp.  The identifier is used only as
        the final deterministic tie breaker, never as the current-address rule.
        """
        self._positive_id(tech_id, "tech_id")
        with self.auth.connection() as connection:
            self._require_technician(connection, tech_id)
            row = connection.execute(
                "SELECT * FROM TechAddresses WHERE tech_id=? "
                "ORDER BY is_primary DESC, "
                "CASE WHEN end_date IS NULL OR end_date='' THEN 0 ELSE 1 END, "
                "coalesce(effective_date,'') DESC, "
                "coalesce(updated_at,created_at,'') DESC, address_id DESC LIMIT 1",
                (tech_id,),
            ).fetchone()
            return dict(row) if row else None

    def add_address(self, session: Session, tech_id: int, address_data: dict[str, Any]) -> int:
        """Add and audit an address, maintaining at most one primary address."""
        self._require_admin(session)
        self._positive_id(tech_id, "tech_id")
        clean = self._clean_address(address_data, creating=True)
        with self.auth.connection() as connection:
            self._require_technician(connection, tech_id)
            if clean["is_primary"]:
                connection.execute("UPDATE TechAddresses SET is_primary=0 WHERE tech_id=?", (tech_id,))
            fields = list(clean)
            cursor = connection.execute(
                f"INSERT INTO TechAddresses (tech_id,{','.join(fields)},created_at,created_by) "
                f"VALUES (?,{','.join('?' for _ in fields)},?,?)",
                [tech_id, *clean.values(), utc_now_iso(), session.user_id])
            address_id = int(cursor.lastrowid)
            record_event(connection, "technician_address_added", actor_user_id=session.user_id,
                         details={"tech_id": tech_id, "address_id": address_id})
            return address_id

    def update_address(self, session: Session, tech_id: int, address_id: int,
                       changes: dict[str, Any]) -> dict[str, Any]:
        """Update an owned address while preserving primary-address uniqueness."""
        self._require_admin(session)
        self._positive_id(tech_id, "tech_id")
        self._positive_id(address_id, "address_id")
        clean = self._clean_address(changes, creating=False)
        with self.auth.connection() as connection:
            self._require_technician(connection, tech_id)
            before = dict(self._require_address(connection, tech_id, address_id))
            if clean.get("is_primary"):
                connection.execute("UPDATE TechAddresses SET is_primary=0 WHERE tech_id=?", (tech_id,))
            assignments = ",".join(f"{field}=?" for field in clean)
            connection.execute(f"UPDATE TechAddresses SET {assignments},updated_at=?,updated_by=? "
                               "WHERE address_id=?", [*clean.values(), utc_now_iso(), session.user_id, address_id])
            record_event(connection, "technician_address_updated", actor_user_id=session.user_id,
                         details={"tech_id": tech_id, "address_id": address_id,
                                  "before": {k: before[k] for k in clean}, "after": clean})
            return dict(connection.execute("SELECT * FROM TechAddresses WHERE address_id=?",
                                           (address_id,)).fetchone())

    def set_primary_address(self, session: Session, tech_id: int, address_id: int) -> None:
        """Atomically make an owned address the technician's sole primary address."""
        self._require_admin(session)
        self._positive_id(tech_id, "tech_id")
        self._positive_id(address_id, "address_id")
        with self.auth.connection() as connection:
            self._require_technician(connection, tech_id)
            self._require_address(connection, tech_id, address_id)
            old = connection.execute("SELECT address_id FROM TechAddresses WHERE tech_id=? AND is_primary=1",
                                     (tech_id,)).fetchone()
            connection.execute("UPDATE TechAddresses SET is_primary=0 WHERE tech_id=?", (tech_id,))
            connection.execute("UPDATE TechAddresses SET is_primary=1,updated_at=?,updated_by=? WHERE address_id=?",
                               (utc_now_iso(), session.user_id, address_id))
            record_event(connection, "technician_primary_address_changed", actor_user_id=session.user_id,
                         details={"tech_id": tech_id, "address_id": address_id,
                                  "previous_address_id": old[0] if old else None})

    def delete_address(self, session: Session, tech_id: int, address_id: int) -> None:
        """Delete an owned address without automatically selecting a new primary."""
        self._require_admin(session)
        self._positive_id(tech_id, "tech_id")
        self._positive_id(address_id, "address_id")
        with self.auth.connection() as connection:
            self._require_technician(connection, tech_id)
            address = self._require_address(connection, tech_id, address_id)
            connection.execute("DELETE FROM TechAddresses WHERE address_id=?", (address_id,))
            record_event(connection, "technician_address_deleted", actor_user_id=session.user_id,
                         details={"tech_id": tech_id, "address_id": address_id,
                                  "was_primary": bool(address["is_primary"])})
