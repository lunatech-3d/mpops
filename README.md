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

## Database and current scope

The database defaults to `mpops.db` in the repository root. Set `MPOPS_DB_PATH` to another file path. The database directory, initial schema, and forward-only migrations are applied automatically at startup while preserving existing users and hashes.

The Dashboard contains neutral summaries for upcoming jobs, assignment, technician payments, and reconciliation. Jobs, Technicians, Markets, Clients, Payments, and Reports are clear placeholders; no speculative operational tables or records are created.

## Development

```bash
python -m unittest discover -s tests -v
```

See [`docs/architecture/authentication.md`](docs/architecture/authentication.md) for security boundaries and [`docs/decisions/0001-independent-project.md`](docs/decisions/0001-independent-project.md) for project separation.
