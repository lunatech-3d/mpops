"""Preview and import OpenTable CSV exports into Matterport Ops Jobs."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.address_utils import split_us_postal_code_suffix
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

_US_STATE_ABBREVIATIONS = frozenset({
    "AK", "AL", "AR", "AZ", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI",
    "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN",
    "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA",
    "WI", "WV", "WY",
})
_US_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
_COUNTRY_NAMES = frozenset({
    "us", "usa", "united states", "united states of america",
})
_LEGACY_ZIP_SUFFIX = re.compile(r"(?:^|\s)(\d{5}(?:-\d{4})?)$")
_STATE_SUFFIX = re.compile(
    r"(?:^|\s)(" + "|".join(re.escape(name) for name in sorted(
        _US_STATE_NAMES, key=len, reverse=True
    )) + r"|[A-Za-z]{2})$", re.IGNORECASE,
)
_COUNTY_SEGMENT = re.compile(r"^[A-Za-z][A-Za-z .'-]* County$", re.IGNORECASE)
_STREET_PREFIX = re.compile(r"^\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?\s+\S")
_ADDRESS_FIELDS = frozenset({
    "address_1", "address_2", "city", "state", "postal_code", "county", "country",
})
_ADDRESS_REVIEW_FIELDS = ("address_1", "address_2", "city", "state", "postal_code", "county", "country")


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
    def _address_parts(raw: str, *, split_whitespace_columns: bool) -> list[str]:
        """Return address segments without erasing column-like whitespace.

        Some Airtable exports contain an address laid out with tabs or runs of
        spaces instead of commas. Only treat that whitespace as a delimiter when
        the entire address has no commas; within a comma-delimited address it may
        be intentional spacing in a facility or street name.
        """
        if split_whitespace_columns and "," not in raw:
            parts = re.split(r"(?:\t+| {2,}|\r?\n+)", raw)
        else:
            parts = raw.split(",")
        normalized = [re.sub(r"\s+", " ", part).strip() for part in parts]
        return [part for part in normalized if part]

    @classmethod
    def _parse_address_version(
        cls, raw: str | None, *, split_whitespace_columns: bool,
    ) -> dict[str, str | None]:
        """Conservatively parse a US address using its right-hand suffix."""
        result = {
            "address_1": None,
            "address_2": None,
            "city": None,
            "state": None,
            "postal_code": None,
            "county": None,
            "country": None,
        }
        if not raw or not str(raw).strip():
            return result

        parts = cls._address_parts(
            str(raw).strip(), split_whitespace_columns=split_whitespace_columns
        )
        if not parts:
            return result

        # Airtable addresses use both "MI, 48170, USA" and
        # "MI, Wayne County, USA, 48170". Peel off the trailing metadata in
        # either order before looking for the state and city.
        while parts:
            last = parts[-1]
            if last.casefold() in _COUNTRY_NAMES:
                result["country"] = "USA"
                parts.pop()
                continue

            if split_whitespace_columns:
                postal_code, remainder = split_us_postal_code_suffix(last)
            else:
                zip_match = _LEGACY_ZIP_SUFFIX.search(last)
                postal_code = zip_match.group(1) if zip_match else None
                remainder = last[:zip_match.start()].strip() if zip_match else last
            if result["postal_code"] is None and postal_code:
                result["postal_code"] = postal_code
                parts[-1] = remainder
                if not parts[-1]:
                    parts.pop()
            elif (result["county"] is None and len(parts) >= 3
                  and _COUNTY_SEGMENT.fullmatch(last)):
                result["county"] = parts.pop()
            else:
                break

        if parts:
            state_match = _STATE_SUFFIX.search(parts[-1])
            state_token = state_match.group(1) if state_match else None
            state_code = (_US_STATE_NAMES.get(state_token.casefold())
                          or state_token.upper()) if state_token else None
            if state_code in _US_STATE_ABBREVIATIONS:
                result["state"] = state_code
                parts[-1] = parts[-1][:state_match.start()].strip()
                if not parts[-1]:
                    parts.pop()

        # A locality is only safe to infer when it immediately precedes a recognized
        # state suffix. In particular, never use a numeric street as a positional city.
        if (result["state"] and parts and not _STREET_PREFIX.match(parts[-1])
                and not _COUNTY_SEGMENT.fullmatch(parts[-1])):
            result["city"] = parts.pop()

        # Work back from the locality so a business/facility prefix is ignored. Suite
        # and unit text remains in the selected component without further splitting.
        street = next((part for part in reversed(parts) if _STREET_PREFIX.match(part)), None)
        if street:
            result["address_1"] = street
        return result

    @classmethod
    def _parse_address(cls, raw: str | None) -> dict[str, str | None]:
        """Parse comma-delimited and column-spaced US source addresses."""
        return cls._parse_address_version(raw, split_whitespace_columns=True)

    @classmethod
    def parse_address_for_review(
        cls, raw: str | None,
    ) -> tuple[dict[str, str | None], list[str]]:
        """Return a non-writing address parse and any confidence warnings."""
        parsed = cls._parse_address(raw)
        warnings = cls._address_warnings({"capture_address_raw": raw, **parsed})
        return parsed, warnings

    @classmethod
    def _legacy_parse_address(cls, raw: str | None) -> dict[str, str | None]:
        """Reproduce the prior parser so its stored artifacts can be repaired."""
        return cls._parse_address_version(raw, split_whitespace_columns=False)

    @staticmethod
    def _address_warnings(job_data: dict[str, Any]) -> list[str]:
        if not job_data.get("capture_address_raw"):
            return []
        missing = [name for name in ("address_1", "city", "state") if not job_data.get(name)]
        warnings = (["Could not confidently parse " + ", ".join(missing)] if missing else [])
        if not job_data.get("postal_code"):
            warnings.append("No ZIP found in the source address")
        return warnings

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
    def _job_changes_with_review(
        cls, job_data: dict[str, Any], existing: Any,
        protected_fields: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        """Update certain imported values; hold uncertain address changes for review.

        A complete street/city/state parse may fill blanks, even on an unchanged
        source row. Existing nonblank components may be unaudited local fixes, so
        conflicting imported values are held for an operator to review.
        """
        changes = {}
        held = []
        repairs = []
        confident = all(job_data.get(field) for field in ("address_1", "city", "state"))
        legacy_address = cls._legacy_parse_address(existing["capture_address_raw"])
        for field, value in job_data.items():
            if field == "external_job_id" or field in protected_fields or existing[field] == value:
                continue
            if field not in _ADDRESS_FIELDS:
                if value is not None:
                    changes[field] = value
            elif not confident or value is None:
                held.append(field)
            elif existing[field] in (None, ""):
                changes[field] = value
            elif existing[field] == legacy_address.get(field):
                # This exact value was produced by the former parser from the
                # stored raw source. It is safe to replace unless an operator edit
                # created a field-level override, which was handled above.
                changes[field] = value
                repairs.append(field)
            else:
                held.append(field)
        return changes, sorted(held), sorted(repairs)

    @classmethod
    def _job_changes(
        cls, job_data: dict[str, Any], existing: Any,
        protected_fields: set[str] | frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        return cls._job_changes_with_review(job_data, existing, protected_fields)[0]

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
                if None in row:
                    raise ValueError(
                        f"Row {source_row_number} has extra CSV columns; check address quoting"
                    )
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
                row["external_job_id"].casefold(): row
                for row in connection.execute(
                    "SELECT * FROM Jobs"
                )
            }
            existing_records = {
                row["external_record_number"]: row["source_row_json"]
                for row in connection.execute(
                    "SELECT external_record_number, source_row_json FROM JobSourceRecords "
                    "WHERE source_system = 'OpenTable'"
                )
            }
            overrides = defaultdict(set)
            for row in connection.execute(
                "SELECT job_id, field_name FROM JobFieldOverrides "
                "WHERE source_system = 'OpenTable'"
            ):
                overrides[int(row["job_id"])].add(row["field_name"])

        items = []
        counts = defaultdict(int)
        for group in groups:
            job_key = group["external_job_id"].casefold()
            existing_job = existing_jobs.get(job_key)
            imported = 0
            changed = 0
            for row in group["source_rows"]:
                record_number = self._text(row.get("Record Number"))
                if record_number not in existing_records:
                    continue
                imported += 1
                if existing_records[record_number] != self._source_row_json(row):
                    changed += 1

            protected_fields = (overrides[int(existing_job["job_id"])]
                                if existing_job else set())
            job_changes, held_address, repaired_address = (self._job_changes_with_review(
                group["job"], existing_job, protected_fields
            ) if existing_job else ({}, [], []))
            protected_changes = (sorted(
                field for field in protected_fields & _ADDRESS_FIELDS
                if existing_job[field] != group["job"][field]
            ) if existing_job else [])
            address_changes = sorted(set(job_changes) & _ADDRESS_FIELDS)
            protected_status = (str(existing_job["job_status"]).casefold()
                                if existing_job is not None else "")
            if protected_status in {"cancelled", "archived"}:
                action = "Decision Required"
            elif imported == group["source_row_count"] and changed == 0 and not job_changes:
                action = "Skipped"
            elif existing_job is not None:
                action = "Updated"
            else:
                action = "Created"
            counts[action.lower()] += 1
            items.append({
                "action": action,
                "existing_job_id": int(existing_job["job_id"]) if existing_job else None,
                "external_job_id": group["external_job_id"],
                "client_name": group["job"].get("client_name_source"),
                "project_name": group["job"].get("project_name_source"),
                "job_status": group["job"].get("job_status"),
                "scheduled_start_at": group["job"].get("scheduled_start_at"),
                "capture_address": group["job"].get("capture_address_raw"),
                "source_row_count": group["source_row_count"],
                "already_imported_rows": imported,
                "changed_source_rows": changed,
                "changed_job_fields": sorted(job_changes),
                "protected_job_fields": protected_changes,
                "address_changes": address_changes,
                "address_repairs": repaired_address,
                "held_address_fields": held_address,
                "address_warnings": self._address_warnings(group["job"]),
                "parsed_address": {field: group["job"].get(field)
                                   for field in _ADDRESS_REVIEW_FIELDS},
                "current_address": ({field: existing_job[field]
                                     for field in _ADDRESS_REVIEW_FIELDS}
                                    if existing_job else None),
                "parent_record_count": group["parent_record_count"],
                "existing_job_status": existing_job["job_status"] if existing_job else None,
            })
        return {
            "file_name": Path(file_path).name,
            "groups": groups,
            "items": items,
            "counts": dict(counts),
        }

    def import_csv(self, session: Session, file_path: str, *, update_protected: bool = False) -> dict[str, Any]:
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
                    "SELECT * FROM Jobs WHERE external_job_id = ? COLLATE NOCASE",
                    (external_job_id,),
                ).fetchone()
                job_data = group["job"]
                job_changed = False
                if (existing is not None
                        and str(existing["job_status"]).casefold() in {"cancelled", "archived"}
                        and not update_protected):
                    result["skipped"] += 1
                    result.setdefault("protected", []).append(external_job_id)
                    continue
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
                    protected_fields = {row[0] for row in connection.execute(
                        "SELECT field_name FROM JobFieldOverrides WHERE job_id = ? "
                        "AND source_system = 'OpenTable'",
                        (job_id,),
                    )}
                    changes = self._job_changes(job_data, existing, protected_fields)
                    # An import may update source-owned details after explicit approval,
                    # but it must never silently reactivate a lifecycle-protected Job.
                    if str(existing["job_status"]).casefold() in {"cancelled", "archived"}:
                        changes.pop("job_status", None)
                        changes.pop("cancelled_at", None)
                        changes.pop("cancellation_reason", None)
                    job_changed = bool(changes)
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
                        source_json,
                        now,
                        Path(file_path).name,
                        int(row["__source_row_number"]),
                    )
                    if source_record is None:
                        connection.execute(
                            """
                            INSERT INTO JobSourceRecords (
                                job_id, source_system, external_record_number,
                                record_description, is_parent_record, requested_capture_size,
                                source_row_json, imported_at,
                                source_file_name, source_row_number, created_at
                            ) VALUES (?, 'OpenTable', ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                                requested_capture_size = ?, source_row_json = ?,
                                imported_at = ?, source_file_name = ?,
                                source_row_number = ?
                            WHERE job_source_record_id = ?
                            """,
                            (*values, int(source_record["job_source_record_id"])),
                        )
                        result["source_rows_updated"] += 1
                        changed_for_job += 1

                    source_record_id = int(
                        connection.execute(
                            "SELECT job_source_record_id FROM JobSourceRecords "
                            "WHERE source_system = 'OpenTable' AND external_record_number = ?",
                            (record_number,),
                        ).fetchone()["job_source_record_id"]
                    )
                    financial_values = (
                        job_id,
                        self._text(row.get("AP Invoice Number")),
                        self._money(row.get("CT Rate")),
                        self._money(row.get("CT Travel Payout")),
                        self._money(row.get("CT Off Hours Payout")),
                    )
                    connection.execute(
                        """
                        INSERT INTO JobFinancials (
                            job_id, job_source_record_id, ap_invoice_number, ct_rate,
                            ct_travel_payout, ct_off_hours_payout, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(job_source_record_id) DO UPDATE SET
                            job_id = excluded.job_id,
                            ap_invoice_number = excluded.ap_invoice_number,
                            ct_rate = excluded.ct_rate,
                            ct_travel_payout = excluded.ct_travel_payout,
                            ct_off_hours_payout = excluded.ct_off_hours_payout,
                            updated_at = excluded.created_at
                        """,
                        (financial_values[0], source_record_id, *financial_values[1:], now),
                    )

                if changed_for_job == 0 and existing is not None and not job_changed:
                    result["skipped"] += 1
                    result["updated"] -= 1
                result["job_ids"].append(job_id)

            record_event(
                connection,
                "opentable_csv_imported",
                actor_user_id=session.user_id,
                details={
                    "file_name": Path(file_path).name,
                    "created": result["created"],
                    "updated": result["updated"],
                    "skipped": result["skipped"],
                    "source_rows_added": result["source_rows_added"],
                    "source_rows_updated": result["source_rows_updated"],
                },
            )
        return result
