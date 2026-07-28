"""Preview and import OpenTable CSV exports into Matterport Ops Jobs."""

from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError


_REQUIRED_COLUMNS = frozenset({
    "Record Number", "Request Date/Time", "MP Client.", "Job ID", "Project Name",
    "Job Status", "Job Scheduled Date/Time", "Capture Address", "Floor/Unit/Suite",
    "Capture Size - Requested", "CT Travel Payout", "CT Off Hours Payout", "CT Rate",
    "AP Invoice Number", "CT Name", "On-Site Contact Name", "On-Site Contact Email",
    "On-Site Contact Number",
})

_STATUS_MAP = {
    "complete": "Completed",
    "completed": "Completed",
    "scheduled": "Scheduled",
    "cancelled": "Cancelled",
    "canceled": "Cancelled",
    "cancelled last minute": "Cancelled",
    "canceled last minute": "Cancelled",
    "model not found": "On Hold",
}


class OpenTableImportService:
    """Parse, preview, and transactionally import an OpenTable CSV export."""

    def __init__(self, auth: AuthService):
        self.auth = auth

    @staticmethod
    def _require_operator(session: Session | None) -> None:
        if session is None or session.role not in {"admin", "operator"}:
            raise AuthorizationError("Administrator or operator role required")

    @staticmethod
    def _text(value: Any) -> str | None:
        value = str(value or "").strip()
        return value or None

    @staticmethod
    def _money(value: Any) -> float:
        text = str(value or "").strip().replace("$", "").replace(",", "")
        if not text:
            return 0.0
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid currency value: {value}") from exc
        if not number.is_finite():
            raise ValueError(f"Invalid currency value: {value}")
        return float(number)

    @staticmethod
    def _number(value: Any) -> float | None:
        text = str(value or "").strip().replace(",", "")
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid numeric value: {value}") from exc
        if not number.is_finite() or number < 0:
            raise ValueError(f"Invalid numeric value: {value}")
        return float(number)

    @staticmethod
    def _timestamp(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None

        normalized = re.sub(r"\s+", " ", text).strip()
        formats = (
            "%m/%d/%Y %I:%M:%S %p",
            "%m/%d/%Y %I:%M %p",
            "%m/%d/%Y %I:%M%p",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y",
            "%m/%d/%y %I:%M:%S %p",
            "%m/%d/%y %I:%M %p",
            "%m/%d/%y %I:%M%p",
            "%m/%d/%y %H:%M:%S",
            "%m/%d/%y %H:%M",
            "%m/%d/%y",
            "%m-%d-%Y %I:%M:%S %p",
            "%m-%d-%Y %I:%M %p",
            "%m-%d-%Y %I:%M%p",
            "%m-%d-%Y %H:%M:%S",
            "%m-%d-%Y %H:%M",
            "%m-%d-%Y",
            "%m-%d-%y %I:%M:%S %p",
            "%m-%d-%y %I:%M %p",
            "%m-%d-%y %I:%M%p",
            "%m-%d-%y %H:%M:%S",
            "%m-%d-%y %H:%M",
            "%m-%d-%y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        )
        for fmt in formats:
            try:
                return datetime.strptime(normalized, fmt).isoformat(timespec="minutes")
            except ValueError:
                continue
        raise ValueError(f"Unsupported date/time value: {text}")

    @staticmethod
    def _status(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "Requested"
        return _STATUS_MAP.get(text.casefold(), text)

    @staticmethod
    def _source_row_json(row: dict[str, str]) -> str:
        """Serialize only original source columns, excluding importer metadata."""
        source = {key: value for key, value in row.items() if not key.startswith("__")}
        return json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _parse_address(raw: str | None) -> dict[str, str | None]:
        """Best-effort split while always preserving the original address string."""
        if not raw:
            return {
                "address_1": None,
                "address_2": None,
                "city": None,
                "state": None,
                "postal_code": None,
                "county": None,
                "country": None,
            }
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        result = {
            "address_1": parts[0] if parts else raw,
            "address_2": None,
            "city": None,
            "state": None,
            "postal_code": None,
            "county": None,
            "country": None,
        }
        if len(parts) >= 2:
            result["city"] = parts[1]
        for part in parts[2:]:
            if re.fullmatch(r"[A-Za-z]{2}", part):
                result["state"] = part.upper()
            elif re.fullmatch(r"\d{5}(?:-\d{4})?", part):
                result["postal_code"] = part
            elif part.casefold() in {"usa", "united states", "united states of america"}:
                result["country"] = part
            elif "county" in part.casefold():
                result["county"] = part
        return result

    @staticmethod
    def _is_parent(row: dict[str, str]) -> bool:
        return str(row.get("Floor/Unit/Suite") or "").strip().casefold() == "parent record"

    @classmethod
    def _choose_job_row(cls, rows: list[dict[str, str]]) -> dict[str, str]:
        parent = next((row for row in rows if cls._is_parent(row)), None)
        return parent or rows[0]

    @classmethod
    def _capture_size(cls, rows: list[dict[str, str]]) -> float | None:
        parent = next((row for row in rows if cls._is_parent(row)), None)
        if parent:
            return cls._number(parent.get("Capture Size - Requested"))
        values = [cls._number(row.get("Capture Size - Requested")) for row in rows]
        values = [value for value in values if value is not None]
        return max(values) if values else None

    @classmethod
    def _spaces_note(cls, rows: list[dict[str, str]]) -> str | None:
        spaces = []
        for row in rows:
            value = cls._text(row.get("Floor/Unit/Suite"))
            if value and value.casefold() != "parent record" and value not in spaces:
                spaces.append(value)
        return "Capture spaces: " + "; ".join(spaces) if spaces else None

    @classmethod
    def _build_job(cls, external_job_id: str, rows: list[dict[str, str]]) -> dict[str, Any]:
        chosen = cls._choose_job_row(rows)
        address_raw = cls._text(chosen.get("Capture Address"))
        address = cls._parse_address(address_raw)
        notes = [cls._text(chosen.get("Additional Details")), cls._spaces_note(rows)]
        cancellation_reason = None
        source_status = cls._text(chosen.get("Job Status"))
        if source_status and cls._status(source_status) == "Cancelled":
            cancellation_reason = source_status
        return {
            "external_job_id": external_job_id,
            "project_name_source": cls._text(chosen.get("Project Name")),
            "client_name_source": cls._text(chosen.get("MP Client.")),
            "job_status": cls._status(chosen.get("Job Status")),
            "request_received_at": cls._timestamp(chosen.get("Request Date/Time")),
            "scheduled_start_at": cls._timestamp(chosen.get("Job Scheduled Date/Time")),
            "capture_address_raw": address_raw,
            **address,
            "requested_capture_size": cls._capture_size(rows),
            "additional_details": "\n\n".join(note for note in notes if note) or None,
            "scheduling_link": cls._text(chosen.get("Scheduling Link")),
            "floor_plan_attachments": cls._text(chosen.get("Floor Plans/Attachments")),
            "onsite_contact_name": cls._text(chosen.get("On-Site Contact Name")),
            "onsite_contact_email": cls._text(chosen.get("On-Site Contact Email")),
            "onsite_contact_phone": cls._text(chosen.get("On-Site Contact Number")),
            "preferred_datetime_1": cls._timestamp(chosen.get("Preferred Date/Time 1")),
            "preferred_datetime_2": cls._timestamp(chosen.get("Preferred Date/Time 2")),
            "alternate_datetime_1": cls._timestamp(chosen.get("Alternative Date/Time")),
            "alternate_datetime_2": cls._timestamp(chosen.get("Alternative Date/Time 2")),
            "alternate_datetime_3": cls._timestamp(chosen.get("Alternative Date/Time 3")),
            "cancellation_reason": cancellation_reason,
        }

    @classmethod
    def read_csv(cls, file_path: str) -> list[dict[str, Any]]:
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("CSV file path is required")
        with open(file_path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or ())
            missing = sorted(_REQUIRED_COLUMNS - columns)
            if missing:
                raise ValueError("OpenTable export is missing columns: " + ", ".join(missing))
            grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
            for source_row_number, row in enumerate(reader, start=2):
                external_job_id = cls._text(row.get("Job ID"))
                record_number = cls._text(row.get("Record Number"))
                if not external_job_id:
                    raise ValueError(f"Row {source_row_number} has no Job ID")
                if not record_number:
                    raise ValueError(f"Row {source_row_number} has no Record Number")
                row["__source_row_number"] = str(source_row_number)
                grouped[external_job_id].append(row)

        results = []
        for external_job_id, rows in grouped.items():
            results.append({
                "external_job_id": external_job_id,
                "job": cls._build_job(external_job_id, rows),
                "source_rows": rows,
                "source_row_count": len(rows),
                "parent_record_count": sum(cls._is_parent(row) for row in rows),
            })
        return results

    def preview(self, file_path: str) -> dict[str, Any]:
        groups = self.read_csv(file_path)
        with self.auth.connection() as connection:
            existing_jobs = {
                row["external_job_id"].casefold(): int(row["job_id"])
                for row in connection.execute("SELECT job_id, external_job_id FROM Jobs")
            }
            existing_records = {
                row["external_record_number"]: row["source_row_json"]
                for row in connection.execute(
                    "SELECT external_record_number, source_row_json FROM JobSourceRecords "
                    "WHERE source_system = 'OpenTable'"
                )
            }

        items = []
        counts = defaultdict(int)
        for group in groups:
            job_key = group["external_job_id"].casefold()
            imported = 0
            changed = 0
            for row in group["source_rows"]:
                record_number = self._text(row.get("Record Number"))
                if record_number not in existing_records:
                    continue
                imported += 1
                if existing_records[record_number] != self._source_row_json(row):
                    changed += 1

            if imported == group["source_row_count"] and changed == 0:
                action = "Skipped"
            elif job_key in existing_jobs:
                action = "Updated"
            else:
                action = "Created"
            counts[action.lower()] += 1
            items.append({
                "action": action,
                "existing_job_id": existing_jobs.get(job_key),
                "external_job_id": group["external_job_id"],
                "client_name": group["job"].get("client_name_source"),
                "project_name": group["job"].get("project_name_source"),
                "job_status": group["job"].get("job_status"),
                "scheduled_start_at": group["job"].get("scheduled_start_at"),
                "capture_address": group["job"].get("capture_address_raw"),
                "source_row_count": group["source_row_count"],
                "already_imported_rows": imported,
                "changed_source_rows": changed,
                "parent_record_count": group["parent_record_count"],
            })
        return {
            "file_name": os.path.basename(file_path),
            "groups": groups,
            "items": items,
            "counts": dict(counts),
        }

    def import_csv(self, session: Session, file_path: str) -> dict[str, Any]:
        self._require_operator(session)
        preview = self.preview(file_path)
        now = utc_now_iso()
        result = {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "source_rows_added": 0,
            "source_rows_updated": 0,
            "job_ids": [],
        }
        with self.auth.connection() as connection:
            for group in preview["groups"]:
                external_job_id = group["external_job_id"]
                existing = connection.execute(
                    "SELECT job_id FROM Jobs WHERE external_job_id = ? COLLATE NOCASE",
                    (external_job_id,),
                ).fetchone()
                job_data = group["job"]
                if existing is None:
                    fields = [field for field, value in job_data.items() if value is not None]
                    cursor = connection.execute(
                        f"INSERT INTO Jobs ({','.join(fields)}, created_at, created_by) "
                        f"VALUES ({','.join('?' for _ in fields)}, ?, ?)",
                        [job_data[field] for field in fields] + [now, session.user_id],
                    )
                    job_id = int(cursor.lastrowid)
                    result["created"] += 1
                else:
                    job_id = int(existing["job_id"])
                    changes = {
                        field: value
                        for field, value in job_data.items()
                        if field != "external_job_id" and value is not None
                    }
                    assignments = ",".join(f"{field} = ?" for field in changes)
                    if assignments:
                        connection.execute(
                            f"UPDATE Jobs SET {assignments}, updated_at = ?, updated_by = ? "
                            "WHERE job_id = ?",
                            [*changes.values(), now, session.user_id, job_id],
                        )
                    result["updated"] += 1

                changed_for_job = 0
                for row in group["source_rows"]:
                    record_number = self._text(row.get("Record Number"))
                    source_json = self._source_row_json(row)
                    source_record = connection.execute(
                        "SELECT job_source_record_id, source_row_json "
                        "FROM JobSourceRecords WHERE source_system = 'OpenTable' "
                        "AND external_record_number = ?",
                        (record_number,),
                    ).fetchone()
                    values = (
                        job_id,
                        self._text(row.get("Floor/Unit/Suite")),
                        int(self._is_parent(row)),
                        self._number(row.get("Capture Size - Requested")),
                        self._money(row.get("CT Rate")),
                        self._money(row.get("CT Travel Payout")),
                        self._money(row.get("CT Off Hours Payout")),
                        self._text(row.get("AP Invoice Number")),
                        source_json,
                        now,
                        os.path.basename(file_path),
                        int(row["__source_row_number"]),
                    )
                    if source_record is None:
                        connection.execute(
                            """
                            INSERT INTO JobSourceRecords (
                                job_id, source_system, external_record_number,
                                record_description, is_parent_record, requested_capture_size,
                                ct_rate, ct_travel_payout, ct_off_hours_payout,
                                ap_invoice_number, source_row_json, imported_at,
                                source_file_name, source_row_number, created_at
                            ) VALUES (?, 'OpenTable', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (job_id, record_number, *values[1:], now),
                        )
                        result["source_rows_added"] += 1
                        changed_for_job += 1
                    elif source_record["source_row_json"] != source_json:
                        connection.execute(
                            """
                            UPDATE JobSourceRecords SET
                                job_id = ?, record_description = ?, is_parent_record = ?,
                                requested_capture_size = ?, ct_rate = ?, ct_travel_payout = ?,
                                ct_off_hours_payout = ?, ap_invoice_number = ?,
                                source_row_json = ?, imported_at = ?, source_file_name = ?,
                                source_row_number = ?
                            WHERE job_source_record_id = ?
                            """,
                            (*values, int(source_record["job_source_record_id"])),
                        )
                        result["source_rows_updated"] += 1
                        changed_for_job += 1

                if changed_for_job == 0 and existing is not None:
                    result["skipped"] += 1
                    result["updated"] -= 1
                result["job_ids"].append(job_id)

            record_event(
                connection,
                "opentable_csv_imported",
                actor_user_id=session.user_id,
                details={
                    "file_name": os.path.basename(file_path),
                    "created": result["created"],
                    "updated": result["updated"],
                    "skipped": result["skipped"],
                    "source_rows_added": result["source_rows_added"],
                    "source_rows_updated": result["source_rows_updated"],
                },
            )
        return result
