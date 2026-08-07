"""Extend the existing technician-payment ledger for direct and partial payments."""


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def migrate(connection):
    run_columns = _columns(connection, "TechnicianPaymentRuns")
    if "run_type" not in run_columns:
        connection.execute(
            "ALTER TABLE TechnicianPaymentRuns ADD COLUMN run_type TEXT NOT NULL DEFAULT 'Batch'"
        )

    payment_columns = _columns(connection, "TechnicianPayments")
    for name, definition in (
        ("payment_kind", "TEXT NOT NULL DEFAULT 'Batch'"),
        ("payment_category", "TEXT"),
        ("financial_component", "TEXT"),
        ("description", "TEXT"),
        ("reversal_of_payment_id", "INTEGER REFERENCES TechnicianPayments(technician_payment_id)"),
    ):
        if name not in payment_columns:
            connection.execute(f"ALTER TABLE TechnicianPayments ADD COLUMN {name} {definition}")

    # The old one-link constraint incorrectly implied that an earning could only
    # ever be paid once.  Preserve every allocation while allowing partial
    # payments and later payments against the same immutable earning.
    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='TechnicianPaymentEarnings'"
    ).fetchone()[0]
    if "UNIQUE(technician_earning_id)" in sql.replace(" ", ""):
        connection.execute("ALTER TABLE TechnicianPaymentEarnings RENAME TO _single_payment_earnings")
        connection.executescript("""
          CREATE TABLE TechnicianPaymentEarnings (
            technician_payment_earning_id INTEGER PRIMARY KEY AUTOINCREMENT,
            technician_payment_id INTEGER NOT NULL REFERENCES TechnicianPayments(technician_payment_id),
            technician_earning_id INTEGER NOT NULL REFERENCES TechnicianJobEarnings(technician_earning_id),
            amount_applied_cents INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(technician_payment_id, technician_earning_id)
          );
          INSERT INTO TechnicianPaymentEarnings SELECT * FROM _single_payment_earnings;
          DROP TABLE _single_payment_earnings;
        """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_direct_payments_tech ON TechnicianPayments(tech_id,payment_kind,payment_status)")
