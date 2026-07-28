Matterport Ops database schema

This document describes the reconciled production schema. The live SQLite database isC:/sqlite/mpops/database/mpops.db (overridable with MPOPS_DB_PATH). It is excludedfrom Git because it contains credentials and operational/personal data; only schema,migration, documentation, and test artifacts belong in the repository.

Conventions and tables

Table names are PascalCase and fields are lowercase snake_case.

Internal database record IDs are implementation details. They may be used internallyfor relationships, selections, service calls, auditing, and database operations, butthey must not normally be displayed through application forms, lists, details screens,reports, or operational exports.

User-facing records should instead be identified by meaningful business values. Fortechnicians, the visible operational identifier is tech_code, not tech_id.

Users — id primary key; case-insensitively unique username;password_hash, display_name, constrained role, constrained is_active,last_login_at, and create/update timestamps and user references. created_by andupdated_by reference Users.id and may be null for the bootstrap administrator.

AuditLog — append-only events keyed by id, with occurred_at, optionalactor_user_id and subject_user_id references to Users.id, action, anddetails_json.

Techs — keyed internally by tech_id, with case-insensitively uniquetech_code, complete technician identity, contact, engagement, status, emergencycontact, limited compliance, notes, and create/update audit fields. User referencestarget Users.id.

TechAddresses — keyed internally by address_id; tech_id referencesTechs.tech_id; address, effective period, constrained is_primary, andcreate/update audit fields.

SchemaMigrations — migration name primary key and applied_at timestamp.

The exact authoritative columns, defaults, checks, and foreign keys must remainsynchronized with 001_initial.sql and all forward migrations.

Table: Techs

Purpose

Stores the identity, contact, engagement, status, emergency-contact, limited compliance,and administrative information for Matterport technicians and contractors.

The internal primary key tech_id is used by the application and foreign-keyrelationships. It must not be displayed to users through technician lists, forms,details windows, reports, or exported operational documents unless a specific technicaldiagnostic requires it.

The user-facing technician identifier is tech_code.

Definition

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

Fields

Field

Type

Required

Default

Description

tech_id

INTEGER

Yes

Auto-generated

Internal technician primary key. Never displayed as a normal user-facing form field or list column.

tech_code

TEXT

Yes

None

Case-insensitively unique operational technician code. This is the visible technician identifier.

first_name

TEXT

Yes

None

Technician's first name.

middle_name

TEXT

No

NULL

Technician's middle name or initial.

last_name

TEXT

Yes

None

Technician's last name.

suffix

TEXT

No

NULL

Name suffix such as Jr., Sr., II, III, or IV.

preferred_name

TEXT

No

NULL

Name the technician prefers to use in normal operations.

company_name

TEXT

No

NULL

Company or legal business name associated with the technician.

contractor_type

TEXT

No

NULL

Classification of the technician's working relationship. Controlled interface values should be used when practical.

status

TEXT

Yes

Active

Current technician status. Operational values are normally Active and Inactive.

inactive_reason

TEXT

No

NULL

Reason the technician became inactive.

date_of_birth

TEXT

No

NULL

Technician's date of birth, normally stored as YYYY-MM-DD. Sensitive personal information.

ssn_last4

TEXT

No

NULL

Last four digits only of the technician's Social Security number. A complete SSN must never be stored in this field.

drivers_license_number

TEXT

No

NULL

Technician's driver's-license number. Sensitive information requiring restricted display.

drivers_license_state

TEXT

No

NULL

State or issuing jurisdiction for the driver's license.

email

TEXT

No

NULL

Primary email address.

alternate_email

TEXT

No

NULL

Secondary email address.

mobile_phone

TEXT

No

NULL

Mobile telephone number.

home_phone

TEXT

No

NULL

Home telephone number.

work_phone

TEXT

No

NULL

Work or company telephone number.

emergency_contact_name

TEXT

No

NULL

Name of the technician's emergency contact.

emergency_contact_relationship

TEXT

No

NULL

