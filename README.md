# Matterport Ops

Matterport Ops is an independent Tkinter desktop operations application with no runtime dependency on Phoenix Database. It uses SQLite, role-aware authentication, PBKDF2-HMAC-SHA256 password hashes, sessions, and an append-only audit log.

## Quick start

Bootstrap the first administrator **only when the database has no users**, then start the application:

```bash
python -m app.security.register_user --username admin --role admin
python -m app.main
```

The login screen rejects invalid credentials and inactive accounts without disclosing account details. After login, the header shows the current username and role. Log Out clears the session and returns to login without restarting Python; Exit closes the application.

## Roles and administration

* `admin` — full access, including **Administration → Users**. Administrators can search/filter users, add or edit accounts, reset passwords, and activate/deactivate other users.
* `operator` — normal operational access; no user administration.
* `viewer` — read-only access; no user administration.

An administrator cannot deactivate their own account. Password resets require confirmation in the interface and use the same validation and salted hashing as account creation.

`TechnicianService` provides technician lookup and administrator-only technician and
address mutations. Address changes are transactional and enforce at most one primary
address per technician; deleting a primary address intentionally leaves no primary
rather than selecting one implicitly. The Technician Manager UI remains a later milestone.

## Database and current scope

The production database defaults to `C:/sqlite/mpops/database/mpops.db`. Set
`MPOPS_DB_PATH` to override it (tests always use temporary paths). The parent directory,
canonical initial schema, and transactional forward-only migrations are handled at
startup while preserving existing records and password hashes. The live database and
its WAL/journal sidecars are ignored because they contain sensitive data.

The Dashboard contains neutral summaries for upcoming jobs, assignment, technician payments, and reconciliation. Jobs, Technicians, Markets, Clients, Payments, and Reports are clear placeholders; no speculative operational tables or records are created.

## Development

```bash
python -m unittest discover -s tests -v
```

Inspect a deployment without printing password hashes:

```bash
python -m app.verify_database
```

See [`docs/architecture/authentication.md`](docs/architecture/authentication.md) for security boundaries and [`docs/decisions/0001-independent-project.md`](docs/decisions/0001-independent-project.md) for project separation.
# Database backup and reporting copies

Use **Database Backup** in the navigation to select a local Google Drive for
Desktop folder and create a verified backup. MPOPS creates timestamped history
files and atomically refreshes `mpops_latest.db`; the synchronized copy must
never be selected as the operational database.

To open a downloaded, local copy for reporting, set `MPOPS_DB_PATH` to that
copy and set `MPOPS_REPORTING_COPY=1` before launching MPOPS. Reporting mode
opens SQLite with `mode=ro`, displays a reporting-copy banner, and exposes only
dashboard/report navigation. It is a one-way workflow and does not merge data.
