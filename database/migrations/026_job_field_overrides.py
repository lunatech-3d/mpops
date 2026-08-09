"""Track field-level local ownership overrides for imported Jobs."""


def migrate(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS JobFieldOverrides (
            job_field_override_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            field_name TEXT NOT NULL,
            source_system TEXT NOT NULL DEFAULT 'OpenTable',
            protected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            protected_by INTEGER,
            reason TEXT,
            FOREIGN KEY (job_id) REFERENCES Jobs(job_id),
            FOREIGN KEY (protected_by) REFERENCES Users(id),
            UNIQUE (job_id, field_name, source_system)
        );

        CREATE INDEX IF NOT EXISTS idx_JobFieldOverrides_job_source
            ON JobFieldOverrides(job_id, source_system);
        """
    )
