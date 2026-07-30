"""Separate imported financial rows from operational Jobs and source metadata."""


def migrate(connection):
    connection.executescript(
        """
        CREATE TABLE JobFinancials (
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
        );

        CREATE INDEX idx_JobFinancials_job_id ON JobFinancials(job_id);
        CREATE INDEX idx_JobFinancials_ap_invoice
            ON JobFinancials(ap_invoice_number COLLATE NOCASE);

        INSERT INTO JobFinancials (
            job_id, job_source_record_id, ap_invoice_number, ct_rate,
            ct_travel_payout, ct_off_hours_payout, created_at
        )
        SELECT job_id, job_source_record_id, ap_invoice_number, ct_rate,
               ct_travel_payout, ct_off_hours_payout, imported_at
        FROM JobSourceRecords;

        DROP INDEX IF EXISTS idx_JobSourceRecords_ap_invoice;
        ALTER TABLE JobSourceRecords DROP COLUMN ap_invoice_number;
        ALTER TABLE JobSourceRecords DROP COLUMN ct_off_hours_payout;
        ALTER TABLE JobSourceRecords DROP COLUMN ct_travel_payout;
        ALTER TABLE JobSourceRecords DROP COLUMN ct_rate;
        ALTER TABLE Jobs DROP COLUMN ap_invoice_number;
        """
    )
