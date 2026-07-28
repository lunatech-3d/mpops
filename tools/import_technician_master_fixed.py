"""One-time importer for the LunaTech Technician Master CSV.

Run from Git Bash:
    python tools/import_technician_master.py \
      --database C:/sqlite/mpops/database/mpops.db \
      --file "imports/LunaTech_Technician_Master.xlsx - Technician Master.csv" \
      --actor-username YOUR_ADMIN_USERNAME \
      --dry-run

Replace --dry-run with --apply after reviewing the output.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


IGNORED_SOURCE_FIELDS = {
    "Preferred Payment Method",
    "Default Pay %",
    "Home Market",
    "Preferred Travel Radius (miles)",
}

TECH_COLUMN_MAP = {
    "Technician ID": "tech_code",
    "First Name": "first_name",
    "Last Name": "last_name",
    "Preferred Name": "preferred_name",
    "Status": "status",
    "Hire Date": "hire_date",
    "Termination Date": "termination_date",
    "Email": "email",
    "Cell Phone": "mobile_phone",
    "Home Phone": "home_phone",
    "Date of Birth": "date_of_birth",
    "Emergency Contact Name": "emergency_contact_name",
    "Emergency Contact Relationship": "emergency_contact_relationship",
    "Emergency Contact Phone": "emergency_contact_phone",
    "Driver License Number": "drivers_license_number",
    "Driver License State": "drivers_license_state",
    "Notes": "notes",
}

ADDRESS_COLUMN_MAP = {
    "Street Address 1": "address_1",
    "Street Address 2": "address_2",
    "City": "city",
    "State": "state",
    "ZIP Code": "zip_code",
}

DATE_FIELDS = {"hire_date", "termination_date", "date_of_birth"}
VALID_STATUSES = {"Active", "Inactive"}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def iso_date(value: str | None, field_name: str, source_row: int) -> str | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"source row {source_row}: invalid {field_name}: {value!r}")


def ssn_last4(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) < 4:
        raise ValueError("SSN / Tax ID must contain at least four digits")
    return digits[-4:]


def read_source(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    header_index = None
    for index, row in enumerate(rows):
        if "Technician ID" in row and "First Name" in row and "Last Name" in row:
            header_index = index
            break

    if header_index is None:
        raise ValueError("Could not locate the Technician Master header row")

    headers = rows[header_index]
    records: list[dict[str, str]] = []
    for source_row, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        padded = row + [""] * (len(headers) - len(row))
        record = dict(zip(headers, padded))
        if clean(record.get("Technician ID")):
            record["_source_row"] = str(source_row)
            records.append(record)
    return records


def validate_and_transform(records: list[dict[str, str]]) -> list[dict[str, Any]]:
    transformed: list[dict[str, Any]] = []
    seen_codes: set[str] = set()

    for record in records:
        source_row = int(record["_source_row"])
        tech: dict[str, Any] = {}

        for source_name, target_name in TECH_COLUMN_MAP.items():
            value = clean(record.get(source_name))
            if target_name in DATE_FIELDS:
                value = iso_date(value, source_name, source_row)
            tech[target_name] = value

        tech["ssn"] = clean(record.get("SSN / Tax ID"))
        tech["ssn_last4"] = ssn_last4(tech["ssn"])

        required = ("tech_code", "first_name", "last_name", "status")
        missing = [name for name in required if not tech.get(name)]
        if missing:
            raise ValueError(
                f"source row {source_row}: missing required values: {', '.join(missing)}"
            )

        if tech["status"] not in VALID_STATUSES:
            raise ValueError(
                f"source row {source_row}: invalid status {tech['status']!r}"
            )

        if tech["email"] and not EMAIL_RE.fullmatch(tech["email"]):
            raise ValueError(
                f"source row {source_row}: invalid email {tech['email']!r}"
            )

        code_key = tech["tech_code"].casefold()
        if code_key in seen_codes:
            raise ValueError(
                f"source row {source_row}: duplicate Technician ID {tech['tech_code']!r}"
            )
        seen_codes.add(code_key)

        address = {
            target: clean(record.get(source))
            for source, target in ADDRESS_COLUMN_MAP.items()
        }
        address_values = [address[name] for name in ("address_1", "city", "state", "zip_code")]
        if any(address_values) and not all(address_values):
            raise ValueError(
                f"source row {source_row}: partial address; address_1, city, state, "
                "and zip_code must all be present"
            )

        transformed.append(
            {
                "source_row": source_row,
                "tech": tech,
                "address": address if all(address_values) else None,
            }
        )

    return transformed


def record_event(
    connection: sqlite3.Connection,
    action: str,
    actor_user_id: int,
    details: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO AuditLog(actor_user_id, action, details_json)
        VALUES (?, ?, ?)
        """,
        (actor_user_id, action, json.dumps(details, sort_keys=True)),
    )


