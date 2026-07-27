# Matterport Ops database schema

This document describes the reconciled production schema. The live SQLite database is
`C:/sqlite/mpops/database/mpops.db` (overridable with `MPOPS_DB_PATH`). It is excluded
from Git because it contains credentials and operational/personal data; only schema,
migration, documentation, and test artifacts belong in the repository.

## Conventions and tables

Table names are PascalCase and fields are lowercase `snake_case`.

* **`Users`** — `id` primary key; case-insensitively unique `username`;
  `password_hash`, `display_name`, constrained `role`, constrained `is_active`,
  `last_login_at`, and create/update timestamps and user references. `created_by` and
  `updated_by` reference `Users.id` and may be null for the bootstrap administrator.
* **`AuditLog`** — append-only events keyed by `id`, with `occurred_at`, optional
  `actor_user_id` and `subject_user_id` references to `Users.id`, `action`, and
  `details_json`.
* **`Techs`** — keyed by `tech_id`, with case-insensitively unique `tech_code`, name,
  status, contact, employment, notes, and create/update audit fields. User references
  target `Users.id`.
* **`TechAddresses`** — keyed by `address_id`; `tech_id` references `Techs.tech_id`;
  address, effective period, constrained `is_primary`, and create/update audit fields.
* **`SchemaMigrations`** — migration `name` primary key and `applied_at` timestamp.

The exact authoritative columns, defaults, checks, and foreign keys are executable in
[`001_initial.sql`](001_initial.sql).

## Indexes

SQLite supplies unique indexes for `Users.username` and `Techs.tech_code`. Explicit
indexes are `idx_AuditLog_occurred_at`, `idx_AuditLog_actor_user_id`,
`idx_AuditLog_subject_user_id`, `idx_Techs_status`, `idx_Techs_name`,
`idx_TechAddresses_tech_id`, and partial unique `ux_TechAddresses_primary`. The latter
permits no more than one primary address for each technician.

## Relationships and migrations

Users create/update users, technicians, and addresses and may act in audit events. A
technician has zero or more addresses. Foreign-key enforcement is enabled on every
application connection.

Numbered forward-only migrations run in filename order. Each migration and its
`SchemaMigrations` record commit together; failures roll back and are not recorded.
The compatibility migration inspects actual legacy names, preserves IDs, hashes and
records, rebuilds inconsistent tables, validates row counts, and requires
`PRAGMA foreign_key_check` to return no rows. A fresh schema records that compatibility
migration immediately because it already has the final structure.

Resolved historical issues include lowercase authentication table names,
`username_key`, PascalCase technician fields, invalid `Users(UserID)` references,
missing audit storage/indexes, and unenforced primary-address uniqueness.
