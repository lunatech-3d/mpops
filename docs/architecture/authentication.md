# Authentication architecture

Matterport Ops authenticates against its own SQLite `Users` table. Passwords are
salted and hashed with PBKDF2-HMAC-SHA256; plaintext passwords are never stored or
logged. User lookup is case-insensitive while preserving the entered display name.

The first user must be an administrator and may be bootstrapped without a session.
Subsequent user creation and account activation changes require an authenticated
administrator. Later users store the administrator in `created_by`; account edits,
activation changes, and password resets store a timezone-aware UTC `updated_at` and
the administrator in `updated_by`. Successful and failed authentication attempts and
administrative changes are recorded in the append-only `AuditLog` table.

Lookup uses `username = ? COLLATE NOCASE`; the schema's `COLLATE NOCASE UNIQUE`
constraint provides case-insensitive uniqueness without a derived `username_key`.
Application-generated timestamps use `YYYY-MM-DDTHH:MM:SS+00:00`.

After authentication, a session can publish identity using `MPOPS_USER_ID`,
`MPOPS_USERNAME`, and `MPOPS_ROLE`. These values are process context, not trusted
credentials; authorization decisions use the immutable `Session` returned by the
authentication service.

## Provenance

The component boundaries were designed to permit adaptation of proven authentication,
session, audit, SQLite, and Tkinter patterns from Phoenix Database. No Phoenix source
was available in this repository during the initial implementation, so no source file
was copied. Matterport Ops contains no runtime import or database dependency on Phoenix.
