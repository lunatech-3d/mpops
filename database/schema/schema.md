# Matterport Ops Database Schema

## Document Status

This document describes the schema found in the Matterport Ops SQLite database reviewed on July 27, 2026.

**Current reviewed file:** `/mnt/data/mpops.db`  
**Intended production location:** `C:/sqlite/mpops/database/mpops.db`

The production database should not normally be committed to GitHub. The repository should contain this schema documentation, SQL creation scripts, migrations, and test database builders instead.

---

## Naming Conventions

The current database uses the following naming pattern:

- **Table names:** PascalCase, such as `Users`, `Techs`, and `TechAddresses`.
- **User table fields:** lowercase `snake_case`, such as `username`, `password_hash`, and `created_at`.
- **Technician table fields:** PascalCase, such as `TechID`, `FirstName`, and `CreatedAt`.

This reflects the database exactly as reviewed. The field naming conventions are therefore currently inconsistent between the authentication tables and the technician tables.

---

## Database Overview

The database currently contains three application tables:

| Table | Purpose |
|---|---|
| `Users` | Stores application login accounts, roles, account status, and audit metadata. |
| `Techs` | Stores Matterport technician identity, contact, employment, and status information. |
| `TechAddresses` | Stores one or more addresses associated with a technician. |

No views, triggers, or application-defined indexes were found. SQLite automatically created unique indexes for `Users.username` and `Techs.TechCode`.

---

# Table: Users

## Purpose

Stores Matterport Ops application users and authentication information.

## Definition

```sql
CREATE TABLE Users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    username            TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash       TEXT NOT NULL,

    display_name        TEXT,

    role                TEXT NOT NULL DEFAULT 'operator'
                            CHECK (role IN ('admin','operator','viewer')),

    is_active           INTEGER NOT NULL DEFAULT 0
                            CHECK (is_active IN (0,1)),

    last_login_at       TEXT,

    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by          INTEGER,

    updated_at          TEXT,
    updated_by          INTEGER,

    FOREIGN KEY (created_by)
        REFERENCES users(id),

    FOREIGN KEY (updated_by)
        REFERENCES users(id)
);
```

## Fields

| Field | Type | Required | Default | Description |
|---|---|---:|---|---|
| `id` | INTEGER | Yes | Auto-generated | Primary key for the user account. |
| `username` | TEXT | Yes | None | Case-insensitive unique login name. |
| `password_hash` | TEXT | Yes | None | Salted password hash. Plaintext passwords must never be stored. |
| `display_name` | TEXT | No | NULL | Friendly name displayed in the interface and reports. |
| `role` | TEXT | Yes | `operator` | Application role. Allowed values are `admin`, `operator`, and `viewer`. |
| `is_active` | INTEGER | Yes | `0` | Account status. `1` means active; `0` means inactive. |
| `last_login_at` | TEXT | No | NULL | Timestamp of the most recent successful login. |
| `created_at` | TEXT | Yes | `CURRENT_TIMESTAMP` | Timestamp when the account was created. |
| `created_by` | INTEGER | No | NULL | User who created the account. May be NULL for the bootstrap administrator. |
| `updated_at` | TEXT | No | NULL | Timestamp of the most recent account update. |
| `updated_by` | INTEGER | No | NULL | User who most recently updated the account. |

## Keys and Constraints

- Primary key: `id`
- Unique constraint: `username`
- `username` uses `COLLATE NOCASE`, making uniqueness case-insensitive.
- `role` must be one of:
  - `admin`
  - `operator`
  - `viewer`
- `is_active` must be `0` or `1`.
- `created_by` references `Users.id`.
- `updated_by` references `Users.id`.

## Indexes

SQLite automatically created:

```text
sqlite_autoindex_Users_1
```

This unique index supports the `username` unique constraint.

---

# Table: Techs

## Purpose

Stores the core identity, contact, employment, and status information for Matterport technicians.

## Definition

```sql
CREATE TABLE Techs (
    TechID              INTEGER PRIMARY KEY AUTOINCREMENT,

    TechCode            TEXT NOT NULL UNIQUE,

    FirstName           TEXT NOT NULL,
    LastName            TEXT NOT NULL,
    PreferredName       TEXT,

    Status              TEXT NOT NULL DEFAULT 'Active',

    Email               TEXT,
    MobilePhone         TEXT,
    HomePhone           TEXT,

    HireDate            TEXT,
    TerminationDate     TEXT,

    Notes               TEXT,

    CreatedAt           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CreatedBy           INTEGER NOT NULL,
    UpdatedAt           TEXT,
    UpdatedBy           INTEGER,

    FOREIGN KEY (CreatedBy) REFERENCES Users(UserID),
    FOREIGN KEY (UpdatedBy) REFERENCES Users(UserID)
);
```

