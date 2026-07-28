# Matterport Ops database schema

This document describes the reconciled production schema and the approved design for
new operational tables. The live SQLite database is
`C:/sqlite/mpops/database/mpops.db` (overridable with `MPOPS_DB_PATH`). It is excluded
from Git because it contains credentials and operational/personal data; only schema,
migration, documentation, and test artifacts belong in the repository.

## Conventions and tables

Table names are PascalCase and fields are lowercase `snake_case`.

Internal database record IDs are implementation details. They may be used internally
for relationships, selections, service calls, auditing, and database operations, but
they must not normally be displayed through application forms, lists, details screens,
reports, or operational exports.

User-facing records should instead be identified by meaningful business values:

* technicians use `tech_code`, not `tech_id`;
* jobs use `external_job_id`, not `job_id`;
* imported source rows use `external_record_number`, not `job_source_record_id`;
* projects use `project_code` or `project_name`, not `project_id`.

The schema includes or plans the following tables:

* **`Users`** — `id` primary key; case-insensitively unique `username`;
  `password_hash`, `display_name`, constrained `role`, constrained `is_active`,
  `last_login_at`, and create/update timestamps and user references. `created_by` and
  `updated_by` reference `Users.id` and may be null for the bootstrap administrator.
* **`AuditLog`** — append-only events keyed by `id`, with `occurred_at`, optional
  `actor_user_id` and `subject_user_id` references to `Users.id`, `action`, and
  `details_json`.
* **`Techs`** — keyed internally by `tech_id`, with case-insensitively unique
  `tech_code`, complete technician identity, contact, engagement, status, emergency
  contact, limited compliance, notes, and create/update audit fields. User references
  target `Users.id`.
* **`TechAddresses`** — keyed internally by `address_id`; `tech_id` references
  `Techs.tech_id`; address, effective period, constrained `is_primary`, and
  create/update audit fields.
* **`Projects`** — optional grouping for a broader customer engagement that may require
  multiple separately scheduled jobs over time.
* **`Jobs`** — one row per scheduled Matterport assignment or capture visit. A recurring
  weekly engagement therefore creates multiple Jobs linked to one Project.
* **`JobSourceRecords`** — one row per imported OpenTable report row. Multiple source
  records may belong to one Job, including component rows and a parent compensation row.
* **`JobAssignments`** — historical technician assignments for a Job, including the
  active primary assignment and any prior, replacement, or supporting technicians.
* **`SchemaMigrations`** — migration `name` primary key and `applied_at` timestamp.

Planned Tipalti reconciliation tables are described later in this document but must not
be treated as final until an actual Tipalti export has been inspected.

The exact implemented columns, defaults, checks, and foreign keys must remain
synchronized with [`001_initial.sql`](001_initial.sql) and all forward migrations.

---

# Table: `Techs`

## Purpose

Stores the identity, contact, engagement, status, emergency-contact, limited compliance,
and administrative information for Matterport technicians and contractors.

The internal primary key `tech_id` is used by the application and foreign-key
relationships. It must not be displayed to users through technician lists, forms,
details windows, reports, or exported operational documents unless a specific technical
diagnostic requires it.

The user-facing technician identifier is `tech_code`.

## Definition

```sql
CREATE TABLE Techs (
    tech_id                         INTEGER PRIMARY KEY AUTOINCREMENT,

    tech_code                       TEXT NOT NULL COLLATE NOCASE UNIQUE,

    first_name                      TEXT NOT NULL,
    middle_name                     TEXT,
    last_name                       TEXT NOT NULL,
    suffix                          TEXT,
    preferred_name                  TEXT,

    company_name                    TEXT,
    contractor_type                 TEXT,

    status                          TEXT NOT NULL DEFAULT 'Active',
    inactive_reason                 TEXT,

    date_of_birth                   TEXT,
    ssn_last4                       TEXT,

    drivers_license_number          TEXT,
    drivers_license_state           TEXT,

    email                           TEXT,
    alternate_email                 TEXT,

    mobile_phone                    TEXT,
    home_phone                      TEXT,
    work_phone                      TEXT,

    emergency_contact_name          TEXT,
    emergency_contact_relationship  TEXT,
    emergency_contact_phone         TEXT,

    hire_date                       TEXT,
    termination_date                TEXT,

    notes                           TEXT,
    notes_private                   TEXT,

    created_at                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by                      INTEGER NOT NULL,
    updated_at                      TEXT,
    updated_by                      INTEGER,

    FOREIGN KEY (created_by) REFERENCES Users(id),
    FOREIGN KEY (updated_by) REFERENCES Users(id)
);
```

