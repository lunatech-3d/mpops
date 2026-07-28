"""Operational technician assignment and assignment-history services."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError


class AssignmentService:
    """Assign active technicians to Jobs while retaining assignment history."""

    def __init__(self, auth: AuthService):
        self.auth = auth

    @staticmethod
    def _require_operator(session: Session | None) -> None:
        if session is None or session.role not in {"admin", "operator"}:
            raise AuthorizationError("Administrator or operator role required")

    @staticmethod
    def _positive_id(value: Any, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} must be a positive integer")
        return value

    @staticmethod
    def _clean_notes(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("assignment_notes must be text")
        value = value.strip()
        if not value:
            return None
        if len(value) > 4000:
            raise ValueError("assignment_notes is too long")
        return value

    @staticmethod
    def _require_job(connection: sqlite3.Connection, job_id: int) -> sqlite3.Row:
        row = connection.execute(
            "SELECT job_id, external_job_id, job_status FROM Jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Job not found")
        return row

    @staticmethod
    def _require_active_technician(
        connection: sqlite3.Connection, tech_id: int
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT tech_id, tech_code, first_name, last_name, status "
            "FROM Techs WHERE tech_id = ?",
            (tech_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Technician not found")
        if row["status"] != "Active":
            raise ValueError("Only active technicians may be assigned")
        return row

    def list_active_technicians(self) -> list[dict[str, Any]]:
        """Return active technicians and their current active-primary workload."""
        with self.auth.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    t.tech_id,
                    t.tech_code,
                    t.first_name,
                    t.middle_name,
                    t.last_name,
                    t.preferred_name,
                    t.company_name,
                    t.email,
                    t.mobile_phone,
                    COUNT(a.job_assignment_id) AS active_job_count
                FROM Techs t
                LEFT JOIN JobAssignments a
                  ON a.tech_id = t.tech_id
                 AND a.assignment_role = 'Primary'
                 AND a.unassigned_at IS NULL
                 AND a.assignment_status NOT IN ('Completed', 'Declined', 'Cancelled')
                WHERE t.status = 'Active'
                GROUP BY t.tech_id
                ORDER BY active_job_count, t.last_name COLLATE NOCASE,
                         t.first_name COLLATE NOCASE, t.tech_id
                """
            )
            return [dict(row) for row in rows]

    def get_active_primary(self, job_id: int) -> dict[str, Any] | None:
        """Return the current primary assignment for a Job, if one exists."""
        self._positive_id(job_id, "job_id")
        with self.auth.connection() as connection:
            row = connection.execute(
                """
                SELECT a.*, t.tech_code, t.first_name, t.middle_name,
                       t.last_name, t.preferred_name, t.email, t.mobile_phone
                FROM JobAssignments a
                JOIN Techs t ON t.tech_id = a.tech_id
                WHERE a.job_id = ?
                  AND a.assignment_role = 'Primary'
                  AND a.unassigned_at IS NULL
                ORDER BY a.job_assignment_id DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_assignment_history(self, job_id: int) -> list[dict[str, Any]]:
        """Return complete assignment history for a Job, newest first."""
        self._positive_id(job_id, "job_id")
        with self.auth.connection() as connection:
            self._require_job(connection, job_id)
            rows = connection.execute(
                """
                SELECT a.*, t.tech_code, t.first_name, t.middle_name,
                       t.last_name, t.preferred_name
                FROM JobAssignments a
                JOIN Techs t ON t.tech_id = a.tech_id
                WHERE a.job_id = ?
                ORDER BY a.assigned_at DESC, a.job_assignment_id DESC
                """,
                (job_id,),
            )
            return [dict(row) for row in rows]

    def assign_primary(
        self,
        session: Session,
        job_id: int,
        tech_id: int,
        assignment_notes: str | None = None,
    ) -> int:
        """Replace the current primary assignment and return the new assignment ID."""
        self._require_operator(session)
        self._positive_id(job_id, "job_id")
        self._positive_id(tech_id, "tech_id")
        notes = self._clean_notes(assignment_notes)
        now = utc_now_iso()

        with self.auth.connection() as connection:
            job = self._require_job(connection, job_id)
            technician = self._require_active_technician(connection, tech_id)
            current = connection.execute(
                "SELECT * FROM JobAssignments WHERE job_id = ? "
                "AND assignment_role = 'Primary' AND unassigned_at IS NULL",
                (job_id,),
            ).fetchone()

            if current is not None and int(current["tech_id"]) == tech_id:
                if notes != current["assignment_notes"]:
                    connection.execute(
                        "UPDATE JobAssignments SET assignment_notes = ?, updated_at = ? "
                        "WHERE job_assignment_id = ?",
                        (notes, now, current["job_assignment_id"]),
                    )
                return int(current["job_assignment_id"])

            if current is not None:
                connection.execute(
                    "UPDATE JobAssignments SET assignment_status = 'Reassigned', "
                    "unassigned_at = ?, updated_at = ? WHERE job_assignment_id = ?",
                    (now, now, current["job_assignment_id"]),
                )

            cursor = connection.execute(
                """
                INSERT INTO JobAssignments (
                    job_id, tech_id, assignment_role, assignment_status,
                    assigned_at, assigned_by, assignment_notes, created_at
                ) VALUES (?, ?, 'Primary', 'Assigned', ?, ?, ?, ?)
                """,
                (job_id, tech_id, now, session.user_id, notes, now),
            )
            assignment_id = int(cursor.lastrowid)

            if job["job_status"] in {"Requested", "Scheduling", "Scheduled"}:
                connection.execute(
                    "UPDATE Jobs SET job_status = 'Assigned', updated_at = ?, "
                    "updated_by = ? WHERE job_id = ?",
                    (now, session.user_id, job_id),
                )

            record_event(
                connection,
                "job_primary_assigned",
                actor_user_id=session.user_id,
                details={
                    "job_id": job_id,
                    "external_job_id": job["external_job_id"],
                    "job_assignment_id": assignment_id,
                    "tech_id": tech_id,
                    "tech_code": technician["tech_code"],
                    "replaced_assignment_id": (
                        int(current["job_assignment_id"]) if current is not None else None
                    ),
                },
            )
            return assignment_id

    def unassign_primary(
        self, session: Session, job_id: int, assignment_notes: str | None = None
    ) -> bool:
        """Close the active primary assignment while preserving its history."""
        self._require_operator(session)
        self._positive_id(job_id, "job_id")
        notes = self._clean_notes(assignment_notes)
        now = utc_now_iso()

        with self.auth.connection() as connection:
            job = self._require_job(connection, job_id)
            current = connection.execute(
                "SELECT * FROM JobAssignments WHERE job_id = ? "
                "AND assignment_role = 'Primary' AND unassigned_at IS NULL",
                (job_id,),
            ).fetchone()
            if current is None:
                return False

            final_notes = notes if notes is not None else current["assignment_notes"]
            connection.execute(
                "UPDATE JobAssignments SET assignment_status = 'Unassigned', "
                "unassigned_at = ?, assignment_notes = ?, updated_at = ? "
                "WHERE job_assignment_id = ?",
                (now, final_notes, now, current["job_assignment_id"]),
            )
            if job["job_status"] == "Assigned":
                connection.execute(
                    "UPDATE Jobs SET job_status = 'Scheduling', updated_at = ?, "
                    "updated_by = ? WHERE job_id = ?",
                    (now, session.user_id, job_id),
                )
            record_event(
                connection,
                "job_primary_unassigned",
                actor_user_id=session.user_id,
                details={
                    "job_id": job_id,
                    "external_job_id": job["external_job_id"],
                    "job_assignment_id": int(current["job_assignment_id"]),
                    "tech_id": int(current["tech_id"]),
                },
            )
            return True
