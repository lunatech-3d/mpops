"""Service API for Matterport receipt batches and imported Tipalti items.

Money is accepted and returned exclusively as integer cents.  This module owns
all access to the Matterport payment tables so callers do not need to know the
underlying schema.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError


BATCH_STATUSES = (
    "Draft", "Imported", "Needs Review", "Reconciled", "Approved", "Closed", "Cancelled"
)
PAYMENT_ITEM_STATUSES = (
    "Unmatched", "Matched", "Ambiguous", "Missing Job", "Amount Review", "Excluded"
)
_NEXT_BATCH_STATUS = {
    "Draft": "Imported",
    "Imported": "Needs Review",
    "Needs Review": "Reconciled",
    "Reconciled": "Approved",
    "Approved": "Closed",
}
_BATCH_FIELDS = frozenset({
    "payment_date", "payment_amount_cents", "payment_method", "payer_name",
    "source_system", "batch_status", "source_email_subject",
    "source_email_received_at", "notes",
})
_ITEM_FIELDS = frozenset({
    "document_number", "document_type", "document_date", "description_raw",
    "amount_received_cents", "job_id", "match_status", "match_method", "match_notes",
})
_TEXT_LIMITS = {
    "payment_method": 100, "payer_name": 255, "source_system": 100,
    "source_email_subject": 1000, "notes": 4000, "document_number": 255,
    "document_type": 100, "description_raw": 4000, "match_method": 255,
    "match_notes": 4000,
}


class PaymentService:
    """Create, reconcile, and query Matterport payment receipts."""

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
    def _cents(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} must be integer cents")
        if value < 0:
            raise ValueError(f"{field} cannot be negative")
        return value

    @staticmethod
    def _text(field: str, value: Any, *, required: bool = False) -> str | None:
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
        if len(value) > _TEXT_LIMITS.get(field, 255):
            raise ValueError(f"{field} is too long")
        return value

    @classmethod
    def _date(cls, field: str, value: Any, *, required: bool = False) -> str | None:
        value = cls._text(field, value, required=required)
        if value is None:
            return None
        try:
            # Both source dates and ISO timestamps are deliberately accepted.
            datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        except ValueError:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{field} must be an ISO-compatible date or timestamp") from exc
        return value

    @staticmethod
    def validate_batch_status(status: Any) -> str:
        if not isinstance(status, str) or status not in BATCH_STATUSES:
            raise ValueError(f"Invalid batch status: {status}")
        return status

    @staticmethod
    def validate_payment_status(status: Any) -> str:
        """Validate the reconciliation status stored on a payment item."""
        if not isinstance(status, str) or status not in PAYMENT_ITEM_STATUSES:
            raise ValueError(f"Invalid payment status: {status}")
        return status

    @classmethod
    def validate_batch_status_transition(cls, current: Any, requested: Any) -> None:
        current = cls.validate_batch_status(current)
        requested = cls.validate_batch_status(requested)
        if requested == current:
            return
        if requested == "Cancelled" and current != "Closed":
            return
        if _NEXT_BATCH_STATUS.get(current) != requested:
            raise ValueError(f"Invalid batch status transition: {current} to {requested}")

    @classmethod
    def _clean_batch(cls, data: Any, *, creating: bool) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("payment batch data must be a dictionary")
        invalid = set(data) - _BATCH_FIELDS
        if invalid:
            raise ValueError(f"Unsupported payment batch fields: {', '.join(sorted(invalid))}")
        if creating and "payment_date" not in data:
            raise ValueError("payment_date is required")
        if creating and "payment_amount_cents" not in data:
            raise ValueError("payment_amount_cents is required")
        if not creating and not data:
            raise ValueError("At least one payment batch field is required")
        clean: dict[str, Any] = {}
        for field, value in data.items():
            if field == "payment_amount_cents":
                clean[field] = cls._cents(value, field)
            elif field == "batch_status":
                clean[field] = cls.validate_batch_status(value)
            elif field in {"payment_date", "source_email_received_at"}:
                clean[field] = cls._date(field, value, required=field == "payment_date")
            else:
                clean[field] = cls._text(field, value)
        if creating:
            clean.setdefault("batch_status", "Draft")
        return clean

    @classmethod
    def _clean_item(cls, data: Any, *, creating: bool) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("payment item data must be a dictionary")
        invalid = set(data) - _ITEM_FIELDS
        if invalid:
            raise ValueError(f"Unsupported payment item fields: {', '.join(sorted(invalid))}")
        if creating:
            for required in ("document_number", "amount_received_cents"):
                if required not in data:
                    raise ValueError(f"{required} is required")
        elif not data:
            raise ValueError("At least one payment item field is required")
        clean: dict[str, Any] = {}
        for field, value in data.items():
            if field == "amount_received_cents":
                clean[field] = cls._cents(value, field)
            elif field == "job_id":
                clean[field] = None if value in (None, "") else cls._positive_id(value, field)
            elif field == "match_status":
                clean[field] = cls.validate_payment_status(value)
            elif field == "document_date":
                clean[field] = cls._date(field, value)
            else:
                clean[field] = cls._text(field, value, required=field == "document_number")
        if creating:
            clean.setdefault("match_status", "Unmatched")
        return clean

    @staticmethod
    def _require_batch(connection: sqlite3.Connection, batch_id: int) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM MatterportPaymentBatches WHERE payment_batch_id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Payment batch not found")
        return row

    @staticmethod
    def _require_item(connection: sqlite3.Connection, item_id: int) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM MatterportPaymentItems WHERE payment_item_id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Payment item not found")
        return row

    @staticmethod
    def _require_job(connection: sqlite3.Connection, job_id: int | None) -> None:
        if job_id is not None and connection.execute(
            "SELECT 1 FROM Jobs WHERE job_id = ?", (job_id,)
        ).fetchone() is None:
            raise ValueError("Invalid Job reference")

    def create_payment_batch(self, session: Session, batch_data: dict[str, Any]) -> int:
        self._require_operator(session)
        clean = self._clean_batch(batch_data, creating=True)
        fields = list(clean)
        with self.auth.connection() as connection:
            cursor = connection.execute(
                f"INSERT INTO MatterportPaymentBatches ({','.join(fields)}, created_at, created_by) "
                f"VALUES ({','.join('?' for _ in fields)}, ?, ?)",
                [*clean.values(), utc_now_iso(), session.user_id],
            )
            batch_id = int(cursor.lastrowid)
            record_event(connection, "payment_batch_created", actor_user_id=session.user_id,
                         details={"payment_batch_id": batch_id})
            return batch_id

    def update_payment_batch(
        self, session: Session, payment_batch_id: int, changes: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_operator(session)
        self._positive_id(payment_batch_id, "payment_batch_id")
        clean = self._clean_batch(changes, creating=False)
        with self.auth.connection() as connection:
            before = self._require_batch(connection, payment_batch_id)
            if "batch_status" in clean:
                self.validate_batch_status_transition(before["batch_status"], clean["batch_status"])
            assignments = ",".join(f"{field} = ?" for field in clean)
            connection.execute(
                f"UPDATE MatterportPaymentBatches SET {assignments}, updated_at = ?, updated_by = ? "
                "WHERE payment_batch_id = ?",
                [*clean.values(), utc_now_iso(), session.user_id, payment_batch_id],
            )
            record_event(connection, "payment_batch_updated", actor_user_id=session.user_id,
                         details={"payment_batch_id": payment_batch_id,
                                  "fields_changed": sorted(clean)})
            return dict(self._require_batch(connection, payment_batch_id))

    def get_payment_batch(self, payment_batch_id: int) -> dict[str, Any] | None:
        self._positive_id(payment_batch_id, "payment_batch_id")
        with self.auth.connection() as connection:
            row = connection.execute(
                "SELECT * FROM MatterportPaymentBatches WHERE payment_batch_id = ?",
                (payment_batch_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_payment_batches(self, batch_status: str | None = None) -> list[dict[str, Any]]:
        parameters: tuple[Any, ...] = ()
        where = ""
        if batch_status is not None:
            where = " WHERE batch_status = ?"
            parameters = (self.validate_batch_status(batch_status),)
        with self.auth.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM MatterportPaymentBatches" + where
                + " ORDER BY payment_date DESC, payment_batch_id DESC", parameters
            )]

    def delete_payment_batch(self, session: Session, payment_batch_id: int) -> bool:
        self._require_operator(session)
        self._positive_id(payment_batch_id, "payment_batch_id")
        with self.auth.connection() as connection:
            batch = self._require_batch(connection, payment_batch_id)
            if batch["batch_status"] != "Draft":
                raise ValueError("Only Draft payment batches may be deleted")
            # Child rows are explicitly removed because the migration intentionally
            # uses restrictive foreign keys rather than database cascades.
            connection.execute(
                "DELETE FROM MatterportPaymentItems WHERE payment_batch_id = ?",
                (payment_batch_id,),
            )
            connection.execute(
                "DELETE FROM MatterportPaymentBatches WHERE payment_batch_id = ?",
                (payment_batch_id,),
            )
            record_event(connection, "payment_batch_deleted", actor_user_id=session.user_id,
                         details={"payment_batch_id": payment_batch_id})
            return True

    def find_duplicate_document(self, document_number: str) -> dict[str, Any] | None:
        document_number = self._text("document_number", document_number, required=True)
        with self.auth.connection() as connection:
            row = connection.execute(
                "SELECT * FROM MatterportPaymentItems WHERE document_number = ? COLLATE NOCASE",
                (document_number,),
            ).fetchone()
            return dict(row) if row else None

    def add_payment_item(
        self, session: Session, payment_batch_id: int, item_data: dict[str, Any]
    ) -> int:
        self._require_operator(session)
        self._positive_id(payment_batch_id, "payment_batch_id")
        clean = self._clean_item(item_data, creating=True)
        with self.auth.connection() as connection:
            batch = self._require_batch(connection, payment_batch_id)
            if batch["batch_status"] != "Draft":
                raise ValueError("Payment items may only be imported into Draft batches")
            duplicate = connection.execute(
                "SELECT payment_item_id, payment_batch_id FROM MatterportPaymentItems "
                "WHERE document_number = ? COLLATE NOCASE", (clean["document_number"],)
            ).fetchone()
            if duplicate:
                record_event(connection, "payment_document_duplicate_detected",
                             actor_user_id=session.user_id,
                             details={"document_number": clean["document_number"],
                                      "existing_payment_item_id": duplicate["payment_item_id"]})
                # Keep the required duplicate audit event even though validation aborts the import.
                connection.commit()
                raise ValueError("Document number has already been imported")
            self._require_job(connection, clean.get("job_id"))
            fields = list(clean)
            try:
                cursor = connection.execute(
                    f"INSERT INTO MatterportPaymentItems (payment_batch_id,{','.join(fields)},created_at) "
                    f"VALUES (?,{','.join('?' for _ in fields)},?)",
                    [payment_batch_id, *clean.values(), utc_now_iso()],
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Payment item conflicts with an existing record") from exc
            item_id = int(cursor.lastrowid)
            record_event(connection, "payment_item_imported", actor_user_id=session.user_id,
                         details={"payment_item_id": item_id,
                                  "payment_batch_id": payment_batch_id,
                                  "document_number": clean["document_number"]})
            return item_id

    def update_payment_item(
        self, session: Session, payment_item_id: int, changes: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_operator(session)
        self._positive_id(payment_item_id, "payment_item_id")
        clean = self._clean_item(changes, creating=False)
        with self.auth.connection() as connection:
            item = self._require_item(connection, payment_item_id)
            batch = self._require_batch(connection, item["payment_batch_id"])
            if batch["batch_status"] != "Draft":
                raise ValueError("Payment items may only be updated in Draft batches")
            if "document_number" in clean:
                duplicate = connection.execute(
                    "SELECT payment_item_id FROM MatterportPaymentItems "
                    "WHERE document_number = ? COLLATE NOCASE AND payment_item_id <> ?",
                    (clean["document_number"], payment_item_id),
                ).fetchone()
                if duplicate:
                    record_event(connection, "payment_document_duplicate_detected",
                                 actor_user_id=session.user_id,
                                 details={"document_number": clean["document_number"],
                                          "existing_payment_item_id": duplicate[0]})
                    connection.commit()
                    raise ValueError("Document number has already been imported")
            if "job_id" in clean:
                self._require_job(connection, clean["job_id"])
            assignments = ",".join(f"{field} = ?" for field in clean)
            connection.execute(
                f"UPDATE MatterportPaymentItems SET {assignments}, updated_at = ? "
                "WHERE payment_item_id = ?", [*clean.values(), utc_now_iso(), payment_item_id]
            )
            record_event(connection, "payment_item_updated", actor_user_id=session.user_id,
                         details={"payment_item_id": payment_item_id,
                                  "fields_changed": sorted(clean)})
            return dict(self._require_item(connection, payment_item_id))

    def delete_payment_item(self, session: Session, payment_item_id: int) -> bool:
        self._require_operator(session)
        self._positive_id(payment_item_id, "payment_item_id")
        with self.auth.connection() as connection:
            item = self._require_item(connection, payment_item_id)
            batch = self._require_batch(connection, item["payment_batch_id"])
            if batch["batch_status"] != "Draft":
                raise ValueError("Payment items may only be deleted from Draft batches")
            connection.execute(
                "DELETE FROM MatterportPaymentItems WHERE payment_item_id = ?", (payment_item_id,)
            )
            record_event(connection, "payment_item_deleted", actor_user_id=session.user_id,
                         details={"payment_item_id": payment_item_id})
            return True

    def list_payment_items(self, payment_batch_id: int) -> list[dict[str, Any]]:
        self._positive_id(payment_batch_id, "payment_batch_id")
        with self.auth.connection() as connection:
            self._require_batch(connection, payment_batch_id)
            return [dict(row) for row in connection.execute(
                "SELECT * FROM MatterportPaymentItems WHERE payment_batch_id = ? "
                "ORDER BY payment_item_id", (payment_batch_id,)
            )]

    def match_payment_items(self, session: Session, payment_batch_id: int) -> dict[str, int]:
        """Match every item in a batch to ``Jobs.external_job_id`` atomically."""
        self._require_operator(session)
        self._positive_id(payment_batch_id, "payment_batch_id")
        matched = unmatched = 0
        with self.auth.connection() as connection:
            self._require_batch(connection, payment_batch_id)
            items = connection.execute(
                "SELECT payment_item_id, document_number FROM MatterportPaymentItems "
                "WHERE payment_batch_id = ? ORDER BY payment_item_id", (payment_batch_id,)
            ).fetchall()
            for item in items:
                job = connection.execute(
                    "SELECT job_id FROM Jobs WHERE external_job_id = ? COLLATE NOCASE",
                    (item["document_number"],),
                ).fetchone()
                if job:
                    connection.execute(
                        "UPDATE MatterportPaymentItems SET job_id = ?, match_status = 'Matched', "
                        "match_method = 'External Job ID', updated_at = ? WHERE payment_item_id = ?",
                        (job["job_id"], utc_now_iso(), item["payment_item_id"]),
                    )
                    matched += 1
                    action = "payment_item_matched"
                else:
                    connection.execute(
                        "UPDATE MatterportPaymentItems SET job_id = NULL, "
                        "match_status = 'Missing Job', match_method = NULL, updated_at = ? "
                        "WHERE payment_item_id = ?", (utc_now_iso(), item["payment_item_id"])
                    )
                    unmatched += 1
                    action = "payment_item_unmatched"
                record_event(connection, action, actor_user_id=session.user_id,
                             details={"payment_item_id": item["payment_item_id"],
                                      "document_number": item["document_number"],
                                      "job_id": int(job["job_id"]) if job else None})
        return {"matched_count": matched, "unmatched_count": unmatched}

    def get_primary_technician(self, job_id: int) -> dict[str, Any] | None:
        self._positive_id(job_id, "job_id")
        with self.auth.connection() as connection:
            self._require_job(connection, job_id)
            row = connection.execute(
                """
                SELECT t.tech_id, t.first_name, t.last_name
                FROM JobAssignments a
                JOIN Techs t ON t.tech_id = a.tech_id
                WHERE a.job_id = ? AND a.assignment_role = 'Primary'
                  AND a.unassigned_at IS NULL AND a.assignment_status = 'Assigned'
                  AND t.status = 'Active'
                ORDER BY a.job_assignment_id DESC LIMIT 1
                """, (job_id,)
            ).fetchone()
            return dict(row) if row else None

    def calculate_batch_totals(self, payment_batch_id: int) -> dict[str, int]:
        self._positive_id(payment_batch_id, "payment_batch_id")
        with self.auth.connection() as connection:
            batch = self._require_batch(connection, payment_batch_id)
            totals = connection.execute(
                """
                SELECT COALESCE(SUM(amount_received_cents), 0) AS imported_total_cents,
                       COALESCE(SUM(CASE WHEN match_status = 'Matched'
                                        THEN amount_received_cents ELSE 0 END), 0)
                           AS matched_total_cents,
                       COALESCE(SUM(CASE WHEN match_status <> 'Matched'
                                        THEN amount_received_cents ELSE 0 END), 0)
                           AS unmatched_total_cents,
                       COALESCE(SUM(CASE WHEN match_status = 'Matched' THEN 1 ELSE 0 END), 0)
                           AS matched_count,
                       COALESCE(SUM(CASE WHEN match_status <> 'Matched' THEN 1 ELSE 0 END), 0)
                           AS unmatched_count
                FROM MatterportPaymentItems WHERE payment_batch_id = ?
                """, (payment_batch_id,)
            ).fetchone()
            imported = int(totals["imported_total_cents"])
            payment = int(batch["payment_amount_cents"])
            return {
                "payment_amount_cents": payment,
                "imported_total_cents": imported,
                "difference_cents": payment - imported,
                "matched_total_cents": int(totals["matched_total_cents"]),
                "unmatched_total_cents": int(totals["unmatched_total_cents"]),
                "matched_count": int(totals["matched_count"]),
                "unmatched_count": int(totals["unmatched_count"]),
            }