## Fields

| Field | Type | Required | Default | Description |
|---|---|---:|---|---|
| `tech_id` | INTEGER | Yes | Auto-generated | Internal technician primary key. Never displayed as a normal user-facing form field or list column. |
| `tech_code` | TEXT | Yes | None | Case-insensitively unique operational technician code. This is the visible technician identifier. |
| `first_name` | TEXT | Yes | None | Technician's first name. |
| `middle_name` | TEXT | No | NULL | Technician's middle name or initial. |
| `last_name` | TEXT | Yes | None | Technician's last name. |
| `suffix` | TEXT | No | NULL | Name suffix such as Jr., Sr., II, III, or IV. |
| `preferred_name` | TEXT | No | NULL | Name the technician prefers to use in normal operations. |
| `company_name` | TEXT | No | NULL | Company or legal business name associated with the technician. |
| `contractor_type` | TEXT | No | NULL | Classification of the technician's working relationship. Controlled interface values should be used when practical. |
| `status` | TEXT | Yes | `Active` | Current technician status. Operational values are normally `Active` and `Inactive`. |
| `inactive_reason` | TEXT | No | NULL | Reason the technician became inactive. |
| `date_of_birth` | TEXT | No | NULL | Technician's date of birth, normally stored as `YYYY-MM-DD`. Sensitive personal information. |
| `ssn_last4` | TEXT | No | NULL | Last four digits only of the technician's Social Security number. A complete SSN must never be stored in this field. |
| `drivers_license_number` | TEXT | No | NULL | Technician's driver's-license number. Sensitive information requiring restricted display. |
| `drivers_license_state` | TEXT | No | NULL | State or issuing jurisdiction for the driver's license. |
| `email` | TEXT | No | NULL | Primary email address. |
| `alternate_email` | TEXT | No | NULL | Secondary email address. |
| `mobile_phone` | TEXT | No | NULL | Mobile telephone number. |
| `home_phone` | TEXT | No | NULL | Home telephone number. |
| `work_phone` | TEXT | No | NULL | Work or company telephone number. |
| `emergency_contact_name` | TEXT | No | NULL | Name of the technician's emergency contact. |
| `emergency_contact_relationship` | TEXT | No | NULL | Emergency contact's relationship to the technician. |
| `emergency_contact_phone` | TEXT | No | NULL | Emergency contact's telephone number. |
| `hire_date` | TEXT | No | NULL | Date the technician began working with the organization, normally `YYYY-MM-DD`. |
| `termination_date` | TEXT | No | NULL | Date the technician's engagement ended, normally `YYYY-MM-DD`. |
| `notes` | TEXT | No | NULL | General operational notes suitable for normal administrative viewing. |
| `notes_private` | TEXT | No | NULL | Restricted administrative notes. This field must not be shown to non-administrators. |
| `created_at` | TEXT | Yes | `CURRENT_TIMESTAMP` | Timestamp when the technician record was created. |
| `created_by` | INTEGER | Yes | None | Internal reference to the user who created the record. Do not display the numeric ID. |
| `updated_at` | TEXT | No | NULL | Timestamp of the most recent update. |
| `updated_by` | INTEGER | No | NULL | Internal reference to the user who most recently updated the record. Do not display the numeric ID. |

## Keys and constraints

* Primary key: `tech_id`
* User-facing identifier: `tech_code`
* Unique constraint: `tech_code`
* `tech_code` uses `COLLATE NOCASE`.
* `created_by` references `Users.id`.
* `updated_by` references `Users.id`.
* `status` should be managed through the application rather than entered as unrestricted text.
* `ssn_last4` should contain exactly four digits when populated.
* `drivers_license_state` should normally contain a two-character state abbreviation when applicable.
* Dates should normally use `YYYY-MM-DD`.

## Interface visibility rules

The following fields are internal and must not appear as editable or visible user-facing
fields:

```text
tech_id
created_by
updated_by
```

The following audit timestamps may appear in an administrative audit or details panel,
but they should not be part of the normal Add/Edit Technician form:

```text
created_at
updated_at
```

The following fields contain sensitive or restricted information:

