"""Job repository operations for the first Matterport Ops implementation slice."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError


_JOB_FIELDS = frozenset({
    "project_id", "market_id", "external_job_id", "project_name_source", "client_name_source",
    "job_status", "request_received_at", "scheduled_start_at", "actual_start_at",
    "completed_at", "cancelled_at", "capture_address_raw", "address_1", "address_2",
    "city", "state", "postal_code", "county", "country", "requested_capture_size",
    "additional_details", "scheduling_link", "floor_plan_attachments",
    "onsite_contact_name", "onsite_contact_email", "onsite_contact_phone",
    "preferred_datetime_1", "preferred_datetime_2", "alternate_datetime_1",
    "alternate_datetime_2", "alternate_datetime_3", "cancellation_reason",
    "internal_notes", "archived_at", "archive_reason",
})
_REQUIRED_JOB_FIELDS = frozenset({"external_job_id"})
_TIMESTAMP_FIELDS = frozenset({
    "request_received_at", "scheduled_start_at", "actual_start_at", "completed_at",
    "cancelled_at", "archived_at", "preferred_datetime_1", "preferred_datetime_2",
    "alternate_datetime_1", "alternate_datetime_2", "alternate_datetime_3",
})
_LONG_TEXT_FIELDS = frozenset({
    "additional_details", "floor_plan_attachments", "internal_notes",
})
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_TECHNICIAN_UNCHANGED = object()

# Fields whose normalized operational value is initially owned by an external import,
# but becomes locally owned when an operator explicitly changes it. Adding another
# import-managed field here extends the same ownership mechanism without schema work.
IMPORT_PROTECTABLE_JOB_FIELDS = frozenset({
    "address_1", "address_2", "city", "state", "postal_code", "county", "country",
})

_JOB_SUMMARY_SELECT = """
    SELECT
        j.*,
        m.market_name,
        m.state AS market_state,
        m.status AS market_status,
        p.project_code,
        p.project_name,
        p.client_name AS project_client_name,
        a.job_assignment_id AS primary_assignment_id,
        a.tech_id AS primary_technician_id,
        a.assignment_status AS primary_assignment_status,
        t.tech_code AS primary_tech_code,
        t.first_name AS primary_tech_first_name,
        t.last_name AS primary_tech_last_name,
        COALESCE((
            SELECT SUM(jf.ct_rate + jf.ct_travel_payout + jf.ct_off_hours_payout)
            FROM JobFinancials jf
            WHERE jf.job_id = j.job_id
        ), 0) AS expected_payout
    FROM Jobs j
    LEFT JOIN Markets m ON m.market_id = j.market_id
    LEFT JOIN Projects p ON p.project_id = j.project_id
    LEFT JOIN JobAssignments a
        ON a.job_id = j.job_id
       AND a.assignment_role = 'Primary'
       AND a.assignment_status = 'Assigned'
       AND a.unassigned_at IS NULL
    LEFT JOIN Techs t ON t.tech_id = a.tech_id
