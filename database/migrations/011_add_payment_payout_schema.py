"""Add Matterport receipts and technician payout schema without replacing existing objects."""


TABLES = {
    "MatterportPaymentBatches": """
        CREATE TABLE MatterportPaymentBatches (
            payment_batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_date TEXT NOT NULL,
            payment_amount_cents INTEGER NOT NULL,
            payment_method TEXT NOT NULL DEFAULT 'ACH',
            payer_name TEXT NOT NULL DEFAULT 'Matterport',
            source_system TEXT NOT NULL DEFAULT 'Tipalti',
            batch_status TEXT NOT NULL DEFAULT 'Draft',
            source_email_subject TEXT, source_email_received_at TEXT, notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, created_by INTEGER,
            updated_at TEXT, updated_by INTEGER,
            FOREIGN KEY (created_by) REFERENCES Users(id),
            FOREIGN KEY (updated_by) REFERENCES Users(id),
            CHECK (payment_amount_cents >= 0),
            CHECK (batch_status IN ('Draft', 'Imported', 'Needs Review', 'Reconciled',
                                    'Approved', 'Closed', 'Cancelled'))
        )""",
    "MatterportPaymentItems": """
        CREATE TABLE MatterportPaymentItems (
            payment_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_batch_id INTEGER NOT NULL, document_number TEXT NOT NULL,
            document_type TEXT NOT NULL DEFAULT 'Invoice', document_date TEXT,
            description_raw TEXT, amount_received_cents INTEGER NOT NULL, job_id INTEGER,
            match_status TEXT NOT NULL DEFAULT 'Unmatched', match_method TEXT, match_notes TEXT,
            expected_job_amount_cents INTEGER, resolved_amount_cents INTEGER,
            amount_resolution TEXT, amount_resolution_notes TEXT,
            amount_resolved_at TEXT, amount_resolved_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT,
            FOREIGN KEY (payment_batch_id) REFERENCES MatterportPaymentBatches(payment_batch_id),
            FOREIGN KEY (job_id) REFERENCES Jobs(job_id),
            FOREIGN KEY (amount_resolved_by) REFERENCES Users(id),
            UNIQUE (payment_batch_id, document_number), CHECK (amount_received_cents >= 0),
            CHECK (expected_job_amount_cents IS NULL OR expected_job_amount_cents >= 0),
            CHECK (resolved_amount_cents IS NULL OR resolved_amount_cents >= 0),
            CHECK (amount_resolution IS NULL OR amount_resolution IN ('Imported','Job','Manual')),
            CHECK (match_status IN ('Unmatched', 'Matched', 'Ambiguous', 'Missing Job',
                                    'Amount Review', 'Excluded'))
        )""",
    "TechnicianJobEarnings": """
        CREATE TABLE TechnicianJobEarnings (
            technician_earning_id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_item_id INTEGER NOT NULL, job_id INTEGER NOT NULL, tech_id INTEGER NOT NULL,
            gross_job_payment_cents INTEGER NOT NULL, pay_percentage NUMERIC NOT NULL,
            calculated_amount_cents INTEGER NOT NULL,
            adjustment_amount_cents INTEGER NOT NULL DEFAULT 0,
            final_technician_amount_cents INTEGER NOT NULL, percentage_source TEXT,
            calculation_status TEXT NOT NULL DEFAULT 'Calculated', adjustment_reason TEXT,
            approved_at TEXT, approved_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT,
            FOREIGN KEY (payment_item_id) REFERENCES MatterportPaymentItems(payment_item_id),
            FOREIGN KEY (job_id) REFERENCES Jobs(job_id), FOREIGN KEY (tech_id) REFERENCES Techs(tech_id),
            FOREIGN KEY (approved_by) REFERENCES Users(id), UNIQUE (payment_item_id, tech_id),
            CHECK (gross_job_payment_cents >= 0), CHECK (pay_percentage >= 0 AND pay_percentage <= 100),
            CHECK (calculation_status IN ('Calculated', 'Needs Review', 'Approved',
                                          'Included in Payment', 'Paid', 'Excluded'))
        )""",
    "TechnicianPaymentRuns": """
        CREATE TABLE TechnicianPaymentRuns (
            technician_payment_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_payment_batch_id INTEGER NOT NULL, payment_run_date TEXT,
            payment_status TEXT NOT NULL DEFAULT 'Draft', total_amount_cents INTEGER NOT NULL DEFAULT 0,
            notes TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, created_by INTEGER,
            updated_at TEXT, updated_by INTEGER,
            FOREIGN KEY (source_payment_batch_id) REFERENCES MatterportPaymentBatches(payment_batch_id),
            FOREIGN KEY (created_by) REFERENCES Users(id), FOREIGN KEY (updated_by) REFERENCES Users(id),
            UNIQUE (source_payment_batch_id), CHECK (total_amount_cents >= 0),
            CHECK (payment_status IN ('Draft', 'Approved', 'Submitted', 'Partially Paid',
                                      'Paid', 'Cancelled'))
        )""",
    "TechnicianPayments": """
        CREATE TABLE TechnicianPayments (
            technician_payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            technician_payment_run_id INTEGER NOT NULL, tech_id INTEGER NOT NULL,
            payment_amount_cents INTEGER NOT NULL, payment_method TEXT NOT NULL DEFAULT 'ACH',
            payment_status TEXT NOT NULL DEFAULT 'Pending', submitted_at TEXT, settled_at TEXT,
            bank_confirmation_number TEXT, notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT,
            FOREIGN KEY (technician_payment_run_id)
                REFERENCES TechnicianPaymentRuns(technician_payment_run_id),
            FOREIGN KEY (tech_id) REFERENCES Techs(tech_id),
            UNIQUE (technician_payment_run_id, tech_id), CHECK (payment_amount_cents >= 0),
            CHECK (payment_status IN ('Pending', 'Approved', 'Submitted', 'Paid', 'Failed', 'Cancelled'))
        )""",
    "TechnicianPaymentEarnings": """
        CREATE TABLE TechnicianPaymentEarnings (
            technician_payment_earning_id INTEGER PRIMARY KEY AUTOINCREMENT,
            technician_payment_id INTEGER NOT NULL, technician_earning_id INTEGER NOT NULL,
            amount_applied_cents INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (technician_payment_id) REFERENCES TechnicianPayments(technician_payment_id),
            FOREIGN KEY (technician_earning_id) REFERENCES TechnicianJobEarnings(technician_earning_id),
            UNIQUE (technician_earning_id), CHECK (amount_applied_cents >= 0)
        )""",
}