```text
date_of_birth
ssn_last4
drivers_license_number
drivers_license_state
emergency_contact_name
emergency_contact_relationship
emergency_contact_phone
notes_private
```

These fields should be visible and editable only by authorized administrators. They
should not appear in the main technician list.

The main technician list should use concise, meaningful columns such as:

```text
Tech Code
Name
Preferred Name
Company
Contractor Type
Status
Primary Email
Mobile Phone
Hire Date
```

It must not display `tech_id`.

## Record-retention rule

Technicians are not physically deleted during normal operation.

The application changes their status between `Active` and `Inactive`. When a technician
becomes inactive, the interface should capture `termination_date` and
`inactive_reason` when applicable.

The technician record remains available for historical jobs, payments, assignments,
auditing, and reporting.

---

# Table: `Projects`

## Purpose

Stores a broader customer engagement that may contain one or many separately scheduled
Jobs.

A Project is useful for recurring or multi-visit work such as weekly construction
progress capture. For example, a six-week LensCrafters construction engagement is one
Project containing six separately scheduled Jobs.

A Project is optional. A routine one-time Matterport assignment may exist without a
Project relationship. The system must not force a user to create a Project before a
single Job can be imported or entered.

## Proposed definition

```sql
CREATE TABLE Projects (
    project_id                   INTEGER PRIMARY KEY AUTOINCREMENT,

    project_code                 TEXT COLLATE NOCASE UNIQUE,
    project_name                 TEXT NOT NULL,

    client_name                  TEXT,

    project_status               TEXT NOT NULL DEFAULT 'Active',
    project_type                 TEXT,
    capture_frequency            TEXT,

    site_address_1               TEXT,
    site_address_2               TEXT,
    site_city                    TEXT,
    site_state                   TEXT,
    site_postal_code             TEXT,
    site_county                  TEXT,
    site_country                 TEXT,

    project_start_date           TEXT,
    project_end_date             TEXT,

    project_notes                TEXT,

    created_at                   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by                   INTEGER NOT NULL,
    updated_at                   TEXT,
    updated_by                   INTEGER,

    FOREIGN KEY (created_by) REFERENCES Users(id),
    FOREIGN KEY (updated_by) REFERENCES Users(id)
);
```

## Design rules

* `project_id` is internal and must not appear in normal forms, lists, reports, or exports.
* `project_code`, when used, is the visible project identifier.
* `project_name` is required.
* `project_status`, `project_type`, and `capture_frequency` should use controlled
  application values when those vocabularies are formally defined.
* A Project can have zero or more Jobs.
* A Project must not be automatically merged with another Project solely because names
  or addresses appear similar.
* Imported names and addresses must remain preserved on the Job even after the Job is
  linked to a normalized Project.

## Suggested indexes

```sql
CREATE INDEX idx_Projects_status
    ON Projects(project_status);

CREATE INDEX idx_Projects_name
    ON Projects(project_name COLLATE NOCASE);

CREATE INDEX idx_Projects_location
    ON Projects(site_state, site_city, site_postal_code);
```

---

# Table: `Jobs`

## Purpose

Stores one separately scheduled Matterport assignment or capture visit.

The OpenTable/Matterport `Job ID` represents this scheduled assignment. It does not
represent the complete recurring Project. Multiple weekly visits to the same location
therefore create multiple Jobs, each with a different external Job ID, while all may be
linked to one Project.

One Job may contain multiple imported source records. For example, the OpenTable export
may include both a descriptive component row and a separate `Parent Record` row carrying
the technician compensation amount.

## Proposed definition