## Fields

| Field | Type | Required | Default | Description |
|---|---|---:|---|---|
| `TechID` | INTEGER | Yes | Auto-generated | Primary key for the technician. |
| `TechCode` | TEXT | Yes | None | Unique operational code assigned to the technician. |
| `FirstName` | TEXT | Yes | None | Technician's legal or standard first name. |
| `LastName` | TEXT | Yes | None | Technician's last name. |
| `PreferredName` | TEXT | No | NULL | Name the technician prefers to use. |
| `Status` | TEXT | Yes | `Active` | Current technician status. No CHECK constraint currently limits the permitted values. |
| `Email` | TEXT | No | NULL | Technician email address. |
| `MobilePhone` | TEXT | No | NULL | Technician mobile telephone number. |
| `HomePhone` | TEXT | No | NULL | Technician home telephone number. |
| `HireDate` | TEXT | No | NULL | Date the technician began working with the company. |
| `TerminationDate` | TEXT | No | NULL | Date the technician stopped working with the company. |
| `Notes` | TEXT | No | NULL | Free-form administrative notes. |
| `CreatedAt` | TEXT | Yes | `CURRENT_TIMESTAMP` | Timestamp when the record was created. |
| `CreatedBy` | INTEGER | Yes | None | User who created the record. |
| `UpdatedAt` | TEXT | No | NULL | Timestamp of the most recent update. |
| `UpdatedBy` | INTEGER | No | NULL | User who most recently updated the record. |

## Keys and Constraints

- Primary key: `TechID`
- Unique constraint: `TechCode`
- Intended foreign key: `CreatedBy` to the creating user.
- Intended foreign key: `UpdatedBy` to the updating user.

## Indexes

SQLite automatically created:

```text
sqlite_autoindex_Techs_1
```

This unique index supports the `TechCode` unique constraint.

---

# Table: TechAddresses

## Purpose

Stores current and historical technician addresses. A technician may have multiple address records over time.

## Definition

```sql
CREATE TABLE TechAddresses (
    AddressID           INTEGER PRIMARY KEY AUTOINCREMENT,

    TechID              INTEGER NOT NULL,

    Address1            TEXT NOT NULL,
    Address2            TEXT,

    City                TEXT NOT NULL,
    State               TEXT NOT NULL,
    ZipCode             TEXT NOT NULL,

    IsPrimary           INTEGER NOT NULL DEFAULT 1
                            CHECK (IsPrimary IN (0,1)),

    EffectiveDate       TEXT,
    EndDate             TEXT,

    CreatedAt           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CreatedBy           INTEGER NOT NULL,
    UpdatedAt           TEXT,
    UpdatedBy           INTEGER,

    FOREIGN KEY (TechID) REFERENCES Techs(TechID),
    FOREIGN KEY (CreatedBy) REFERENCES Users(UserID),
    FOREIGN KEY (UpdatedBy) REFERENCES Users(UserID)
);
```

## Fields

| Field | Type | Required | Default | Description |
|---|---|---:|---|---|
| `AddressID` | INTEGER | Yes | Auto-generated | Primary key for the technician address record. |
| `TechID` | INTEGER | Yes | None | Technician associated with the address. |
| `Address1` | TEXT | Yes | None | Primary street-address line. |
| `Address2` | TEXT | No | NULL | Secondary address information, such as apartment or unit. |
| `City` | TEXT | Yes | None | City. |
| `State` | TEXT | Yes | None | State or province. |
| `ZipCode` | TEXT | Yes | None | Postal code. Stored as text to preserve leading zeros and extended formats. |
| `IsPrimary` | INTEGER | Yes | `1` | Indicates whether this is the technician's primary address. |
| `EffectiveDate` | TEXT | No | NULL | Date the address became effective. |
| `EndDate` | TEXT | No | NULL | Date the address stopped being effective. |
| `CreatedAt` | TEXT | Yes | `CURRENT_TIMESTAMP` | Timestamp when the address record was created. |
| `CreatedBy` | INTEGER | Yes | None | User who created the address record. |
| `UpdatedAt` | TEXT | No | NULL | Timestamp of the most recent update. |
| `UpdatedBy` | INTEGER | No | NULL | User who most recently updated the address record. |

