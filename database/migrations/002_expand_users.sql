PRAGMA foreign_keys = ON;

ALTER TABLE users ADD COLUMN display_name TEXT;
ALTER TABLE users ADD COLUMN created_by INTEGER REFERENCES users(id);
ALTER TABLE users ADD COLUMN updated_at TEXT;
ALTER TABLE users ADD COLUMN updated_by INTEGER REFERENCES users(id);