```sql
CREATE TABLE Jobs (
    job_id                       INTEGER PRIMARY KEY AUTOINCREMENT,

    project_id                   INTEGER,

    external_job_id              TEXT NOT NULL COLLATE NOCASE UNIQUE,

    project_name_source          TEXT,
    client_name_source           TEXT,

    job_status                   TEXT NOT NULL DEFAULT 'Requested',

    request_received_at          TEXT,
    scheduled_start_at           TEXT,
    actual_start_at              TEXT,
    completed_at                 TEXT,
    cancelled_at                 TEXT,

    capture_address_raw          TEXT,

    address_1                    TEXT,
    address_2                    TEXT,
    city                         TEXT,
    state                        TEXT,
    postal_code                  TEXT,
    county                       TEXT,
    country                      TEXT,

    requested_capture_size       NUMERIC,

    additional_details           TEXT,
    scheduling_link              TEXT,
    floor_plan_attachments       TEXT,

    onsite_contact_name          TEXT,
    onsite_contact_email         TEXT,
    onsite_contact_phone         TEXT,

    preferred_datetime_1         TEXT,
    preferred_datetime_2         TEXT,
    alternate_datetime_1         TEXT,
    alternate_datetime_2         TEXT,
    alternate_datetime_3         TEXT,

    cancellation_reason          TEXT,
    internal_notes               TEXT,

    created_at                   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by                   INTEGER NOT NULL,
    updated_at                   TEXT,
    updated_by                   INTEGER,

    FOREIGN KEY (project_id) REFERENCES Projects(project_id),
    FOREIGN KEY (created_by) REFERENCES Users(id),
    FOREIGN KEY (updated_by) REFERENCES Users(id)
);
```

## Source-field mapping

| OpenTable report field | Jobs destination |
|---|---|
| `Request Date/Time` | `request_received_at` |
| `MP Client.` | `client_name_source` |
| `Job ID` | `external_job_id` |
| `Project Name` | `project_name_source` |
| `Scheduling Link` | `scheduling_link` |
| `Job Status` | `job_status` |
| `Job Scheduled Date/Time` | `scheduled_start_at` |
| `Capture Address` | `capture_address_raw` and parsed address fields |
| `Capture Size - Requested` | `requested_capture_size`, normally using the parent or authoritative source row |
| `Additional Details` | `additional_details` |
| `Floor Plans/Attachments` | `floor_plan_attachments` until an attachment subsystem is implemented |
| On-site contact fields | corresponding `onsite_contact_*` fields |
| Preferred and alternate dates | corresponding preferred/alternate datetime fields |

The original imported text must be preserved even after normalization. For example,
`client_name_source`, `project_name_source`, and `capture_address_raw` retain what the
source system supplied.

## Keys and constraints

* Primary key: `job_id`
* User-facing identifier: `external_job_id`
* Unique constraint: `external_job_id`
* `project_id` is optional and references `Projects.project_id`.
* `created_by` and `updated_by` reference `Users.id`.
* `job_status` must remain separate from technician-assignment status and payment status.
* Dates and timestamps should use ISO-compatible values.

## Interface visibility rules

Do not display these internal identifiers in normal user-facing screens:

```text
job_id
project_id
created_by
updated_by
```

The visible job identifier is `external_job_id`.

A normal Jobs list should favor concise operational columns such as:

```text
External Job ID
Scheduled Date/Time
Project Name
Client
Location
Job Status
Primary Technician
Expected Payout
Payment Status
```

Payment columns may remain unavailable until the Tipalti reconciliation layer is
implemented.

## Suggested indexes

```sql
CREATE INDEX idx_Jobs_project_id
    ON Jobs(project_id);

CREATE INDEX idx_Jobs_status
    ON Jobs(job_status);

CREATE INDEX idx_Jobs_scheduled_start
    ON Jobs(scheduled_start_at);

CREATE INDEX idx_Jobs_location
    ON Jobs(state, city, postal_code);
```

---

# Table: `JobSourceRecords`

## Purpose

Preserves every individual row imported from the OpenTable report.

This table is required because one external Job ID may appear on multiple report rows.
A Job can contain a descriptive capture component, a `Parent Record`, separate rate or
travel lines, and separate AP invoice references. Flattening those rows into `Jobs`
would discard source detail and make later Tipalti reconciliation unreliable.

## Proposed definition

```sql
CREATE TABLE JobSourceRecords (
    job_source_record_id         INTEGER PRIMARY KEY AUTOINCREMENT,

    job_id                       INTEGER NOT NULL,

    source_system                TEXT NOT NULL DEFAULT 'OpenTable',
    external_record_number       TEXT NOT NULL,

    record_description           TEXT,
    is_parent_record             INTEGER NOT NULL DEFAULT 0
                                     CHECK (is_parent_record IN (0,1)),

    requested_capture_size       NUMERIC,

    ct_rate                      NUMERIC NOT NULL DEFAULT 0,
    ct_travel_payout             NUMERIC NOT NULL DEFAULT 0,
    ct_off_hours_payout          NUMERIC NOT NULL DEFAULT 0,

    ap_invoice_number            TEXT,

    imported_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_file_name             TEXT,
    source_row_number            INTEGER,

    created_at                   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (job_id) REFERENCES Jobs(job_id),

    UNIQUE (source_system, external_record_number)
);
```

