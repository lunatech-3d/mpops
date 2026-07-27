# Matterport Ops

Matterport Ops is an independent desktop operations application for managing
Matterport-related workflows. This repository owns its source, SQLite database,
configuration, assets, documentation, tests, and release history. It has no
runtime dependency on Phoenix Database.

The first application layer provides local user registration, authentication,
session context, role-aware user administration, and an append-only audit trail.
Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes. The application uses
`mpops.db` by default and recognizes only `MPOPS_`-prefixed identity settings.

## Quick start

```bash
python -m app.security.register_user --username admin --role admin
python -m app.security.login
```

Set `MPOPS_DB_PATH` to select another database file. On first use, the schema is
created automatically from `database/schema/001_initial.sql`.

## Development

```bash
python -m unittest discover -s tests -v
```

See [`docs/architecture/authentication.md`](docs/architecture/authentication.md)
for security boundaries and [`docs/decisions/0001-independent-project.md`](docs/decisions/0001-independent-project.md)
for the project-separation decision.

