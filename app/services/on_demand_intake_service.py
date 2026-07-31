"""Parse and persist Matterport On-Demand jobs from their two source texts."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.services.assignment_service import AssignmentService
from app.services.jobs_service import JobsService


SOURCE_SYSTEM = "Skedulo / Matterport Capture Services Email"
PIPELINE = "On-Demand"
_ZONE_OFFSETS = {"EST": "-05:00", "EDT": "-04:00", "CST": "-06:00",
                 "CDT": "-05:00", "MST": "-07:00", "MDT": "-06:00",
                 "PST": "-08:00", "PDT": "-07:00"}


def _label(text: str, name: str, next_names: str = "") -> str | None:
    end = r"(?=\n(?:" + next_names + r"):|\Z)" if next_names else r"(?=\n[^\n:]+:|\Z)"
    pattern = r"(?ims)^\s*" + re.escape(name) + r":\s*(.*?)\s*" + end
    match = re.search(pattern, text)
    return match.group(1).strip() if match and match.group(1).strip() else None


def parse_address(raw: str | None) -> dict[str, str | None]:
    """Conservatively split common comma-delimited US addresses, retaining raw."""
    result = {"address_1": None, "address_2": None, "city": None, "state": None,
              "postal_code": None, "country": None}
    parts = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    if len(parts) >= 4 and re.fullmatch(r"[A-Za-z]{2}", parts[-3]) and re.fullmatch(r"\d{5}(?:-\d{4})?", parts[-2]):
        result.update(address_1=", ".join(parts[:-3]), city=parts[-4] if len(parts) > 4 else parts[1],
                      state=parts[-3].upper(), postal_code=parts[-2], country=parts[-1])
        # With the usual street, city, state, ZIP, country shape.
        if len(parts) == 5:
            result["address_1"], result["city"] = parts[0], parts[1]
    elif len(parts) >= 3:
        state_zip = re.fullmatch(r"([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)", parts[-1])
        if state_zip:
            result.update(address_1=", ".join(parts[:-2]), city=parts[-2],
                          state=state_zip.group(1).upper(), postal_code=state_zip.group(2), country="US")
    return result


def _duration_minutes(value: str | None) -> int | None:
    if not value:
        return None
    hours = re.search(r"(\d+(?:\.\d+)?)\s*hours?", value, re.I)
    minutes = re.search(r"(\d+)\s*(?:minutes?|min)\b", value, re.I)
    if hours or minutes:
        return round(float(hours.group(1)) * 60 if hours else 0) + (int(minutes.group(1)) if minutes else 0)
    return None


def parse_confirmation_email(raw_text: str) -> dict[str, Any]:
    if not raw_text.strip():
        raise ValueError("Paste the Matterport confirmation email before parsing.")
    job = re.search(r"(?im)^\s*Job ID:\s*(\S+)", raw_text)
    payout = re.search(r"(?im)^\s*Payout:\s*\$?([\d,]+(?:\.\d{1,2})?)", raw_text)
    duration = re.search(r"(?im)^\s*Approximately:\s*(.+?)\s*$", raw_text)
    date_line = re.search(r"(?im)^\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*.+?\d{4},\s*\d{1,2}:\d{2}\s*[AP]M(?:\s*\(([A-Z]{2,5})\))?\s*$", raw_text)
    scheduled = timezone = None
    if date_line:
        line = date_line.group(0).strip()
        timezone = date_line.group(1)
        clean = re.sub(r"\s*\([A-Z]{2,5}\)\s*$", "", line)
        parsed = datetime.strptime(clean, "%a, %d %b %Y, %I:%M %p")
        scheduled = parsed.isoformat(timespec="minutes") + _ZONE_OFFSETS.get(timezone or "", "")
    address = None
    if date_line:
        preceding = raw_text[:date_line.start()].splitlines()
        address = next((line.strip() for line in reversed(preceding) if line.strip()), None)
    return {"job_id": job.group(1).strip() if job else None, "address": address,
            "scheduled_start_at": scheduled, "timezone": timezone,
            "estimated_minutes": _duration_minutes(duration.group(1) if duration else None),
            "expected_payout": payout.group(1).replace(",", "") if payout else None}


def parse_skedulo_notes(raw_text: str) -> dict[str, Any]:
    if not raw_text.strip():
        raise ValueError("Paste the Skedulo Notes before parsing.")
    fields = {key: _label(raw_text, label) for key, label in (
        ("contact_name", "Contact Name"), ("contact_email", "Contact Email"),
        ("contact_phone", "Contact Phone"), ("contact_onsite", "Contact Will Be Onsite"),
        ("property_type", "Property Type"), ("property_size", "Property Size"),
        ("start_time", "Start Time"), ("estimated_time", "Estimated Time"),
        ("address", "Address"), ("suite", "Suite"), ("capture_type", "Capture Type"),
        ("job_id", "Job ID"), ("job_link", "Job Link"))}
    fields["site_info"] = _label(raw_text, "Site Info", "Property Type")
    spaces = re.search(r"(?ims)^\s*Spaces:\s*.*?^\s*Name:\s*(.*?)\s*$.*?^\s*Property Size:\s*(.*?)\s*$", raw_text)
    fields["space_name"] = spaces.group(1).strip() if spaces else None
    fields["space_property_size"] = spaces.group(2).strip() if spaces else None
    fields["estimated_minutes"] = _duration_minutes(fields["estimated_time"])
    return fields


def combine_sources(email_text: str, notes_text: str) -> dict[str, Any]:
    email, notes = parse_confirmation_email(email_text), parse_skedulo_notes(notes_text)
    if not email["job_id"] or not notes["job_id"]:
        missing = "Matterport confirmation" if not email["job_id"] else "Skedulo Notes"
        raise ValueError(f"A Job ID could not be detected in the {missing}. Both source Job IDs are required.")
    if email["job_id"] and notes["job_id"] and email["job_id"].casefold() != notes["job_id"].casefold():
        raise ValueError("The Matterport confirmation and Skedulo Notes appear to belong to different jobs. "
                         f"Confirmation Job ID: {email['job_id']}; Skedulo Job ID: {notes['job_id']}.")
    warnings = []
    if email["address"] and notes["address"] and re.sub(r"\W", "", email["address"]).casefold() != re.sub(r"\W", "", notes["address"]).casefold():
        warnings.append("The addresses differ between the two sources.")
    if email["estimated_minutes"] and notes["estimated_minutes"] and email["estimated_minutes"] != notes["estimated_minutes"]:
        warnings.append("The estimated durations differ between the two sources.")
    if email["scheduled_start_at"] and notes["start_time"]:
        shown = datetime.fromisoformat(email["scheduled_start_at"]).strftime("%I:%M %p").lstrip("0")
        if shown.casefold() != notes["start_time"].strip().casefold():
            warnings.append("The start times differ between the two sources.")
    address = notes["address"] or email["address"]
    result = {**notes, **parse_address(address), "job_id": email["job_id"] or notes["job_id"],
              "address": address, "scheduled_start_at": email["scheduled_start_at"],
              "timezone": email["timezone"], "estimated_minutes": email["estimated_minutes"] or notes["estimated_minutes"],
              "expected_payout": email["expected_payout"], "warnings": warnings,
              "email_job_id": email["job_id"], "notes_job_id": notes["job_id"]}
    return result


class OnDemandIntakeService:
    def __init__(self, auth):
        self.auth = auth
        self.jobs = JobsService(auth)
        self.assignments = AssignmentService(auth)

    def list_active_technicians(self):
        rows = self.assignments.list_active_technicians()
        return sorted(rows, key=lambda row: ((row.get("first_name") or "").casefold(), (row.get("last_name") or "").casefold()))

    def import_job(self, session, data: dict[str, Any], email_text: str, notes_text: str, tech_id: int) -> tuple[int, bool]:
        self.jobs._require_operator(session)
        required = (("job_id", "External Job ID"), ("address", "Address"),
                    ("scheduled_start_at", "Scheduled start"), ("expected_payout", "Expected payout"))
        missing = [label for key, label in required if not data.get(key)]
        if not tech_id:
            missing.append("Technician")
        if missing:
            raise ValueError("Required fields missing: " + ", ".join(missing))
        try:
            payout = Decimal(str(data["expected_payout"]).replace("$", "").replace(",", ""))
        except InvalidOperation as exc:
            raise ValueError("Expected payout must be a currency amount.") from exc
        if not payout.is_finite() or payout < 0:
            raise ValueError("Expected payout must be zero or greater.")
        job_fields = {"external_job_id": data["job_id"], "capture_address_raw": data["address"],
                      "address_1": data.get("address_1"), "address_2": data.get("suite") or data.get("address_2"),
                      "city": data.get("city"), "state": data.get("state"), "postal_code": data.get("postal_code"),
                      "country": data.get("country"), "scheduled_start_at": data["scheduled_start_at"],
                      "requested_capture_size": re.sub(r"[^\d.]", "", str(data.get("property_size") or "")) or None,
                      "additional_details": data.get("site_info"), "scheduling_link": data.get("job_link"),
                      "onsite_contact_name": data.get("contact_name"), "onsite_contact_email": data.get("contact_email"),
                      "onsite_contact_phone": re.sub(r"\D", "", str(data.get("contact_phone") or "")) or None}
        existing = self.jobs.get_job_by_external_id(data["job_id"])
        if existing:
            # The UI confirms updates; only source-owned fields are supplied here.
            # Blank source controls never erase established operational data. Hayley
            # can still explicitly edit populated values in the reviewed preview.
            changes = {k: v for k, v in job_fields.items()
                       if v is not None and existing.get(k) != v}
            if changes:
                self.jobs.update_job(session, int(existing["job_id"]), changes)
            job_id, created = int(existing["job_id"]), False
        else:
            job_id, created = self.jobs.create_job(session, job_fields), True
        now = utc_now_iso()
        evidence = json.dumps({"pipeline": PIPELINE, "confirmation_email": email_text,
                               "skedulo_notes": notes_text, "parsed": data}, default=str)
        with self.auth.connection() as connection:
            connection.execute("""INSERT INTO JobSourceRecords
                (job_id, source_system, external_record_number, record_description,
                 source_row_json, imported_at, source_file_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_system, external_record_number) DO UPDATE SET
                  job_id=excluded.job_id, record_description=excluded.record_description,
                  source_row_json=excluded.source_row_json, imported_at=excluded.imported_at""",
                (job_id, SOURCE_SYSTEM, data["job_id"], PIPELINE, evidence, now, "Pasted On-Demand sources"))
            source_id = connection.execute("SELECT job_source_record_id FROM JobSourceRecords WHERE source_system=? AND external_record_number=?",
                                           (SOURCE_SYSTEM, data["job_id"])).fetchone()[0]
            connection.execute("""INSERT INTO JobFinancials
                (job_id, job_source_record_id, ap_invoice_number, ct_rate, created_at, updated_at)
                VALUES (?, ?, NULL, ?, ?, ?)
                ON CONFLICT(job_source_record_id) DO UPDATE SET ct_rate=excluded.ct_rate,
                  job_id=excluded.job_id, updated_at=excluded.updated_at""", (job_id, source_id, float(payout), now, now))
            record_event(connection, "on_demand_job_imported", actor_user_id=session.user_id,
                         details={"job_id": job_id, "external_job_id": data["job_id"], "created": created,
                                  "pipeline": PIPELINE, "source_system": SOURCE_SYSTEM})
        self.assignments.assign_primary(session, job_id, int(tech_id), "Assigned through On-Demand intake")
        return job_id, created