INDEXES = {
    "idx_MatterportPaymentItems_batch": ("MatterportPaymentItems", "payment_batch_id", False),
    "idx_MatterportPaymentItems_document": ("MatterportPaymentItems", "document_number", False),
    "idx_MatterportPaymentItems_job": ("MatterportPaymentItems", "job_id", False),
    "ux_MatterportPaymentItems_document": ("MatterportPaymentItems", "document_number", True),
    "idx_TechnicianJobEarnings_payment_item": ("TechnicianJobEarnings", "payment_item_id", False),
    "idx_TechnicianJobEarnings_job": ("TechnicianJobEarnings", "job_id", False),
    "idx_TechnicianJobEarnings_tech": ("TechnicianJobEarnings", "tech_id", False),
    "idx_TechnicianPayments_run": ("TechnicianPayments", "technician_payment_run_id", False),
    "idx_TechnicianPayments_tech": ("TechnicianPayments", "tech_id", False),
    "idx_TechnicianPaymentEarnings_payment": ("TechnicianPaymentEarnings", "technician_payment_id", False),
    "idx_TechnicianPaymentEarnings_earning": ("TechnicianPaymentEarnings", "technician_earning_id", False),
}


def _exists(connection, kind, name):
    return connection.execute(
        "SELECT name FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)
    ).fetchone() is not None


def migrate(connection):
    columns = {row[1].lower() for row in connection.execute("PRAGMA table_info(Techs)")}
    if "default_pay_percentage" not in columns:
        connection.execute("ALTER TABLE Techs ADD COLUMN default_pay_percentage NUMERIC")

    for name, definition in TABLES.items():
        if not _exists(connection, "table", name):
            connection.execute(definition)

    for name, (table, columns, unique) in INDEXES.items():
        if not _exists(connection, "index", name):
            qualifier = "UNIQUE " if unique else ""
            connection.execute(f"CREATE {qualifier}INDEX {name} ON {table}({columns})")