Emergency contact's relationship to the technician.

emergency_contact_phone

TEXT

No

NULL

Emergency contact's telephone number.

hire_date

TEXT

No

NULL

Date the technician began working with the organization, normally YYYY-MM-DD.

termination_date

TEXT

No

NULL

Date the technician's engagement ended, normally YYYY-MM-DD.

notes

TEXT

No

NULL

General operational notes suitable for normal administrative viewing.

notes_private

TEXT

No

NULL

Restricted administrative notes. This field must not be shown to non-administrators.

created_at

TEXT

Yes

CURRENT_TIMESTAMP

Timestamp when the technician record was created.

created_by

INTEGER

Yes

None

Internal reference to the user who created the record. Do not display the numeric ID.

updated_at

TEXT

No

NULL

Timestamp of the most recent update.

updated_by

INTEGER

No

NULL

Internal reference to the user who most recently updated the record. Do not display the numeric ID.

Keys and constraints

Primary key: tech_id

User-facing identifier: tech_code

Unique constraint: tech_code

tech_code uses COLLATE NOCASE.

created_by references Users.id.

updated_by references Users.id.

status should be managed through the application rather than entered as unrestricted text.

ssn_last4 should contain exactly four digits when populated.

drivers_license_state should normally contain a two-character state abbreviation when applicable.

Dates should normally use YYYY-MM-DD.

Indexes

CREATE INDEX idx_Techs_status
    ON Techs(status);

CREATE INDEX idx_Techs_name
    ON Techs(last_name, first_name);

SQLite also maintains the automatic unique index supporting tech_code.

Interface visibility rules

The following fields are internal and must not appear as editable or visible user-facingfields:

tech_id
created_by
updated_by

The following audit timestamps may appear in an administrative audit or details panel,but they should not be part of the normal Add/Edit Technician form:

created_at
updated_at

The following fields contain sensitive or restricted information:

date_of_birth
ssn_last4
drivers_license_number
drivers_license_state
emergency_contact_name
emergency_contact_relationship
emergency_contact_phone
notes_private

These fields should be visible and editable only by authorized administrators. Theyshould not appear in the main technician list.

The main technician list should use concise, meaningful columns such as:

Tech Code
Name
Preferred Name
Company
Contractor Type
Status
Primary Email
Mobile Phone
Hire Date

It must not display tech_id.

Record-retention rule

Technicians are not physically deleted during normal operation.

The application changes their status between:

Active
Inactive

When a technician becomes inactive, the interface should capture, when applicable:

termination_date
inactive_reason

The technician record remains available for historical jobs, payments, assignments,auditing, and reporting.

Indexes

SQLite supplies unique indexes for Users.username and Techs.tech_code. Explicitindexes are idx_AuditLog_occurred_at, idx_AuditLog_actor_user_id,idx_AuditLog_subject_user_id, idx_Techs_status, idx_Techs_name,idx_TechAddresses_tech_id, and partial unique ux_TechAddresses_primary. The latterpermits no more than one primary address for each technician.

Relationships and migrations

Users create/update users, technicians, and addresses and may act in audit events. Atechnician has zero or more addresses. Foreign-key enforcement is enabled on everyapplication connection.

TechAddresses.address_id and TechAddresses.tech_id are internal relationship values.They must not be displayed as normal form fields or list columns. Address forms shouldshow the meaningful address information while storing IDs invisibly for service callsand database relationships.

Numbered forward-only migrations run in filename order. Each migration and itsSchemaMigrations record commit together; failures roll back and are not recorded.The compatibility migration inspects actual legacy names, preserves IDs, hashes andrecords, rebuilds inconsistent tables, validates row counts, and requiresPRAGMA foreign_key_check to return no rows. A fresh schema records that compatibilitymigration immediately because it already has the final structure.

Resolved historical issues include lowercase authentication table names,username_key, PascalCase technician fields, invalid Users(UserID) references,missing audit storage/indexes, and unenforced primary-address uniqueness.