"""


class JobsService:
    """Create, update, retrieve, list, and search operational Jobs."""

    def __init__(self, auth: AuthService):
        self.auth = auth

    @staticmethod
    def _require_operator(session: Session | None) -> None:
        if session is None or session.role not in {"admin", "operator"}:
            raise AuthorizationError("Administrator or operator role required")

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
    def _page_value(value: Any, label: str, *, minimum: int, maximum: int | None = None) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{label} must be an integer of at least {minimum}")
        if maximum is not None and value > maximum:
            raise ValueError(f"{label} must not exceed {maximum}")
        return value

    @staticmethod
    def _clean_text(field: str, value: Any, *, required: bool = False) -> str | None:
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
        limit = 4000 if field in _LONG_TEXT_FIELDS else 1000 if field == "capture_address_raw" else 255
        if len(value) > limit:
            raise ValueError(f"{field} is too long")
        if field == "onsite_contact_email" and not _EMAIL.fullmatch(value):
            raise ValueError("On-site contact email is invalid")
        if field == "state" and len(value) == 2 and value.isalpha():
            value = value.upper()
        return value

    @staticmethod
    def _clean_timestamp(field: str, value: Any) -> str | None:
        value = JobsService._clean_text(field, value)
        if value is None:
            return None
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-compatible date or timestamp") from exc
        return value

    @staticmethod
    def _clean_number(field: str, value: Any) -> float | None:
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            raise ValueError(f"{field} must be numeric")
        try:
            number = Decimal(str(value).strip())
        except (InvalidOperation, AttributeError) as exc:
            raise ValueError(f"{field} must be numeric") from exc
        if not number.is_finite() or number < 0:
            raise ValueError(f"{field} must be zero or greater")
        return float(number)

    @classmethod
    def _clean_job(cls, data: dict[str, Any], *, creating: bool) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("job data must be a dictionary")
        invalid = set(data) - _JOB_FIELDS
        if invalid:
            raise ValueError(f"Unsupported job fields: {', '.join(sorted(invalid))}")
        if creating:
            missing = _REQUIRED_JOB_FIELDS - set(data)
            if missing:
                raise ValueError(f"Missing required fields: {', '.join(sorted(missing))}")
        elif not data:
            raise ValueError("At least one job field is required")

        clean: dict[str, Any] = {}
        for field, value in data.items():
            if field in {"project_id", "market_id"}:
                clean[field] = None if value in (None, "") else cls._positive_id(value, field)
            elif field == "requested_capture_size":
                clean[field] = cls._clean_number(field, value)
            elif field in _TIMESTAMP_FIELDS:
                clean[field] = cls._clean_timestamp(field, value)
            else:
                clean[field] = cls._clean_text(
                    field, value, required=field in _REQUIRED_JOB_FIELDS
                )
        if creating:
            clean.setdefault("job_status", "Requested")
        return clean

    @staticmethod
    def _require_job(connection: sqlite3.Connection, job_id: int) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM Jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError("Job not found")
        return row

    @staticmethod
    def _require_project(connection: sqlite3.Connection, project_id: int | None) -> None:
        if project_id is None:
            return
        row = connection.execute(
            "SELECT 1 FROM Projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Project not found")

    @staticmethod
    def _require_market(connection: sqlite3.Connection, market_id: int | None) -> None:
        if market_id is None:
            return
        row = connection.execute(
            "SELECT 1 FROM Markets WHERE market_id = ?", (market_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Market not found")

    @staticmethod
    def _summary_row(connection: sqlite3.Connection, job_id: int) -> dict[str, Any]:
        row = connection.execute(
            _JOB_SUMMARY_SELECT + " WHERE j.job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Job not found")
        return dict(row)

    def list_market_options(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        """Return Markets for Job form dropdowns."""
        sql = "SELECT market_id, market_name, state, status FROM Markets"
        parameters: tuple[Any, ...] = ()
        if not include_inactive:
            sql += " WHERE status = ? COLLATE NOCASE"
            parameters = ("Active",)
        sql += " ORDER BY state COLLATE NOCASE, market_name COLLATE NOCASE"
        with self.auth.connection() as connection:
            return [dict(row) for row in connection.execute(sql, parameters)]

    def get_job_activity_counts(self, today: date | None = None) -> dict[str, int]:
        """Count non-cancelled jobs by their local scheduled calendar date.

        ``scheduled_start_at`` is the operational job date imported from OpenTable.
        Its ISO value represents local application time, so the calendar-date prefix
        is compared directly rather than applying UTC conversion in SQLite.
        """
        if today is None:
            today = datetime.now().astimezone().date()
        if not isinstance(today, date):
            raise ValueError("today must be a date")

        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=7)
        month_start = today.replace(day=1)
        month_end = (
            month_start.replace(year=month_start.year + 1, month=1)
            if month_start.month == 12
            else month_start.replace(month=month_start.month + 1)
        )
        tomorrow = today + timedelta(days=1)

        # One Jobs-only aggregate avoids multiplying jobs by financial/source rows.
        # DISTINCT also protects each total if this query is extended with joins.
        sql = """
            SELECT
                COUNT(DISTINCT CASE WHEN scheduled_start_at >= ?
                                     AND scheduled_start_at < ? THEN job_id END) AS today,
                COUNT(DISTINCT CASE WHEN scheduled_start_at >= ?
                                     AND scheduled_start_at < ? THEN job_id END) AS week,
                COUNT(DISTINCT CASE WHEN scheduled_start_at >= ?
                                     AND scheduled_start_at < ? THEN job_id END) AS month
            FROM Jobs
            WHERE job_status <> 'Cancelled' COLLATE NOCASE
              AND scheduled_start_at >= ?
              AND scheduled_start_at < ?
        """
        earliest = min(today, week_start, month_start)
        latest = max(tomorrow, week_end, month_end)
        parameters = tuple(
            boundary.isoformat()
            for boundary in (
                today, tomorrow, week_start, week_end, month_start, month_end,
                earliest, latest,
            )
        )
        with self.auth.connection() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return {name: int(row[name] or 0) for name in ("today", "week", "month")}

    def list_job_activity(self, period: str, today: date | None = None) -> list[dict[str, Any]]:
        """Return scheduled, non-cancelled jobs in a dashboard calendar period."""
        if today is None:
            today = datetime.now().astimezone().date()
        if not isinstance(today, date):
            raise ValueError("today must be a date")

        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        next_month_start = (
            month_start.replace(year=month_start.year + 1, month=1)
            if month_start.month == 12
            else month_start.replace(month=month_start.month + 1)
        )
        following_month_start = (
            next_month_start.replace(year=next_month_start.year + 1, month=1)
            if next_month_start.month == 12
            else next_month_start.replace(month=next_month_start.month + 1)
        )
        ranges = {
            "today": (today, today + timedelta(days=1)),
            "week": (week_start, week_start + timedelta(days=7)),
            "next_week": (week_start + timedelta(days=7), week_start + timedelta(days=14)),
            "month": (
                month_start,
                next_month_start,
            ),
            "next_month": (next_month_start, following_month_start),
        }
        if period not in ranges:
            raise ValueError("period must be today, week, next_week, month, or next_month")
        start, end = ranges[period]

        return self.list_job_activity_range(start, end - timedelta(days=1))

    def list_job_activity_range(self, start: date, end: date) -> list[dict[str, Any]]:
        """Return scheduled jobs from an inclusive local calendar-date range."""
        if not isinstance(start, date) or not isinstance(end, date):
            raise ValueError("start and end must be dates")
        if start > end:
            raise ValueError("From Date cannot be after To Date.")
        exclusive_end = end + timedelta(days=1)

        sql = (
            _JOB_SUMMARY_SELECT
            + " WHERE j.job_status <> 'Cancelled' COLLATE NOCASE"
              " AND j.scheduled_start_at >= ? AND j.scheduled_start_at < ?"
              " ORDER BY j.scheduled_start_at, j.external_job_id COLLATE NOCASE"
        )
        with self.auth.connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    sql, (start.isoformat(), exclusive_end.isoformat())
                )
            ]

    def list_active_technician_options(self) -> list[dict[str, Any]]:
        """Return active technicians for the Job form, in display order."""
        with self.auth.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT tech_id, first_name, last_name FROM Techs "
                "WHERE status = 'Active' "
                "ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE, tech_id"
            )]

    def get_current_primary_assignment(self, job_id: int) -> dict[str, Any] | None:
        """Return the most recent active, assigned primary technician."""
        self._positive_id(job_id, "job_id")
        with self.auth.connection() as connection:
            self._require_job(connection, job_id)
            row = connection.execute(
                """
                SELECT ja.tech_id, t.first_name, t.last_name, t.status
                FROM JobAssignments ja
                JOIN Techs t ON t.tech_id = ja.tech_id
                WHERE ja.job_id = ?
                  AND ja.assignment_role = 'Primary'
                  AND ja.assignment_status = 'Assigned'
                  AND ja.unassigned_at IS NULL
                ORDER BY ja.assigned_at DESC, ja.job_assignment_id DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _set_primary_technician(
        connection: sqlite3.Connection, session: Session, job_id: int, tech_id: int | None
    ) -> bool:
        """Change a primary assignment using the caller's transaction."""
        current = connection.execute(
            "SELECT tech_id FROM JobAssignments WHERE job_id = ? "
            "AND assignment_role = 'Primary' AND assignment_status = 'Assigned' "
            "AND unassigned_at IS NULL ORDER BY assigned_at DESC, "
            "job_assignment_id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if current is not None and int(current["tech_id"]) == tech_id:
            return False

        if tech_id is not None:
            JobsService._positive_id(tech_id, "tech_id")
            technician = connection.execute(
                "SELECT status FROM Techs WHERE tech_id = ?", (tech_id,)
            ).fetchone()
            if technician is None:
                raise LookupError("Technician not found")
            if technician["status"] != "Active":
                raise ValueError("Only active technicians may be assigned")

        connection.execute(
            "UPDATE JobAssignments SET assignment_status = 'Unassigned', "
            "unassigned_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
            "WHERE job_id = ? AND assignment_role = 'Primary' "
            "AND assignment_status = 'Assigned' AND unassigned_at IS NULL",
            (job_id,),
        )
        if tech_id is not None:
            connection.execute(
                "INSERT INTO JobAssignments (job_id, tech_id, assignment_role, "
                "assignment_status, assigned_at, assigned_by, created_at) "
                "VALUES (?, ?, 'Primary', 'Assigned', CURRENT_TIMESTAMP, ?, "
                "CURRENT_TIMESTAMP)",
                (job_id, tech_id, session.user_id),
            )
        return True

    def list_jobs(
        self,
        job_status: str | None = None,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = self._page_value(limit, "limit", minimum=1, maximum=2000)
        offset = self._page_value(offset, "offset", minimum=0)
        parameters: list[Any] = []
        where = ""
        if job_status == "Active":
            where = " WHERE j.job_status NOT IN ('Cancelled', 'Archived') COLLATE NOCASE"
        elif job_status is not None:
            status = self._clean_text("job_status", job_status, required=True)
            where = " WHERE j.job_status = ? COLLATE NOCASE"
            parameters.append(status)
        parameters.extend((limit, offset))
        sql = (
            _JOB_SUMMARY_SELECT
            + where
            + " ORDER BY CASE WHEN j.scheduled_start_at IS NULL THEN 1 ELSE 0 END, "
              "j.scheduled_start_at, j.external_job_id COLLATE NOCASE LIMIT ? OFFSET ?"
        )
        with self.auth.connection() as connection:
            return [dict(row) for row in connection.execute(sql, parameters)]

    def search_jobs(
        self,
        query: str,
        job_status: str | None = None,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str):
            raise ValueError("query must be text")
        query = query.strip()
        if not query:
            return self.list_jobs(job_status, limit=limit, offset=offset)
        limit = self._page_value(limit, "limit", minimum=1, maximum=2000)
        offset = self._page_value(offset, "offset", minimum=0)
        term = f"%{query}%"
        searchable = (
            "j.external_job_id", "j.project_name_source", "j.client_name_source",
            "j.capture_address_raw", "j.address_1", "j.address_2", "j.city", "j.state",
            "j.postal_code", "j.county", "j.job_status", "m.market_name", "m.state",
            "p.project_code", "p.project_name", "p.client_name", "t.tech_code",
            "t.first_name", "t.last_name",
            "(SELECT GROUP_CONCAT(jf.ap_invoice_number, ' ') FROM JobFinancials jf "
            "WHERE jf.job_id = j.job_id)",
        )
        conditions = [
            "(" + " OR ".join(
                f"COALESCE({field}, '') LIKE ? COLLATE NOCASE" for field in searchable
            ) + ")"
        ]
        parameters: list[Any] = [term] * len(searchable)
        if job_status == "Active":
            conditions.append("j.job_status NOT IN ('Cancelled', 'Archived') COLLATE NOCASE")
        elif job_status is not None:
            conditions.append("j.job_status = ? COLLATE NOCASE")
            parameters.append(self._clean_text("job_status", job_status, required=True))
        parameters.extend((limit, offset))
        sql = (
            _JOB_SUMMARY_SELECT
            + " WHERE " + " AND ".join(conditions)
            + " ORDER BY CASE WHEN j.scheduled_start_at IS NULL THEN 1 ELSE 0 END, "
              "j.scheduled_start_at, j.external_job_id COLLATE NOCASE LIMIT ? OFFSET ?"
        )
        with self.auth.connection() as connection:
            return [dict(row) for row in connection.execute(sql, parameters)]

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        self._positive_id(job_id, "job_id")
        with self.auth.connection() as connection:
            row = connection.execute(
                _JOB_SUMMARY_SELECT + " WHERE j.job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            job = dict(row)
            job["financial_records"] = [dict(financial) for financial in connection.execute(
                "SELECT job_financial_id, ap_invoice_number, ct_rate, "
                "ct_travel_payout, ct_off_hours_payout FROM JobFinancials "
                "WHERE job_id = ? ORDER BY job_financial_id",
                (job_id,),
            )]
            override_rows = connection.execute(
                "SELECT field_name, source_system FROM JobFieldOverrides "
                "WHERE job_id = ? ORDER BY field_name COLLATE NOCASE, "
                "source_system COLLATE NOCASE",
                (job_id,),
            ).fetchall()
            job["protected_fields"] = sorted({row["field_name"] for row in override_rows})
            job["field_overrides"] = [dict(row) for row in override_rows]
            return job

    def get_job_by_external_id(self, external_job_id: str) -> dict[str, Any] | None:
        external_job_id = self._clean_text(
            "external_job_id", external_job_id, required=True
        )
        with self.auth.connection() as connection:
            row = connection.execute(
                _JOB_SUMMARY_SELECT
                + " WHERE j.external_job_id = ? COLLATE NOCASE",
                (external_job_id,),
            ).fetchone()
            return dict(row) if row else None

    def create_job(
        self, session: Session, job_data: dict[str, Any],
        primary_technician_id: int | None = None,
    ) -> int:
        self._require_operator(session)
        clean = self._clean_job(job_data, creating=True)
        fields = list(clean)
        try:
            with self.auth.connection() as connection:
                self._require_project(connection, clean.get("project_id"))
                self._require_market(connection, clean.get("market_id"))
                cursor = connection.execute(
                    f"INSERT INTO Jobs ({','.join(fields)}, created_at, created_by) "
                    f"VALUES ({','.join('?' for _ in fields)}, ?, ?)",
                    [clean[field] for field in fields] + [utc_now_iso(), session.user_id],
                )
                job_id = int(cursor.lastrowid)
                if primary_technician_id is not None:
                    self._set_primary_technician(
                        connection, session, job_id, primary_technician_id
                    )
                record_event(
                    connection,
                    "job_created",
                    actor_user_id=session.user_id,
                    details={
                        "job_id": job_id,
                        "external_job_id": clean["external_job_id"],
                    },
                )
                return job_id
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            if "external_job_id" in message or "unique" in message:
                raise ValueError("A Job with this external Job ID already exists") from exc
            raise ValueError("Job data conflicts with an existing record") from exc

    def update_job(
        self, session: Session, job_id: int, changes: dict[str, Any],
        primary_technician_id: int | None | object = _TECHNICIAN_UNCHANGED,
    ) -> dict[str, Any]:
        self._require_operator(session)
        self._positive_id(job_id, "job_id")
        clean = self._clean_job(changes, creating=False) if changes else {}
        if not clean and primary_technician_id is _TECHNICIAN_UNCHANGED:
            raise ValueError("At least one job field is required")
        try:
            with self.auth.connection() as connection:
                before = dict(self._require_job(connection, job_id))
                if "project_id" in clean:
                    self._require_project(connection, clean["project_id"])
                if "market_id" in clean:
                    self._require_market(connection, clean["market_id"])
                if clean:
                    assignments = ",".join(f"{field} = ?" for field in clean)
                    connection.execute(
                        f"UPDATE Jobs SET {assignments}, updated_at = ?, updated_by = ? "
                        "WHERE job_id = ?",
                        [*clean.values(), utc_now_iso(), session.user_id, job_id],
                    )
                    changed_import_fields = {
                        field for field in clean
                        if field in IMPORT_PROTECTABLE_JOB_FIELDS
                        and before[field] != clean[field]
                    }
                    if changed_import_fields:
                        source_systems = [row[0] for row in connection.execute(
                            "SELECT DISTINCT source_system FROM JobSourceRecords WHERE job_id = ?",
                            (job_id,),
                        )]
                        for source_system in source_systems:
                            for field in changed_import_fields:
                                cursor = connection.execute(
                                    "INSERT OR IGNORE INTO JobFieldOverrides "
                                    "(job_id, field_name, source_system, protected_at, protected_by) "
                                    "VALUES (?, ?, ?, ?, ?)",
                                    (job_id, field, source_system, utc_now_iso(), session.user_id),
                                )
                                if cursor.rowcount:
                                    record_event(
                                        connection, "job_field_override_created",
                                        actor_user_id=session.user_id,
                                        details={"job_id": job_id, "field_name": field,
                                                 "source_system": source_system},
                                    )
                assignment_changed = False
                if primary_technician_id is not _TECHNICIAN_UNCHANGED:
                    assignment_changed = self._set_primary_technician(
                        connection, session, job_id, primary_technician_id
                    )
                record_event(
                    connection,
                    "job_updated",
                    actor_user_id=session.user_id,
                    details={
                        "job_id": job_id,
                        "external_job_id": clean.get(
                            "external_job_id", before["external_job_id"]
                        ),
                        "fields_changed": sorted(clean) + (
                            ["primary_technician"] if assignment_changed else []
                        ),
                        "before": {field: before[field] for field in clean},
                        "after": clean,
                    },
                )
                return self._summary_row(connection, job_id)
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            if "external_job_id" in message or "unique" in message:
                raise ValueError("A Job with this external Job ID already exists") from exc
            raise ValueError("Job data conflicts with an existing record") from exc

    def clear_job_field_override(
        self, session: Session, job_id: int, field_name: str,
        source_system: str = "OpenTable",
    ) -> bool:
        """Return a locally protected Job field to the named importer's ownership."""
        self._require_operator(session)
        self._positive_id(job_id, "job_id")
        if field_name not in IMPORT_PROTECTABLE_JOB_FIELDS:
            raise ValueError("Field is not import-protectable")
        source_system = self._clean_text("source_system", source_system, required=True)
        with self.auth.connection() as connection:
            self._require_job(connection, job_id)
            cursor = connection.execute(
                "DELETE FROM JobFieldOverrides WHERE job_id = ? AND field_name = ? "
                "AND source_system = ? COLLATE NOCASE",
                (job_id, field_name, source_system),
            )
            removed = bool(cursor.rowcount)
            if removed:
                record_event(
                    connection, "job_field_override_cleared",
                    actor_user_id=session.user_id,
                    details={"job_id": job_id, "field_name": field_name,
                             "source_system": source_system},
                )
            return removed

    def cancel_job(self, session: Session, job_id: int, reason: str, notes: str | None = None) -> dict[str, Any]:
        """Cancel a Job without removing any operational or financial history."""
        self._require_operator(session)
        self._positive_id(job_id, "job_id")
        reason = self._clean_text("cancellation_reason", reason, required=True)
        notes = self._clean_text("internal_notes", notes)
        with self.auth.connection() as connection:
            before = dict(self._require_job(connection, job_id))
            if str(before["job_status"]).casefold() == "cancelled":
                return self._summary_row(connection, job_id)
            now = utc_now_iso()
            connection.execute(
                "UPDATE Jobs SET job_status='Cancelled', cancelled_at=?, cancellation_reason=?, "
                "internal_notes=CASE WHEN ? IS NULL THEN internal_notes WHEN internal_notes IS NULL OR internal_notes='' "
                "THEN ? ELSE internal_notes || char(10) || ? END, updated_at=?, updated_by=? WHERE job_id=?",
                (now, reason, notes, notes, notes, now, session.user_id, job_id),
            )
            record_event(connection, "job_cancelled", actor_user_id=session.user_id, details={
                "job_id": job_id, "external_job_id": before["external_job_id"],
                "address": before["capture_address_raw"], "prior_status": before["job_status"],
                "new_status": "Cancelled", "reason": reason, "notes": notes,
                "actor_username": session.username,
            })
            return self._summary_row(connection, job_id)

    def archive_job(self, session: Session, job_id: int, reason: str, notes: str | None = None) -> dict[str, Any]:
        """Archive a Job while preserving every related record."""
        self._require_admin(session)
        self._positive_id(job_id, "job_id")
        reason = self._clean_text("archive_reason", reason, required=True)
        notes = self._clean_text("internal_notes", notes)
        with self.auth.connection() as connection:
            before = dict(self._require_job(connection, job_id))
            if str(before["job_status"]).casefold() == "archived":
                return self._summary_row(connection, job_id)
            now = utc_now_iso()
            connection.execute(
                "UPDATE Jobs SET job_status='Archived', archived_at=?, archive_reason=?, "
                "internal_notes=CASE WHEN ? IS NULL THEN internal_notes WHEN internal_notes IS NULL OR internal_notes='' "
                "THEN ? ELSE internal_notes || char(10) || ? END, updated_at=?, updated_by=? WHERE job_id=?",
                (now, reason, notes, notes, notes, now, session.user_id, job_id),
            )
            record_event(connection, "job_archived", actor_user_id=session.user_id, details={
                "job_id": job_id, "external_job_id": before["external_job_id"],
                "address": before["capture_address_raw"], "prior_status": before["job_status"],
                "new_status": "Archived", "reason": reason, "notes": notes,
                "actor_username": session.username,
            })
            return self._summary_row(connection, job_id)

    @staticmethod
    def _deletion_blockers(connection: sqlite3.Connection, job_id: int) -> list[str]:
        blockers: list[str] = []
        for row in connection.execute(
            "SELECT i.document_number, b.batch_status FROM MatterportPaymentItems i "
            "JOIN MatterportPaymentBatches b ON b.payment_batch_id=i.payment_batch_id WHERE i.job_id=?",
            (job_id,),
        ):
            blockers.append(f"Matterport Payment Item {row['document_number']} (batch {row['batch_status']})")
        for row in connection.execute(
            "SELECT pe.technician_payment_earning_id, p.technician_payment_id, p.payment_status "
            "FROM TechnicianPaymentEarnings pe "
            "JOIN TechnicianPayments p ON p.technician_payment_id=pe.technician_payment_id "
            "JOIN TechnicianJobEarnings e ON e.technician_earning_id=pe.technician_earning_id "
            "WHERE e.job_id=?",
            (job_id,),
        ):
            blockers.append(
                f"Technician payment allocation #{row['technician_payment_earning_id']} "
                f"(payment #{row['technician_payment_id']}, {row['payment_status']})"
            )
        for row in connection.execute(
            "SELECT e.technician_earning_id, e.earning_status, t.first_name, t.last_name "
            "FROM TechnicianJobEarnings e JOIN Techs t ON t.tech_id=e.tech_id WHERE e.job_id=?",
            (job_id,),
        ):
            blockers.append(
                f"Technician earning #{row['technician_earning_id']} for "
                f"{row['first_name']} {row['last_name']} ({row['earning_status']})"
            )
        for row in connection.execute(
            "SELECT job_assignment_id, assignment_status FROM JobAssignments WHERE job_id=? "
            "AND (accepted_at IS NOT NULL OR completed_at IS NOT NULL OR assignment_status IN ('Accepted','Completed'))",
            (job_id,),
        ):
            blockers.append(f"Accepted or completed Job Assignment #{row['job_assignment_id']}")
        return blockers

    def get_job_deletion_blockers(self, session: Session, job_id: int) -> list[str]:
        self._require_admin(session)
        self._positive_id(job_id, "job_id")
        with self.auth.connection() as connection:
            self._require_job(connection, job_id)
            return self._deletion_blockers(connection, job_id)

    def delete_draft_job(self, session: Session, job_id: int, reason: str) -> dict[str, Any]:
        """Permanently delete only a preliminary Job, atomically, after blocker checks."""
        self._require_admin(session)
        self._positive_id(job_id, "job_id")
        reason = self._clean_text("reason", reason, required=True)
        with self.auth.connection() as connection:
            job = dict(self._require_job(connection, job_id))
            blockers = self._deletion_blockers(connection, job_id)
            if blockers:
                raise ValueError("This Job cannot be permanently deleted because it is linked to:\n\n- "
                                 + "\n- ".join(blockers) + "\n\nCancel or archive the Job instead.")
            # JobFinancials is a draft child. Its imported or calculated values do not
            # establish payment finalization; protected downstream records are checked above.
            connection.execute("DELETE FROM JobFinancials WHERE job_id=?", (job_id,))
            connection.execute("DELETE FROM JobAssignments WHERE job_id=?", (job_id,))
            connection.execute("DELETE FROM JobSourceRecords WHERE job_id=?", (job_id,))
            connection.execute("DELETE FROM Jobs WHERE job_id=?", (job_id,))
            details = {"deleted_job_id": job_id, "external_job_id": job["external_job_id"],
                       "address": job["capture_address_raw"], "prior_status": job["job_status"],
                       "reason": reason, "actor_username": session.username}
            record_event(connection, "draft_job_deleted", actor_user_id=session.user_id, details=details)
            return details
