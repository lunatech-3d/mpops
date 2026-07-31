"""Enable manual technician payment recording and signed adjustment links."""


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def migrate(connection):
    # legacy_alter_table prevents SQLite from retargeting the earnings ledger's
    # included_in_payment_run_id foreign key while this parent is rebuilt.
    connection.execute("PRAGMA legacy_alter_table=ON")
    connection.execute("ALTER TABLE TechnicianPaymentRuns RENAME TO _phase4_payment_runs")
    connection.executescript("""
      CREATE TABLE TechnicianPaymentRuns (
        technician_payment_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_payment_batch_id INTEGER REFERENCES MatterportPaymentBatches(payment_batch_id),
        payment_run_date TEXT, payment_status TEXT NOT NULL DEFAULT 'Draft',
        total_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(total_amount_cents >= 0),
        notes TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_by INTEGER REFERENCES Users(id), updated_at TEXT, updated_by INTEGER REFERENCES Users(id),
        approved_at TEXT, approved_by INTEGER REFERENCES Users(id), cancelled_at TEXT,
        cancelled_by INTEGER REFERENCES Users(id), version INTEGER NOT NULL DEFAULT 1,
        CHECK(payment_status IN ('Draft','Approved','Submitted','Partially Paid','Paid','Cancelled'))
      );
      INSERT INTO TechnicianPaymentRuns
        (technician_payment_run_id,source_payment_batch_id,payment_run_date,payment_status,total_amount_cents,
         notes,created_at,created_by,updated_at,updated_by)
      SELECT technician_payment_run_id,source_payment_batch_id,payment_run_date,payment_status,total_amount_cents,
         notes,created_at,created_by,updated_at,updated_by FROM _phase4_payment_runs;
      DROP TABLE _phase4_payment_runs;
    """)
    connection.execute("PRAGMA legacy_alter_table=OFF")

    payment_columns = _columns(connection, "TechnicianPayments")
    for name, definition in (
        ("actual_amount_cents", "INTEGER"), ("payment_date", "TEXT"),
        ("payment_reference", "TEXT"), ("recorded_at", "TEXT"),
        ("recorded_by", "INTEGER REFERENCES Users(id)"), ("approved_at", "TEXT"),
        ("approved_by", "INTEGER REFERENCES Users(id)"),
    ):
        if name not in payment_columns:
            connection.execute(f"ALTER TABLE TechnicianPayments ADD COLUMN {name} {definition}")

    # The Phase IV nonnegative link constraint could not represent a separately
    # approved negative adjustment. Rebuild only this leaf junction table.
    sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='TechnicianPaymentEarnings'").fetchone()[0]
    if "amount_applied_cents >= 0" in sql:
        connection.execute("ALTER TABLE TechnicianPaymentEarnings RENAME TO _phase4_payment_earnings")
        connection.executescript("""
          CREATE TABLE TechnicianPaymentEarnings (
            technician_payment_earning_id INTEGER PRIMARY KEY AUTOINCREMENT,
            technician_payment_id INTEGER NOT NULL REFERENCES TechnicianPayments(technician_payment_id),
            technician_earning_id INTEGER NOT NULL REFERENCES TechnicianJobEarnings(technician_earning_id),
            amount_applied_cents INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(technician_earning_id)
          );
          INSERT INTO TechnicianPaymentEarnings SELECT * FROM _phase4_payment_earnings;
          DROP TABLE _phase4_payment_earnings;
        """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_TechnicianPaymentEarnings_payment ON TechnicianPaymentEarnings(technician_payment_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_TechnicianPaymentEarnings_earning ON TechnicianPaymentEarnings(technician_earning_id)")
