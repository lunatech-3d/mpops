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
_IMPORT_FIELDS = frozenset({
    "document_number", "document_type", "document_date", "description_raw",
    "amount_received_cents",
})
_BATCH_EDIT_FIELDS = {
    "Draft": _BATCH_FIELDS,
    "Imported": _BATCH_FIELDS - {"payment_date", "payment_amount_cents"},
    "Needs Review": frozenset({"notes", "batch_status"}),
    "Reconciled": frozenset({"notes", "batch_status"}),
    "Approved": frozenset({"notes", "batch_status"}),
    "Closed": frozenset(),
    "Cancelled": frozenset(),
}
_MATCHABLE_BATCH_STATUSES = frozenset({"Draft", "Imported", "Needs Review"})
_TEXT_LIMITS = {
    "payment_method": 100, "payer_name": 255, "source_system": 100,
    "source_email_subject": 1000, "notes": 4000, "document_number": 255,
    "document_type": 100, "description_raw": 4000, "match_method": 255,
    "match_notes": 4000,
}
EXCEPTION_STATUSES = frozenset({"Unmatched", "Missing Job", "Ambiguous",
                                "Amount Review", "Excluded"})
EXCEPTION_GROUPS = {
    "Missing Jobs": frozenset({"Unmatched", "Missing Job"}),
    "Ambiguous Matches": frozenset({"Ambiguous"}),
    "Amount Review": frozenset({"Amount Review"}),
    "Excluded": frozenset({"Excluded"}),
}

