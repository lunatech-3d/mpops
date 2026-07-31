CREATE TABLE IF NOT EXISTS AppSettings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by INTEGER,
    FOREIGN KEY (updated_by) REFERENCES Users(id)
);

CREATE TABLE IF NOT EXISTS BackupHistory (
    backup_id INTEGER PRIMARY KEY AUTOINCREMENT,
    completed_at TEXT NOT NULL,
    source_database_path TEXT NOT NULL,
    destination_path TEXT,
    backup_filename TEXT,
    file_size INTEGER,
    succeeded INTEGER NOT NULL CHECK (succeeded IN (0, 1)),
    integrity_result TEXT,
    initiated_by INTEGER,
    error_message TEXT,
    FOREIGN KEY (initiated_by) REFERENCES Users(id)
);
CREATE INDEX IF NOT EXISTS idx_BackupHistory_completed_at
    ON BackupHistory(completed_at DESC);

INSERT OR IGNORE INTO AppSettings(setting_key, setting_value)
VALUES ('backup_retention_count', '30');
INSERT OR IGNORE INTO AppSettings(setting_key, setting_value)
VALUES ('backup_close_reminder', '1');