## Source-field mapping

| OpenTable report field | JobSourceRecords destination |
|---|---|
| `Record Number` | `external_record_number` |
| `Floor/Unit/Suite` | `record_description` |
| `Capture Size - Requested` | `requested_capture_size` |
| `CT Rate` | `ct_rate` |
| `CT Travel Payout` | `ct_travel_payout` |
| `CT Off Hours Payout` | `ct_off_hours_payout` |
| `AP Invoice Number` | `ap_invoice_number` |

`record_description` is intentionally broader than a literal floor/unit/suite field
because the source contains values such as `Parent Record`, `Entire Home`, `Exterior
Capture`, `Clubhouse`, and `Full Store`.

`is_parent_record` should be derived when the imported description unambiguously equals
the source system's parent-record designation. The original description must still be
preserved.

## Keys and constraints

* Primary key: `job_source_record_id`
* `job_id` references `Jobs.job_id`.
* `(source_system, external_record_number)` is unique.
* `ap_invoice_number` should be indexed but must not initially be declared globally
  unique until an actual Tipalti export confirms its behavior.
* Currency values are stored as numeric amounts in U.S. dollars.
* The imported source filename and row number support traceability and repeatable imports.

## Interface visibility rules

`job_source_record_id` and `job_id` are internal and must not be displayed as normal
form fields or list columns.

The user-facing source identifier is `external_record_number`.

## Suggested indexes

```sql
CREATE INDEX idx_JobSourceRecords_job_id
    ON JobSourceRecords(job_id);

CREATE INDEX idx_JobSourceRecords_ap_invoice
    ON JobSourceRecords(ap_invoice_number);
```

---

# Table: `JobAssignments`

## Purpose

Stores technician assignment history for each Job.

The technician is assigned to the separately scheduled Job, not merely to the broader
Project. This permits different technicians to perform different weekly visits, allows
reassignment without losing history, and supports multiple technicians on a large job.

## Proposed definition

```sql
CREATE TABLE JobAssignments (
    job_assignment_id           INTEGER PRIMARY KEY AUTOINCREMENT,

    job_id                      INTEGER NOT NULL,
    tech_id                     INTEGER NOT NULL,

    assignment_role             TEXT NOT NULL DEFAULT 'Primary',
    assignment_status           TEXT NOT NULL DEFAULT 'Assigned',

    assigned_at                 TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    accepted_at                 TEXT,
    declined_at                 TEXT,
    completed_at                TEXT,
    unassigned_at               TEXT,

    assigned_by                 INTEGER,
    assignment_notes            TEXT,

    created_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TEXT,

    FOREIGN KEY (job_id) REFERENCES Jobs(job_id),
    FOREIGN KEY (tech_id) REFERENCES Techs(tech_id),
    FOREIGN KEY (assigned_by) REFERENCES Users(id)
);
```

## Assignment behavior

* A Job may have zero or more assignment records.
* Only one active Primary assignment may exist for a Job at a time.
* Historical assignments remain after reassignment.
* Supporting technicians may be added without replacing the Primary technician.
* `assignment_status` must remain separate from `Jobs.job_status`.
* A technician record must not be physically deleted when historical assignments exist.

Suggested controlled assignment statuses include:

```text
Assigned
Accepted
Declined
Completed
Cancelled
Unassigned
```

The final controlled vocabulary must be implemented consistently in the service and UI.

## Active-primary constraint

```sql
CREATE UNIQUE INDEX ux_JobAssignments_active_primary
ON JobAssignments(job_id)
WHERE assignment_role = 'Primary'
  AND unassigned_at IS NULL;
```

## Interface visibility rules

Do not display:

```text
job_assignment_id
job_id
tech_id
assigned_by
```

The interface should identify the Job by `external_job_id` and the technician by
`tech_code` and name.

## Suggested indexes

```sql
CREATE INDEX idx_JobAssignments_job_id
    ON JobAssignments(job_id);

CREATE INDEX idx_JobAssignments_tech_id
    ON JobAssignments(tech_id);

CREATE INDEX idx_JobAssignments_status
    ON JobAssignments(assignment_status);
```

---

# Planned Tipalti reconciliation layer

