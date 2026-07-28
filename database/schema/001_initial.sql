PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS Users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'operator' CHECK (role IN ('admin', 'operator', 'viewer')),
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    last_login_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER,
    updated_at TEXT,
    updated_by INTEGER,
    FOREIGN KEY (created_by) REFERENCES Users(id),
    FOREIGN KEY (updated_by) REFERENCES Users(id)
);

CREATE TABLE IF NOT EXISTS AuditLog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor_user_id INTEGER,
    subject_user_id INTEGER,
    action TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (actor_user_id) REFERENCES Users(id),
    FOREIGN KEY (subject_user_id) REFERENCES Users(id)
);
CREATE INDEX IF NOT EXISTS idx_AuditLog_occurred_at ON AuditLog(occurred_at);
CREATE INDEX IF NOT EXISTS idx_AuditLog_actor_user_id ON AuditLog(actor_user_id);
CREATE INDEX IF NOT EXISTS idx_AuditLog_subject_user_id ON AuditLog(subject_user_id);

CREATE TABLE IF NOT EXISTS Techs (
    tech_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tech_code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    first_name TEXT NOT NULL, middle_name TEXT, last_name TEXT NOT NULL,
    suffix TEXT, preferred_name TEXT,
    company_name TEXT, contractor_type TEXT,
    status TEXT NOT NULL DEFAULT 'Active',
    inactive_reason TEXT, date_of_birth TEXT, ssn_last4 TEXT,
    drivers_license_number TEXT, drivers_license_state TEXT,
    email TEXT, alternate_email TEXT, mobile_phone TEXT, home_phone TEXT, work_phone TEXT,
    emergency_contact_name TEXT, emergency_contact_relationship TEXT, emergency_contact_phone TEXT,
    hire_date TEXT, termination_date TEXT, notes TEXT,
    notes_private TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER NOT NULL,
    updated_at TEXT, updated_by INTEGER,
    FOREIGN KEY (created_by) REFERENCES Users(id),
    FOREIGN KEY (updated_by) REFERENCES Users(id)
);
CREATE INDEX IF NOT EXISTS idx_Techs_status ON Techs(status);
CREATE INDEX IF NOT EXISTS idx_Techs_name ON Techs(last_name, first_name);

CREATE TABLE IF NOT EXISTS TechAddresses (
    address_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tech_id INTEGER NOT NULL,
    address_1 TEXT NOT NULL, address_2 TEXT,
    city TEXT NOT NULL, state TEXT NOT NULL, zip_code TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 1 CHECK (is_primary IN (0, 1)),
    effective_date TEXT, end_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER NOT NULL,
    updated_at TEXT, updated_by INTEGER,
    FOREIGN KEY (tech_id) REFERENCES Techs(tech_id),
    FOREIGN KEY (created_by) REFERENCES Users(id),
    FOREIGN KEY (updated_by) REFERENCES Users(id)
);
CREATE INDEX IF NOT EXISTS idx_TechAddresses_tech_id ON TechAddresses(tech_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_TechAddresses_primary
    ON TechAddresses(tech_id) WHERE is_primary = 1;

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

CREATE TABLE IF NOT EXISTS SchemaMigrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- A fresh database is already canonical and must not run the compatibility rebuild.
INSERT OR IGNORE INTO SchemaMigrations(name, applied_at)
VALUES ('002_reconcile_legacy.py', CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO SchemaMigrations(name, applied_at)
VALUES ('003_expand_technicians.py', CURRENT_TIMESTAMP);