## Keys and Constraints

- Primary key: `AddressID`
- `TechID` references `Techs.TechID`.
- `IsPrimary` must be `0` or `1`.
- Intended foreign key: `CreatedBy` to the creating user.
- Intended foreign key: `UpdatedBy` to the updating user.

## Indexes

No explicit indexes currently exist on `TechAddresses`.

---

# Relationships

```text
Users
  ├── creates/updates Users
  ├── creates/updates Techs
  └── creates/updates TechAddresses

Techs
  └── has many TechAddresses
```

Expected cardinality:

- One `Users` record may create or update many records.
- One `Techs` record may have zero, one, or many `TechAddresses` records.
- Each `TechAddresses` record belongs to exactly one `Techs` record.

---

# Known Schema Issues

## 1. Broken User Foreign-Key Targets

The `Techs` and `TechAddresses` tables currently declare foreign keys to:

```text
Users(UserID)
```

However, the primary key in `Users` is:

```text
id
```

There is no `UserID` field in the current `Users` table.

The affected declarations are:

```sql
FOREIGN KEY (CreatedBy) REFERENCES Users(UserID)
FOREIGN KEY (UpdatedBy) REFERENCES Users(UserID)
```

These should eventually reference:

```sql
FOREIGN KEY (CreatedBy) REFERENCES Users(id)
FOREIGN KEY (UpdatedBy) REFERENCES Users(id)
```

This mismatch should be corrected through a controlled database migration before technician inserts or updates rely on foreign-key enforcement.

## 2. Mixed Field-Naming Conventions

The `Users` table uses lowercase `snake_case`, while `Techs` and `TechAddresses` use PascalCase field names.

The agreed convention for future work is:

- Table names: PascalCase
- Field names: lowercase `snake_case`

The existing technician tables will therefore eventually need a deliberate migration if the project is to follow the agreed convention consistently. They should not be renamed casually in application code without an accompanying migration.

## 3. No AuditLog Table

The reviewed database does not currently contain an `AuditLog` or equivalent application audit table, even though audit fields exist on the three tables.

Authentication code previously committed to the repository referenced an `audit_log` table. The database and application code must be reconciled before the audit feature can be considered complete.

## 4. Missing Supporting Indexes

The following indexes may eventually be useful but are not currently present:

```text
TechAddresses.TechID
TechAddresses.IsPrimary
Techs.Status
Techs.LastName, Techs.FirstName
Users.is_active, Users.role
```

Indexes should be added only when justified by actual application queries.

## 5. Primary Address Uniqueness Is Not Enforced

`TechAddresses.IsPrimary` identifies a primary address, but the database does not prevent a technician from having multiple rows where `IsPrimary = 1`.

A future partial unique index could enforce one primary address per technician:

```sql
CREATE UNIQUE INDEX UX_TechAddresses_Primary
ON TechAddresses(TechID)
WHERE IsPrimary = 1;
```

This should be adopted only after existing data has been checked for conflicts.

---

# Date and Timestamp Storage

All dates and timestamps are currently stored as SQLite `TEXT` values.

Recommended formats:

- Date only: `YYYY-MM-DD`
- UTC timestamp: `YYYY-MM-DDTHH:MM:SS+00:00`

`CURRENT_TIMESTAMP` in SQLite produces a UTC value in this form:

```text
YYYY-MM-DD HH:MM:SS
```

The application should use one documented timestamp convention consistently.

---

# Repository and Deployment Guidance

The live database is intended to reside at:

```text
C:/sqlite/mpops/database/mpops.db
```

The repository should normally include:

```text
database/schema/
database/migrations/
docs/schema.md
tests/
```

The repository should normally exclude:

```text
mpops.db
mpops.db-shm
mpops.db-wal
mpops.db-journal
```

This protects credentials, password hashes, technician information, customer data, payment information, and operational records.

---

# Current Schema Summary

| Item | Current Count |
|---|---:|
| Application tables | 3 |
| Views | 0 |
| Triggers | 0 |
| Explicit application indexes | 0 |
| SQLite automatic unique indexes | 2 |

The database currently provides the beginnings of user authentication and technician management. Before application UI development proceeds deeply, the user foreign-key mismatch and the missing audit table should be resolved through versioned migrations.
