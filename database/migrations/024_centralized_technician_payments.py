"""Fields and line items for centralized and historical technician payments."""

def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

def migrate(connection):
    columns=_columns(connection,"TechnicianPayments")
    if "is_historical" not in columns:
        connection.execute("ALTER TABLE TechnicianPayments ADD COLUMN is_historical INTEGER NOT NULL DEFAULT 0 CHECK(is_historical IN (0,1))")
    for name,definition in (("reversed_at","TEXT"),("reversed_by","INTEGER REFERENCES Users(id)"),("reversal_reason","TEXT")):
        if name not in columns:
            connection.execute(f"ALTER TABLE TechnicianPayments ADD COLUMN {name} {definition}")
    connection.executescript("""
      CREATE TABLE IF NOT EXISTS TechnicianPaymentItems (
        technician_payment_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        technician_payment_id INTEGER NOT NULL REFERENCES TechnicianPayments(technician_payment_id),
        item_type TEXT NOT NULL CHECK(item_type IN ('Reimbursement','Bonus','Adjustment','Other direct payment')),
        amount_cents INTEGER NOT NULL CHECK(amount_cents>0), description TEXT NOT NULL,
        notes TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE INDEX IF NOT EXISTS idx_TechnicianPaymentItems_payment ON TechnicianPaymentItems(technician_payment_id);
      CREATE INDEX IF NOT EXISTS idx_TechnicianPaymentEarnings_payment ON TechnicianPaymentEarnings(technician_payment_id);
      CREATE INDEX IF NOT EXISTS idx_TechnicianPaymentEarnings_earning ON TechnicianPaymentEarnings(technician_earning_id);
      CREATE UNIQUE INDEX IF NOT EXISTS ux_TechnicianPayments_reference
        ON TechnicianPayments(payment_reference COLLATE NOCASE) WHERE payment_reference IS NOT NULL AND trim(payment_reference)<>'';
    """)
