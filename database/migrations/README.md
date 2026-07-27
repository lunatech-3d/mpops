# Migrations

Numbered `.sql` and conditional `.py` migrations run in filename order. A migration is
recorded in `SchemaMigrations` only after its transaction and foreign-key check pass.
`002_reconcile_legacy.py` normalizes the supported historical schema variants; fresh
databases already contain the final schema and mark that compatibility migration as
applied in `001_initial.sql`.
