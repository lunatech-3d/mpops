"""Add the immutable financial snapshot for reconciled payment batches."""


COLUMNS = {
    "reconciled_at": "TEXT",
    "reconciled_by": "INTEGER REFERENCES Users(id)",
    "reconciled_imported_total_cents": "INTEGER",
    "reconciled_effective_total_cents": "INTEGER",
    "reconciled_payment_amount_cents": "INTEGER",
    "reconciled_matched_count": "INTEGER",
    "reconciled_excluded_count": "INTEGER",
    "reconciled_difference_cents": "INTEGER",
}


def migrate(connection):
    """Add each column once so upgrades are safe to resume."""
    existing = {row[1].lower() for row in connection.execute(
        "PRAGMA table_info(MatterportPaymentBatches)"
    )}
    for name, definition in COLUMNS.items():
        if name.lower() not in existing:
            connection.execute(
                f"ALTER TABLE MatterportPaymentBatches ADD COLUMN {name} {definition}"
            )