The final Tipalti schema must not be implemented solely from assumptions. An actual
Tipalti export must first be inspected to determine:

* the external payment identifier;
* payee identifier behavior;
* whether one payment can contain multiple AP invoice lines;
* whether one AP invoice can be split across payments;
* status vocabulary;
* transaction and settlement dates;
* adjustment, rejection, hold, and reversal behavior.

The approved relationship requirement is:

```text
Project
  -> Job
       -> JobSourceRecords
       -> JobAssignments
       -> future JobPayouts
            -> future payout/source-record allocations
```

A future payout record must be able to relate at minimum to:

```text
job_id
tech_id
Tipalti payment identifier
Tipalti payee identifier
AP invoice number or source-record allocation
expected amount
paid amount
payout status
reconciliation status
submitted, approved, and paid timestamps
```

Operational, assignment, payout, and reconciliation statuses must remain separate. A
single Job may legitimately have:

```text
Job status: Complete
Assignment status: Completed
Payout status: Submitted
Reconciliation status: Missing from Tipalti
```

No final Tipalti column names or unique constraints should be added until the source
export is reviewed.

---

# Import and project-grouping rules

The initial OpenTable report showed that one external Job ID may produce multiple source
rows. The importer must therefore:

1. create or update one `Jobs` row for each distinct external Job ID;
2. create or update one `JobSourceRecords` row for each distinct source-system record
   number;
3. preserve all imported source values;
4. resolve the source technician name to a `Techs.tech_id` when the match is reliable;
5. create a `JobAssignments` record for the resolved technician;
6. place uncertain or unmatched technician names into a review process rather than
   silently creating or guessing a technician;
7. be idempotent so that re-importing the same source file does not duplicate Jobs,
   source records, or assignments;
8. retain the source filename and row number for traceability.

Recurring Projects must not be automatically merged solely through exact or fuzzy text
matching. Project names and addresses may vary between weekly Jobs, and source data may
contain typographical errors. The importer may suggest possible Project groupings, but a
person must be able to confirm or correct uncertain matches.

---

# Index summary

SQLite supplies unique indexes for `Users.username`, `Techs.tech_code`,
`Projects.project_code` when populated, and `Jobs.external_job_id`.

Current explicit indexes include:

```text
idx_AuditLog_occurred_at
idx_AuditLog_actor_user_id
idx_AuditLog_subject_user_id
idx_Techs_status
idx_Techs_name
idx_TechAddresses_tech_id
ux_TechAddresses_primary
```

The approved job-schema design adds:

```text
idx_Projects_status
idx_Projects_name
idx_Projects_location
idx_Jobs_project_id
idx_Jobs_status
idx_Jobs_scheduled_start
idx_Jobs_location
idx_JobSourceRecords_job_id
idx_JobSourceRecords_ap_invoice
idx_JobAssignments_job_id
idx_JobAssignments_tech_id
idx_JobAssignments_status
ux_JobAssignments_active_primary
```

The partial unique address index permits no more than one primary address for each
technician. The partial unique assignment index permits no more than one active Primary
technician assignment for each Job.

---

# Relationships and migrations

Users create/update users, technicians, projects, jobs, and assignments and may act in
audit events. A technician has zero or more addresses and zero or more job assignments.
A Project has zero or more Jobs. A Job has zero or more source records and zero or more
assignment-history records.

Foreign-key enforcement is enabled on every application connection.

`TechAddresses.address_id`, `TechAddresses.tech_id`, `Projects.project_id`,
`Jobs.job_id`, `Jobs.project_id`, `JobSourceRecords.job_source_record_id`,
`JobSourceRecords.job_id`, `JobAssignments.job_assignment_id`,
`JobAssignments.job_id`, and `JobAssignments.tech_id` are internal relationship values.
They must not be displayed as normal form fields or list columns.

Numbered forward-only migrations run in filename order. Each migration and its
`SchemaMigrations` record commit together; failures roll back and are not recorded.
New Projects and Jobs tables must be added through numbered migrations and reflected in
`001_initial.sql` for fresh databases.

Each migration must preserve existing IDs and records, validate row counts where tables
are rebuilt, and require `PRAGMA foreign_key_check` to return no rows.

Resolved historical issues include lowercase authentication table names,
`username_key`, PascalCase technician fields, invalid `Users(UserID)` references,
missing audit storage/indexes, and unenforced primary-address uniqueness.
