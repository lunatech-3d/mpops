"""Add immutable-source amount resolution metadata to payment items."""


COLUMNS = {
    "expected_job_amount_cents": "INTEGER CHECK (expected_job_amount_cents >= 0)",
    "resolved_amount_cents": "INTEGER CHECK (resolved_amount_cents >= 0)",
    "amount_resolution": "TEXT CHECK (amount_resolution IN ('Imported', 'Job', 'Manual'))",
    "amount_resolution_notes": "TEXT",
    "amount_resolved_at": "TEXT",
    "amount_resolved_by": "INTEGER REFERENCES Users(id)",
}


def migrate(connection):
    """SQLite supports idempotent column additions through an explicit inspection."""
    existing = {row[1].lower() for row in connection.execute(
        "PRAGMA table_info(MatterportPaymentItems)"
    )}
    for name, definition in COLUMNS.items():
        if name.lower() not in existing:
            connection.execute(
                f"ALTER TABLE MatterportPaymentItems ADD COLUMN {name} {definition}"
            )
