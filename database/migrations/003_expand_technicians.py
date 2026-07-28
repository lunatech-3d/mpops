"""Add the documented technician profile fields without rebuilding the table."""

COLUMNS = {
    "middle_name": "TEXT", "suffix": "TEXT", "company_name": "TEXT",
    "contractor_type": "TEXT", "inactive_reason": "TEXT", "date_of_birth": "TEXT",
    "ssn_last4": "TEXT", "drivers_license_number": "TEXT",
    "drivers_license_state": "TEXT", "alternate_email": "TEXT", "work_phone": "TEXT",
    "emergency_contact_name": "TEXT", "emergency_contact_relationship": "TEXT",
    "emergency_contact_phone": "TEXT", "notes_private": "TEXT",
}


def migrate(connection):
    existing = {row[1].lower() for row in connection.execute("PRAGMA table_info(Techs)")}
    for name, kind in COLUMNS.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE Techs ADD COLUMN {name} {kind}")
