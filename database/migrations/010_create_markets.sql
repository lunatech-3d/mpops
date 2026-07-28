CREATE TABLE IF NOT EXISTS Markets (
    market_id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    status TEXT NOT NULL DEFAULT 'Active'
        CHECK (status IN ('Active', 'Inactive')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER NOT NULL,
    updated_at TEXT,
    updated_by INTEGER,
    FOREIGN KEY (created_by) REFERENCES Users(id),
    FOREIGN KEY (updated_by) REFERENCES Users(id)
);

CREATE INDEX IF NOT EXISTS idx_Markets_name
    ON Markets(market_name COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_Markets_status
    ON Markets(status);
