# Migrations

Numbered `.sql` and conditional `.py` migrations run in filename order. A migration is
recorded in `SchemaMigrations` only after its transaction and foreign-key check pass.
`002_reconcile_legacy.py` normalizes the supported historical schema variants; fresh
databases already contain the final schema and mark that compatibility migration as
applied in `001_initial.sql`.

Migrations must not rely on `ALTER TABLE ... DROP COLUMN`. Some deployed Python
installations bundle SQLite versions older than 3.35. When columns must be removed,
migrations must provide a transactional table-rebuild fallback for those versions.

Every schema migration must be exercised both from an empty database and as an
upgrade of a populated database. Migration code must inspect legacy columns before
reading them so that resuming a partially applied historical migration is safe.
