"""Separate imported financial rows from operational Jobs and source metadata."""

import re
import sqlite3


def _columns(connection, table):
    """Return the case-insensitive column names currently present in *table*."""
    return {row[1].lower() for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _drop_columns_compat(connection, table, columns):
    """Rebuild *table* when SQLite predates native DROP COLUMN support."""
    schema = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    indexes = [row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    ) if not any(re.search(rf"\b{re.escape(column)}\b", row[0], re.IGNORECASE)
                  for column in columns)]
    temporary = f"__015_{table}"
    rebuilt = re.sub(
        rf"^CREATE TABLE\s+(?:IF NOT EXISTS\s+)?[\"`\[]?{re.escape(table)}[\"`\]]?",
        f'CREATE TABLE "{temporary}"',
        schema,
        count=1,
        flags=re.IGNORECASE,
    )
    for column in columns:
        rebuilt = re.sub(
            rf"^\s*[\"`\[]?{re.escape(column)}[\"`\]]?\s+[^,\n]+,?\s*$",
            "",
            rebuilt,
            count=1,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    rebuilt = re.sub(r",\s*\)$", "\n)", rebuilt)
    retained = [
        row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
        if row[1].lower() not in {column.lower() for column in columns}
    ]
    names = ", ".join(f'"{name}"' for name in retained)
    connection.execute(rebuilt)
    connection.execute(
        f'INSERT INTO "{temporary}" ({names}) SELECT {names} FROM "{table}"'
    )
    connection.execute(f'DROP TABLE "{table}"')
    connection.execute(f'ALTER TABLE "{temporary}" RENAME TO "{table}"')
    for index in indexes:
        connection.execute(index)


def _drop_legacy_columns(connection):
    targets = {
        "JobSourceRecords": (
            "ap_invoice_number", "ct_off_hours_payout", "ct_travel_payout", "ct_rate"
        ),
        "Jobs": ("ap_invoice_number",),
    }
    if sqlite3.sqlite_version_info >= (3, 35, 0):
        connection.execute("DROP INDEX IF EXISTS idx_JobSourceRecords_ap_invoice")
        for table, columns in targets.items():
            existing = _columns(connection, table)
            for column in columns:
                if column.lower() in existing:
                    connection.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
    else:
        for table, columns in targets.items():
            existing = _columns(connection, table)
            present = tuple(column for column in columns if column.lower() in existing)
            if present:
                _drop_columns_compat(connection, table, present)


def migrate(connection):
    # Create the destination before inspecting or reading any legacy source column.
    # Some databases are resuming an older, partially applied version of migration
    # 015, so the destination may exist after its source columns were removed.
    connection.execute(
        """CREATE TABLE IF NOT EXISTS JobFinancials (
            job_financial_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            job_source_record_id INTEGER,
            ap_invoice_number TEXT,
            ct_rate NUMERIC NOT NULL DEFAULT 0,
            ct_travel_payout NUMERIC NOT NULL DEFAULT 0,
            ct_off_hours_payout NUMERIC NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY (job_id) REFERENCES Jobs(job_id),
            FOREIGN KEY (job_source_record_id)
                REFERENCES JobSourceRecords(job_source_record_id) ON DELETE SET NULL,
            UNIQUE (job_source_record_id)
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_JobFinancials_job_id ON JobFinancials(job_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_JobFinancials_ap_invoice "
        "ON JobFinancials(ap_invoice_number COLLATE NOCASE)"
    )

    source_columns = _columns(connection, "JobSourceRecords")
    required_source_columns = {
        "job_id", "job_source_record_id", "ap_invoice_number", "ct_rate",
        "ct_travel_payout", "ct_off_hours_payout", "imported_at",
    }
    if required_source_columns <= source_columns:
        connection.execute(
            """INSERT OR IGNORE INTO JobFinancials (
            job_id, job_source_record_id, ap_invoice_number, ct_rate,
            ct_travel_payout, ct_off_hours_payout, created_at
        )
        SELECT job_id, job_source_record_id, ap_invoice_number, ct_rate,
               ct_travel_payout, ct_off_hours_payout, imported_at
        FROM JobSourceRecords"""
        )

    # Early Jobs schemas stored the invoice directly on the job. Preserve values
    # that do not have a corresponding source-record financial row before dropping
    # that legacy column.
    if "ap_invoice_number" in _columns(connection, "Jobs"):
        connection.execute(
            """INSERT INTO JobFinancials (job_id, ap_invoice_number)
               SELECT j.job_id, j.ap_invoice_number
               FROM Jobs j
               WHERE j.ap_invoice_number IS NOT NULL
                 AND NOT EXISTS (
                     SELECT 1 FROM JobFinancials f
                     WHERE f.job_id = j.job_id
                       AND f.ap_invoice_number = j.ap_invoice_number COLLATE NOCASE
                 )"""
        )
    _drop_legacy_columns(connection)
