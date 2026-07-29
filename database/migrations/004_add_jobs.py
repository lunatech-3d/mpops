"""Add the initial Projects, Jobs, JobSourceRecords, and JobAssignments tables."""


def migrate(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS Projects (
            project_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_code TEXT COLLATE NOCASE UNIQUE,
            project_name TEXT NOT NULL,
            client_name TEXT,
            project_status TEXT NOT NULL DEFAULT 'Active',
            project_type TEXT,
            capture_frequency TEXT,
            site_address_1 TEXT,
            site_address_2 TEXT,
            site_city TEXT,
            site_state TEXT,
            site_postal_code TEXT,
            site_county TEXT,
            site_country TEXT,
            project_start_date TEXT,
            project_end_date TEXT,
            project_notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER NOT NULL,
            updated_at TEXT,
            updated_by INTEGER,
            FOREIGN KEY (created_by) REFERENCES Users(id),
            FOREIGN KEY (updated_by) REFERENCES Users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_Projects_status ON Projects(project_status);
        CREATE INDEX IF NOT EXISTS idx_Projects_name ON Projects(project_name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_Projects_location
            ON Projects(site_state, site_city, site_postal_code);

        CREATE TABLE IF NOT EXISTS Jobs (
            job_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            external_job_id TEXT NOT NULL COLLATE NOCASE UNIQUE,
            ap_invoice_number TEXT,
            project_name_source TEXT,
            client_name_source TEXT,
            job_status TEXT NOT NULL DEFAULT 'Requested',
            request_received_at TEXT,
            scheduled_start_at TEXT,
            actual_start_at TEXT,
            completed_at TEXT,
            cancelled_at TEXT,
            capture_address_raw TEXT,
            address_1 TEXT,
            address_2 TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            county TEXT,
            country TEXT,
            requested_capture_size NUMERIC,
            additional_details TEXT,
            scheduling_link TEXT,
            floor_plan_attachments TEXT,
            onsite_contact_name TEXT,
            onsite_contact_email TEXT,
            onsite_contact_phone TEXT,
            preferred_datetime_1 TEXT,
            preferred_datetime_2 TEXT,
            alternate_datetime_1 TEXT,
            alternate_datetime_2 TEXT,
            alternate_datetime_3 TEXT,
            cancellation_reason TEXT,
            internal_notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER NOT NULL,
            updated_at TEXT,
            updated_by INTEGER,
            FOREIGN KEY (project_id) REFERENCES Projects(project_id),
            FOREIGN KEY (created_by) REFERENCES Users(id),
            FOREIGN KEY (updated_by) REFERENCES Users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_Jobs_project_id ON Jobs(project_id);
        CREATE INDEX IF NOT EXISTS idx_Jobs_status ON Jobs(job_status);
        CREATE INDEX IF NOT EXISTS idx_Jobs_scheduled_start ON Jobs(scheduled_start_at);
        CREATE INDEX IF NOT EXISTS idx_Jobs_location ON Jobs(state, city, postal_code);

        CREATE TABLE IF NOT EXISTS JobSourceRecords (
            job_source_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            source_system TEXT NOT NULL DEFAULT 'OpenTable',
            external_record_number TEXT NOT NULL,
            record_description TEXT,
            is_parent_record INTEGER NOT NULL DEFAULT 0 CHECK (is_parent_record IN (0, 1)),
            requested_capture_size NUMERIC,
            ct_rate NUMERIC NOT NULL DEFAULT 0,
            ct_travel_payout NUMERIC NOT NULL DEFAULT 0,
            ct_off_hours_payout NUMERIC NOT NULL DEFAULT 0,
            ap_invoice_number TEXT,
            source_row_json TEXT,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source_file_name TEXT,
            source_row_number INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (job_id) REFERENCES Jobs(job_id),
            UNIQUE (source_system, external_record_number)
        );

        CREATE INDEX IF NOT EXISTS idx_JobSourceRecords_job_id
            ON JobSourceRecords(job_id);
        CREATE INDEX IF NOT EXISTS idx_JobSourceRecords_ap_invoice
            ON JobSourceRecords(ap_invoice_number);

        CREATE TABLE IF NOT EXISTS JobAssignments (
            job_assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            tech_id INTEGER NOT NULL,
            assignment_role TEXT NOT NULL DEFAULT 'Primary',
            assignment_status TEXT NOT NULL DEFAULT 'Assigned',
            assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            accepted_at TEXT,
            declined_at TEXT,
            completed_at TEXT,
            unassigned_at TEXT,
            assigned_by INTEGER,
            assignment_notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY (job_id) REFERENCES Jobs(job_id),
            FOREIGN KEY (tech_id) REFERENCES Techs(tech_id),
            FOREIGN KEY (assigned_by) REFERENCES Users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_JobAssignments_job_id
            ON JobAssignments(job_id);
        CREATE INDEX IF NOT EXISTS idx_JobAssignments_tech_id
            ON JobAssignments(tech_id);
        CREATE INDEX IF NOT EXISTS idx_JobAssignments_status
            ON JobAssignments(assignment_status);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_JobAssignments_active_primary
            ON JobAssignments(job_id)
            WHERE assignment_role = 'Primary' AND unassigned_at IS NULL;
        """
    )