def import_records(
    database: Path,
    source_file: Path,
    apply: bool,
) -> None:
    records = validate_and_transform(read_source(source_file))

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        actor_id = 1

        source_codes = [item["tech"]["tech_code"] for item in records]
        placeholders = ",".join("?" for _ in source_codes)
        conflicts = connection.execute(
            f"""
            SELECT tech_code
            FROM Techs
            WHERE tech_code IN ({placeholders})
            COLLATE NOCASE
            """,
            source_codes,
        ).fetchall()

        if conflicts:
            conflict_codes = ", ".join(row["tech_code"] for row in conflicts)
            raise ValueError(
                f"Technician IDs already exist in the database: {conflict_codes}"
            )

        active_count = sum(item["tech"]["status"] == "Active" for item in records)
        inactive_count = len(records) - active_count
        address_count = sum(item["address"] is not None for item in records)

        print(f"Source file: {source_file}")
        print(f"Database: {database}")
        print(f"Rows read: {len(records)}")
        print(f"Active technicians: {active_count}")
        print(f"Inactive technicians: {inactive_count}")
        print(f"Complete addresses: {address_count}")
        print(f"Rows without addresses: {len(records) - address_count}")
        print("Ignored fields: Preferred Payment Method, Default Pay %, "
              "Home Market, Preferred Travel Radius (miles)")
        print("SSN handling: only the final four digits will be stored")

        if not apply:
            print("Database changes made: No")
            print("Dry run completed successfully")
            return

        connection.execute("BEGIN")

        tech_columns = [
            "tech_code",
            "first_name",
            "last_name",
            "preferred_name",
            "status",
            "email",
            "mobile_phone",
            "home_phone",
            "hire_date",
            "termination_date",
            "notes",
            "date_of_birth",
            "ssn_last4",
            "ssn",
            "drivers_license_number",
            "drivers_license_state",
            "emergency_contact_name",
            "emergency_contact_relationship",
            "emergency_contact_phone",
        ]

        for item in records:
            tech = item["tech"]
            values = [tech.get(column) for column in tech_columns]
            cursor = connection.execute(
                f"""
                INSERT INTO Techs (
                    {", ".join(tech_columns)},
                    created_by
                )
                VALUES (
                    {", ".join("?" for _ in tech_columns)},
                    ?
                )
                """,
                [*values, actor_id],
            )
            tech_id = int(cursor.lastrowid)

            record_event(
                connection,
                "technician_imported",
                actor_id,
                {
                    "tech_id": tech_id,
                    "tech_code": tech["tech_code"],
                    "source_file": source_file.name,
                    "source_row": item["source_row"],
                },
            )

            if item["address"] is not None:
                address = item["address"]
                address_cursor = connection.execute(
                    """
                    INSERT INTO TechAddresses (
                        tech_id,
                        address_1,
                        address_2,
                        city,
                        state,
                        zip_code,
                        is_primary,
                        created_by
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        tech_id,
                        address["address_1"],
                        address["address_2"],
                        address["city"],
                        address["state"],
                        address["zip_code"],
                        actor_id,
                    ),
                )
                record_event(
                    connection,
                    "technician_address_imported",
                    actor_id,
                    {
                        "tech_id": tech_id,
                        "address_id": int(address_cursor.lastrowid),
                        "tech_code": tech["tech_code"],
                        "source_file": source_file.name,
                        "source_row": item["source_row"],
                    },
                )

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Foreign-key check failed: {violations}")

        connection.commit()

        totals = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM Techs) AS technicians,
                (SELECT COUNT(*) FROM TechAddresses) AS addresses
            """
        ).fetchone()

        print(f"Technicians inserted: {len(records)}")
        print(f"Addresses inserted: {address_count}")
        print(f"Database technician total: {totals['technicians']}")
        print(f"Database address total: {totals['addresses']}")
        print("Foreign-key check: OK")
        print("Import committed: Yes")

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--file", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    import_records(
        database=args.database,
        source_file=args.file,
        apply=args.apply,
    )


if __name__ == "__main__":
    main()