# The single authoritative expression for money used by reconciliation.
EFFECTIVE_AMOUNT_SQL = "COALESCE(resolved_amount_cents, amount_received_cents)"


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

    @staticmethod
    def _require_batch_mutation(batch: sqlite3.Row, operation: str,
                                fields: set[str] | frozenset[str] = frozenset()) -> None:
        """Apply the status-based mutability policy in one place."""
        status = batch["batch_status"]
        if operation == "update_batch":
            disallowed = fields - _BATCH_EDIT_FIELDS[status]
            if disallowed:
                raise ValueError(
                    f"{status} payment batches do not allow changes to: "
                    f"{', '.join(sorted(disallowed))}"
                )
            return
        if operation == "item" and status != "Draft":
            raise ValueError("Payment items may only be changed in Draft batches")
        if operation == "match" and status not in _MATCHABLE_BATCH_STATUSES:
            raise ValueError(f"Payment items cannot be matched in {status} batches")
        if operation == "delete" and status != "Draft":
            raise ValueError("Only Draft payment batches may be deleted")

    @staticmethod
    def _validate_reconciliation(connection: sqlite3.Connection, batch: sqlite3.Row) -> None:
        rows = connection.execute(
            f"SELECT match_status, {EFFECTIVE_AMOUNT_SQL} AS effective_amount_cents "
            "FROM MatterportPaymentItems "
            "WHERE payment_batch_id = ?", (batch["payment_batch_id"],)
        ).fetchall()
        if not rows:
            raise ValueError("Cannot reconcile an empty payment batch")
        effective_total = sum(int(row["effective_amount_cents"]) for row in rows)
        if effective_total != int(batch["payment_amount_cents"]):
            raise ValueError("Cannot reconcile: effective total does not equal payment amount")
        statuses = [row["match_status"] for row in rows]
        if "Missing Job" in statuses:
            raise ValueError("Cannot reconcile: batch contains a Missing Job item")
        if "Ambiguous" in statuses:
            raise ValueError("Cannot reconcile: batch contains an Ambiguous item")
        if any(status not in {"Matched", "Excluded"} for status in statuses):
            raise ValueError("Cannot reconcile: every non-excluded item must be Matched")

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
            self._require_batch_mutation(before, "update_batch", set(clean))
            if "batch_status" in clean:
                self.validate_batch_status_transition(before["batch_status"], clean["batch_status"])
                if clean["batch_status"] == "Reconciled":
                    self._validate_reconciliation(connection, before)
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

    def list_payment_batches_with_totals(
        self, batch_status: str | None = None
    ) -> list[dict[str, Any]]:
        """List batches and their reconciliation aggregates in one database query."""
        parameters: tuple[Any, ...] = ()
        where = ""
        if batch_status is not None:
            where = " WHERE b.batch_status = ?"
            parameters = (self.validate_batch_status(batch_status),)
        with self.auth.connection() as connection:
            rows = connection.execute(
                """
                SELECT b.*,
                       COALESCE(SUM(i.amount_received_cents), 0) AS imported_total_cents,
                       COALESCE(SUM(COALESCE(i.resolved_amount_cents,
                                            i.amount_received_cents)), 0)
                         AS effective_total_cents,
                       b.payment_amount_cents
                         - COALESCE(SUM(COALESCE(i.resolved_amount_cents,
                                                i.amount_received_cents)), 0)
                         AS difference_cents,
                       COUNT(i.payment_item_id) AS item_count,
                       COALESCE(SUM(i.match_status = 'Matched'), 0) AS matched_count,
                       COALESCE(SUM(i.match_status = 'Excluded'), 0) AS excluded_count,
                       COALESCE(SUM(i.match_status IN
                         ('Unmatched', 'Missing Job', 'Ambiguous', 'Amount Review')), 0)
                         AS exception_count
                FROM MatterportPaymentBatches b
                LEFT JOIN MatterportPaymentItems i
                  ON i.payment_batch_id = b.payment_batch_id
                """ + where + """
                GROUP BY b.payment_batch_id
                ORDER BY b.payment_date DESC, b.payment_batch_id DESC
                """,
                parameters,
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_payment_batch(self, session: Session, payment_batch_id: int) -> bool:
        self._require_operator(session)
        self._positive_id(payment_batch_id, "payment_batch_id")
        with self.auth.connection() as connection:
            batch = self._require_batch(connection, payment_batch_id)
            self._require_batch_mutation(batch, "delete")
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

    def find_duplicate_documents(self, document_numbers: list[str]) -> set[str]:
        """Return existing document numbers case-insensitively in one query."""
        cleaned = [self._text("document_number", value, required=True)
                   for value in document_numbers]
        if not cleaned:
            return set()
        placeholders = ",".join("?" for _ in cleaned)
        with self.auth.connection() as connection:
            rows = connection.execute(
                f"SELECT document_number FROM MatterportPaymentItems "
                f"WHERE document_number COLLATE NOCASE IN ({placeholders})", cleaned).fetchall()
        return {row["document_number"] for row in rows}

    def import_payment_items(self, session: Session, payment_batch_id: int,
                             items: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate and insert a set of imported items in one atomic transaction."""
        self._require_operator(session)
        self._positive_id(payment_batch_id, "payment_batch_id")
        if not isinstance(items, list) or not items:
            raise ValueError("At least one payment item is required")
        clean_items = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("payment item data must be a dictionary")
            matching_fields = set(item) - _IMPORT_FIELDS
            if matching_fields:
                raise ValueError("Imported payment items cannot set matching fields: "
                                 + ", ".join(sorted(matching_fields)))
            clean_items.append(self._clean_item(item, creating=True))
        folded = [item["document_number"].casefold() for item in clean_items]
        if len(folded) != len(set(folded)):
            raise ValueError("Duplicate document number in submitted payment items")
        ids = []
        with self.auth.connection() as connection:
            batch = self._require_batch(connection, payment_batch_id)
            self._require_batch_mutation(batch, "item")
            for clean in clean_items:
                duplicate = connection.execute(
                    "SELECT payment_item_id FROM MatterportPaymentItems "
                    "WHERE document_number = ? COLLATE NOCASE", (clean["document_number"],)
                ).fetchone()
                if duplicate:
                    raise ValueError("Document number has already been imported")
                fields = list(clean)
                try:
                    cursor = connection.execute(
                        f"INSERT INTO MatterportPaymentItems "
                        f"(payment_batch_id,{','.join(fields)},created_at) "
                        f"VALUES (?,{','.join('?' for _ in fields)},?)",
                        [payment_batch_id, *clean.values(), utc_now_iso()])
                except sqlite3.IntegrityError as exc:
                    raise ValueError("Payment item conflicts with an existing record") from exc
                ids.append(int(cursor.lastrowid))
            total = sum(item["amount_received_cents"] for item in clean_items)
            record_event(connection, "tipalti_payment_items_imported",
                         actor_user_id=session.user_id,
                         details={"payment_batch_id": payment_batch_id,
                                  "imported_count": len(ids), "imported_total_cents": total,
                                  "document_numbers": [i["document_number"] for i in clean_items[:50]]})
        return {"imported_count": len(ids), "imported_total_cents": total,
                "payment_item_ids": ids}

    def add_payment_item(
        self, session: Session, payment_batch_id: int, item_data: dict[str, Any]
    ) -> int:
        self._require_operator(session)
        self._positive_id(payment_batch_id, "payment_batch_id")
        if not isinstance(item_data, dict):
            raise ValueError("payment item data must be a dictionary")
        matching_fields = set(item_data) - _IMPORT_FIELDS
        if matching_fields:
            raise ValueError(
                "Imported payment items cannot set matching fields: "
                + ", ".join(sorted(matching_fields))
            )
        clean = self._clean_item(item_data, creating=True)
        with self.auth.connection() as connection:
            batch = self._require_batch(connection, payment_batch_id)
            self._require_batch_mutation(batch, "item")
            duplicate = connection.execute(
                "SELECT payment_item_id, payment_batch_id FROM MatterportPaymentItems "
                "WHERE document_number = ? COLLATE NOCASE", (clean["document_number"],)
            ).fetchone()
            if duplicate:
                record_event(connection, "payment_document_duplicate_detected",
                             actor_user_id=session.user_id,
                             details={"document_number": clean["document_number"],
                                      "existing_payment_item_id": duplicate["payment_item_id"]})
                # The audit and rejected import share a transaction.  Raising rolls
                # both back rather than committing inside an ordinary operation.
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
            self._require_batch_mutation(batch, "item")
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
            self._require_batch_mutation(batch, "item")
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

    @staticmethod
    def _resolution_notes(notes: Any) -> str | None:
        if notes is None or (isinstance(notes, str) and not notes.strip()):
            return None
        if not isinstance(notes, str):
            raise ValueError("resolution notes must be text")
        notes = notes.strip()
        if len(notes) > 500:
            raise ValueError("resolution notes may not exceed 500 characters")
        return notes

    @staticmethod
    def _audit_resolution(connection: sqlite3.Connection, action: str, session: Session,
                          item: sqlite3.Row, new_status: str, notes: str | None,
                          **details: Any) -> None:
        record_event(connection, action, actor_user_id=session.user_id, details={
            "payment_item_id": item["payment_item_id"],
            "payment_batch_id": item["payment_batch_id"],
            "previous_status": item["match_status"], "new_status": new_status,
            "notes": notes, **details,
        })

    def list_payment_exceptions(self, payment_batch_id: int) -> dict[str, list[dict[str, Any]]]:
        """Return unresolved and excluded items grouped for the resolution notebook."""
        self._positive_id(payment_batch_id, "payment_batch_id")
        with self.auth.connection() as connection:
            self._require_batch(connection, payment_batch_id)
            rows = [dict(row) for row in connection.execute(
                "SELECT i.*, COALESCE(u.display_name, u.username) AS amount_resolved_by_name "
                "FROM MatterportPaymentItems i LEFT JOIN Users u ON u.id=i.amount_resolved_by "
                "WHERE i.payment_batch_id = ? "
                "AND match_status IN ('Unmatched','Missing Job','Ambiguous','Amount Review','Excluded') "
                "ORDER BY payment_item_id", (payment_batch_id,))]
        return {group: [row for row in rows if row["match_status"] in statuses]
                for group, statuses in EXCEPTION_GROUPS.items()
                if any(row["match_status"] in statuses for row in rows)}

    def get_exception_summary(self, payment_batch_id: int) -> dict[str, int]:
        groups = self.list_payment_exceptions(payment_batch_id)
        return {name: len(groups.get(name, ())) for name in EXCEPTION_GROUPS}

    def list_exception_candidates(self, payment_item_id: int) -> list[dict[str, Any]]:
        """Suggest jobs without selecting one; operators always make the final choice."""
        self._positive_id(payment_item_id, "payment_item_id")
        with self.auth.connection() as connection:
            item = self._require_item(connection, payment_item_id)
            needle = item["document_number"] or ""
            rows = connection.execute(
                """SELECT j.job_id, j.external_job_id AS job_number,
                          j.client_name_source AS customer,
                          COALESCE(j.capture_address_raw, j.address_1) AS property_address,
                          COALESCE(j.completed_at, j.actual_start_at, j.scheduled_start_at) AS capture_date,
                          t.first_name, t.last_name
                   FROM Jobs j
                   LEFT JOIN JobAssignments a ON a.job_id = j.job_id
                     AND a.assignment_role = 'Primary' AND a.unassigned_at IS NULL
                     AND a.assignment_status = 'Assigned'
                   LEFT JOIN Techs t ON t.tech_id = a.tech_id AND t.status = 'Active'
                   WHERE j.external_job_id LIKE ? COLLATE NOCASE
                      OR COALESCE(j.project_name_source, '') LIKE ? COLLATE NOCASE
                   ORDER BY CASE WHEN j.external_job_id = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                            j.job_id DESC LIMIT 25""",
                (f"%{needle}%", f"%{needle}%", needle)).fetchall()
        result = []
        for row in rows:
            candidate = dict(row)
            candidate["technician"] = " ".join(filter(None, (candidate.pop("first_name"),
                                                                 candidate.pop("last_name"))))
            candidate["confidence"] = (100 if str(candidate["job_number"]).casefold() == needle.casefold()
                                       else 60)
            result.append(candidate)
        return result

    def search_jobs_for_payment_exception(self, search_text: str,
                                          limit: int = 50) -> list[dict[str, Any]]:
        """Search all operator-review fields without changing a payment item."""
        needle = self._text("job search", search_text, required=True)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        limit = min(limit, 50)
        pattern = f"%{needle}%"
        with self.auth.connection() as connection:
            rows = connection.execute(
                """SELECT j.job_id, j.external_job_id AS job_number,
                          COALESCE(p.client_name, j.client_name_source) AS customer,
                          COALESCE(p.project_name, j.project_name_source) AS project_name,
                          COALESCE(j.capture_address_raw, j.address_1) AS property_address,
                          COALESCE(j.completed_at, j.actual_start_at,
                                   j.scheduled_start_at) AS capture_date,
                          j.job_status, t.first_name, t.last_name
                   FROM Jobs j
                   LEFT JOIN Projects p ON p.project_id = j.project_id
                   LEFT JOIN JobAssignments a ON a.job_id = j.job_id
                     AND a.assignment_role = 'Primary' AND a.unassigned_at IS NULL
                     AND a.assignment_status = 'Assigned'
                   LEFT JOIN Techs t ON t.tech_id = a.tech_id AND t.status = 'Active'
                   WHERE j.external_job_id LIKE ? COLLATE NOCASE
                      OR COALESCE(j.project_name_source, '') LIKE ? COLLATE NOCASE
                      OR COALESCE(p.project_name, '') LIKE ? COLLATE NOCASE
                      OR COALESCE(j.client_name_source, '') LIKE ? COLLATE NOCASE
                      OR COALESCE(p.client_name, '') LIKE ? COLLATE NOCASE
                      OR COALESCE(j.capture_address_raw, '') LIKE ? COLLATE NOCASE
                      OR COALESCE(j.address_1, '') LIKE ? COLLATE NOCASE
                   ORDER BY CASE WHEN j.external_job_id = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                            j.job_id DESC LIMIT ?""",
                (*([pattern] * 7), needle, limit),
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["technician"] = " ".join(filter(None, (
                result.pop("first_name"), result.pop("last_name"))))
            results.append(result)
        return results

    def assign_payment_item_job(self, session: Session, payment_item_id: int, job_id: int,
                                notes: str | None = None) -> dict[str, Any]:
        self._require_operator(session)
        self._positive_id(payment_item_id, "payment_item_id")
        self._positive_id(job_id, "job_id")
        notes = self._resolution_notes(notes)
        with self.auth.connection() as connection:
            item = self._require_item(connection, payment_item_id)
            batch = self._require_batch(connection, item["payment_batch_id"])
            self._require_batch_mutation(batch, "match")
            self._require_job(connection, job_id)
            connection.execute(
                "UPDATE MatterportPaymentItems SET job_id=?, match_status='Matched', "
                "match_method='Manual exception resolution', match_notes=?, updated_at=? "
                "WHERE payment_item_id=?", (job_id, notes, utc_now_iso(), payment_item_id))
            self._audit_resolution(connection, "payment_item_job_assigned", session, item,
                                   "Matched", notes, job_id=job_id)
            return dict(self._require_item(connection, payment_item_id))

    def exclude_payment_item(self, session: Session, payment_item_id: int,
                             notes: str | None = None, reason: str = "Operator decision") -> dict[str, Any]:
        self._require_operator(session)
        self._positive_id(payment_item_id, "payment_item_id")
        notes = self._resolution_notes(notes)
        reason = self._text("match_method", reason, required=True)
        with self.auth.connection() as connection:
            item = self._require_item(connection, payment_item_id)
            batch = self._require_batch(connection, item["payment_batch_id"])
            self._require_batch_mutation(batch, "match")
            connection.execute(
                "UPDATE MatterportPaymentItems SET match_status='Excluded', match_method=?, "
                "match_notes=?, updated_at=? WHERE payment_item_id=?",
                (f"Excluded: {reason}", notes, utc_now_iso(), payment_item_id))
            self._audit_resolution(connection, "payment_item_excluded", session, item,
                                   "Excluded", notes, reason=reason)
            return dict(self._require_item(connection, payment_item_id))

    def update_payment_item_resolution_notes(self, session: Session, payment_item_id: int,
                                             notes: str | None) -> dict[str, Any]:
        """Edit an exclusion explanation without manufacturing another transition."""
        self._require_operator(session)
        self._positive_id(payment_item_id, "payment_item_id")
        notes = self._resolution_notes(notes)
        with self.auth.connection() as connection:
            item = self._require_item(connection, payment_item_id)
            if item["match_status"] != "Excluded":
                raise ValueError("Resolution notes may only be edited for an excluded item")
            batch = self._require_batch(connection, item["payment_batch_id"])
            self._require_batch_mutation(batch, "match")
            connection.execute(
                "UPDATE MatterportPaymentItems SET match_notes=?, updated_at=? "
                "WHERE payment_item_id=?", (notes, utc_now_iso(), payment_item_id))
            self._audit_resolution(
                connection, "payment_item_resolution_notes_updated", session, item,
                item["match_status"], notes, previous_notes=item["match_notes"],
                new_notes=notes)
            return dict(self._require_item(connection, payment_item_id))

    def restore_payment_item(self, session: Session, payment_item_id: int,
                             notes: str | None = None) -> dict[str, Any]:
        self._require_operator(session)
        self._positive_id(payment_item_id, "payment_item_id")
        notes = self._resolution_notes(notes)
        with self.auth.connection() as connection:
            item = self._require_item(connection, payment_item_id)
            if item["match_status"] != "Excluded":
                raise ValueError("Only excluded payment items may be restored")
            batch = self._require_batch(connection, item["payment_batch_id"])
            self._require_batch_mutation(batch, "match")
            status = "Matched" if item["job_id"] else "Missing Job"
            connection.execute(
                "UPDATE MatterportPaymentItems SET match_status=?, match_method='Restored', "
                "match_notes=?, updated_at=? WHERE payment_item_id=?",
                (status, notes, utc_now_iso(), payment_item_id))
            self._audit_resolution(connection, "payment_item_restored", session, item,
                                   status, notes)
            return dict(self._require_item(connection, payment_item_id))

    def accept_amount_difference(self, session: Session, payment_item_id: int, decision: str,
                                 notes: str | None = None,
                                 job_amount_cents: int | None = None) -> dict[str, Any]:
        """Resolve an amount review using an explicit operator decision."""
        self._require_operator(session)
        self._positive_id(payment_item_id, "payment_item_id")
        if decision not in {"imported", "job"}:
            raise ValueError("decision must be 'imported' or 'job'")
        notes = self._resolution_notes(notes)
        if decision == "job":
            job_amount_cents = self._cents(job_amount_cents, "job_amount_cents")
        with self.auth.connection() as connection:
            item = self._require_item(connection, payment_item_id)
            batch = self._require_batch(connection, item["payment_batch_id"])
            self._require_batch_mutation(batch, "match")
            if item["match_status"] != "Amount Review":
                raise ValueError("Payment item is not awaiting amount review")
            status = "Matched" if item["job_id"] else "Missing Job"
            amount = item["amount_received_cents"] if decision == "imported" else job_amount_cents
            expected = item["expected_job_amount_cents"]
            if decision == "job":
                expected = job_amount_cents
            resolved_at = utc_now_iso()
            connection.execute(
                "UPDATE MatterportPaymentItems SET expected_job_amount_cents=?, "
                "resolved_amount_cents=?, amount_resolution=?, amount_resolution_notes=?, "
                "amount_resolved_at=?, amount_resolved_by=?, match_status=?, match_method=?, "
                "match_notes=?, updated_at=? WHERE payment_item_id=?",
                (expected, amount, decision.title(), notes, resolved_at, session.user_id,
                 status, f"Accepted {decision} amount", notes, resolved_at, payment_item_id))
            self._audit_resolution(connection, "payment_amount_override", session, item,
                                   status, notes, decision=decision,
                                   original_amount_cents=item["amount_received_cents"],
                                   expected_amount_cents=expected,
                                   resolved_amount_cents=amount)
            return dict(self._require_item(connection, payment_item_id))

    def match_payment_items(self, session: Session, payment_batch_id: int) -> dict[str, int]:
        """Match every item in a batch to ``Jobs.external_job_id`` atomically."""
        self._require_operator(session)
        self._positive_id(payment_batch_id, "payment_batch_id")
        matched = missing = ambiguous = 0
        with self.auth.connection() as connection:
            batch = self._require_batch(connection, payment_batch_id)
            self._require_batch_mutation(batch, "match")
            items = connection.execute(
                "SELECT payment_item_id, document_number FROM MatterportPaymentItems "
                "WHERE payment_batch_id = ? ORDER BY payment_item_id", (payment_batch_id,)
            ).fetchall()
            for item in items:
                jobs = connection.execute(
                    "SELECT job_id FROM Jobs WHERE external_job_id = ? COLLATE NOCASE",
                    (item["document_number"],),
                ).fetchall()
                if len(jobs) == 1:
                    job = jobs[0]
                    connection.execute(
                        "UPDATE MatterportPaymentItems SET job_id = ?, match_status = 'Matched', "
                        "match_method = 'External Job ID', match_notes = NULL, updated_at = ? "
                        "WHERE payment_item_id = ?",
                        (job["job_id"], utc_now_iso(), item["payment_item_id"]),
                    )
                    matched += 1
                    action = "payment_item_matched"
                elif not jobs:
                    job = None
                    connection.execute(
                        "UPDATE MatterportPaymentItems SET job_id = NULL, "
                        "match_status = 'Missing Job', match_method = NULL, match_notes = NULL, "
                        "updated_at = ? "
                        "WHERE payment_item_id = ?", (utc_now_iso(), item["payment_item_id"])
                    )
                    missing += 1
                    action = "payment_item_unmatched"
                else:
                    job = None
                    connection.execute(
                        "UPDATE MatterportPaymentItems SET job_id = NULL, "
                        "match_status = 'Ambiguous', match_method = 'External Job ID', "
                        "match_notes = ?, updated_at = ? WHERE payment_item_id = ?",
                        ("Multiple Jobs share this document number", utc_now_iso(),
                         item["payment_item_id"]),
                    )
                    ambiguous += 1
                    action = "payment_item_match_ambiguous"
                record_event(connection, action, actor_user_id=session.user_id,
                             details={"payment_item_id": item["payment_item_id"],
                                      "document_number": item["document_number"],
                                      "job_id": int(job["job_id"]) if job else None})
        return {"matched_count": matched, "missing_job_count": missing,
                "ambiguous_count": ambiguous, "unmatched_count": missing + ambiguous}

    def get_primary_technician_result(self, job_id: int) -> dict[str, Any]:
        """Return whether a job has zero, one, or multiple active primary assignees."""
        self._positive_id(job_id, "job_id")
        with self.auth.connection() as connection:
            self._require_job(connection, job_id)
            rows = connection.execute(
                """
                SELECT t.tech_id, t.first_name, t.last_name
                FROM JobAssignments a
                JOIN Techs t ON t.tech_id = a.tech_id
                WHERE a.job_id = ? AND a.assignment_role = 'Primary'
                  AND a.unassigned_at IS NULL AND a.assignment_status = 'Assigned'
                  AND t.status = 'Active'
                ORDER BY a.job_assignment_id
                """, (job_id,)
            ).fetchall()
        count = len(rows)
        return {
            "status": "Found" if count == 1 else ("Missing" if count == 0 else "Ambiguous"),
            "technician": dict(rows[0]) if count == 1 else None,
            "candidate_count": count,
        }

    def get_primary_technician(self, job_id: int) -> dict[str, Any] | None:
        """Compatibility wrapper returning a technician only for a unique match."""
        return self.get_primary_technician_result(job_id)["technician"]

    def calculate_batch_totals(self, payment_batch_id: int) -> dict[str, int]:
        self._positive_id(payment_batch_id, "payment_batch_id")
        with self.auth.connection() as connection:
            batch = self._require_batch(connection, payment_batch_id)
            totals = connection.execute(
                """
                SELECT COALESCE(SUM(amount_received_cents), 0) AS imported_total_cents,
                       COALESCE(SUM(COALESCE(resolved_amount_cents, amount_received_cents)), 0)
                           AS effective_total_cents,
                       COALESCE(SUM(CASE WHEN match_status = 'Matched'
                                        THEN COALESCE(resolved_amount_cents, amount_received_cents) ELSE 0 END), 0)
                           AS matched_total_cents,
                       COALESCE(SUM(CASE WHEN match_status IN
                                             ('Unmatched', 'Missing Job', 'Ambiguous',
                                              'Amount Review')
                                        THEN COALESCE(resolved_amount_cents, amount_received_cents) ELSE 0 END), 0)
                           AS unmatched_total_cents,
                       COALESCE(SUM(CASE WHEN match_status = 'Matched' THEN 1 ELSE 0 END), 0)
                           AS matched_count,
                       COALESCE(SUM(CASE WHEN match_status = 'Unmatched' THEN 1 ELSE 0 END), 0)
                           AS unmatched_status_count,
                       COALESCE(SUM(CASE WHEN match_status = 'Missing Job' THEN 1 ELSE 0 END), 0)
                           AS missing_job_count,
                       COALESCE(SUM(CASE WHEN match_status = 'Ambiguous' THEN 1 ELSE 0 END), 0)
                           AS ambiguous_count,
                       COALESCE(SUM(CASE WHEN match_status = 'Amount Review' THEN 1 ELSE 0 END), 0)
                           AS amount_review_count,
                       COALESCE(SUM(CASE WHEN match_status = 'Excluded' THEN 1 ELSE 0 END), 0)
                           AS excluded_count,
                       COALESCE(SUM(CASE WHEN match_status = 'Excluded'
                                        THEN COALESCE(resolved_amount_cents, amount_received_cents) ELSE 0 END), 0)
                           AS excluded_total_cents,
                       COUNT(*) AS item_count
                FROM MatterportPaymentItems WHERE payment_batch_id = ?
                """, (payment_batch_id,)
            ).fetchone()
            imported = int(totals["imported_total_cents"])
            effective = int(totals["effective_total_cents"])
            payment = int(batch["payment_amount_cents"])
            matched_count = int(totals["matched_count"])
            excluded_count = int(totals["excluded_count"])
            exception_count = sum(int(totals[field]) for field in (
                "unmatched_status_count", "missing_job_count", "ambiguous_count",
                "amount_review_count",
            ))
            return {
                "payment_amount_cents": payment,
                "imported_total_cents": imported,
                "difference_cents": payment - effective,
                "matched_total_cents": int(totals["matched_total_cents"]),
                "unmatched_total_cents": int(totals["unmatched_total_cents"]),
                "matched_count": matched_count,
                "unmatched_count": exception_count,
                "missing_job_count": int(totals["missing_job_count"]),
                "ambiguous_count": int(totals["ambiguous_count"]),
                "amount_review_count": int(totals["amount_review_count"]),
                "excluded_count": excluded_count,
                "excluded_total_cents": int(totals["excluded_total_cents"]),
                "item_count": int(totals["item_count"]),
                "resolved_count": matched_count + excluded_count,
                "exception_count": exception_count,
            }
