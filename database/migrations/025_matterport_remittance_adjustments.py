"""Add typed remittance adjustments and auditable credit allocations."""


ITEM_COLUMNS = {
    "signed_effect_cents": "INTEGER",
    "account_name": "TEXT",
    "allocation_status": "TEXT NOT NULL DEFAULT 'Not Required'",
    "source_reference": "TEXT",
    "original_source_text": "TEXT",
    "direction_status": "TEXT NOT NULL DEFAULT 'Valid'",
    "imported_at": "TEXT",
    "created_by": "INTEGER REFERENCES Users(id)",
    "updated_by": "INTEGER REFERENCES Users(id)",
}


def migrate(connection):
    existing = {row[1].lower() for row in connection.execute(
        "PRAGMA table_info(MatterportPaymentItems)"
    )}
    for name, definition in ITEM_COLUMNS.items():
        if name.lower() not in existing:
            connection.execute(
                f"ALTER TABLE MatterportPaymentItems ADD COLUMN {name} {definition}"
            )
    connection.execute("""
        CREATE TABLE IF NOT EXISTS MatterportAdjustmentAllocations (
            adjustment_allocation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_item_id INTEGER NOT NULL,
            account_name TEXT,
            target_payment_item_id INTEGER,
            job_id INTEGER,
            allocation_amount_cents INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER NOT NULL,
            updated_at TEXT,
            updated_by INTEGER,
            FOREIGN KEY (payment_item_id) REFERENCES MatterportPaymentItems(payment_item_id),
            FOREIGN KEY (target_payment_item_id) REFERENCES MatterportPaymentItems(payment_item_id),
            FOREIGN KEY (job_id) REFERENCES Jobs(job_id),
            FOREIGN KEY (created_by) REFERENCES Users(id),
            FOREIGN KEY (updated_by) REFERENCES Users(id),
            CHECK (allocation_amount_cents > 0),
            CHECK (account_name IS NOT NULL OR target_payment_item_id IS NOT NULL OR job_id IS NOT NULL)
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_adjustment_allocations_item "
                       "ON MatterportAdjustmentAllocations(payment_item_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_adjustment_allocations_job "
                       "ON MatterportAdjustmentAllocations(job_id)")
