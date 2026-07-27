PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'viewer')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    actor_user_id INTEGER REFERENCES users(id),
    subject_user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_log_occurred_at ON audit_log(occurred_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_user_id);

