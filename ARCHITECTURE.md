# Matterport Ops Architecture

## Purpose

Matterport Ops is an internal operations system for maintaining the people and work records required to manage Matterport capture assignments.

The immediate objective is to provide a reliable repository for:

- technicians;
- jobs;
- technician assignments;
- imported OpenTable job records.

The system is being developed in short, focused sprints. Features that are not required to establish the technician and job repository are intentionally deferred.

## Current operational core

### Technicians

The technician subsystem stores the people and companies that perform capture work.

It is responsible for:

- technician identity and contact information;
- contractor and company information;
- active and inactive status;
- technician addresses;
- restricted compliance and administrative information;
- historical availability for jobs, assignments, payments, and reporting.

Technicians are identified to users by `tech_code`. Internal database identifiers such as `tech_id` are not displayed in normal application screens.

### Projects

A Project represents a broader customer engagement that may contain multiple separately scheduled Jobs.

Example:

```text
LensCrafters construction progress documentation
    -> Week 1 capture
    -> Week 2 capture
    -> Week 3 capture
    -> Week 4 capture
```

Projects are optional. A normal one-time Job may exist without first creating a Project.

### Jobs

A Job represents one separately scheduled Matterport assignment or site visit.

Each Job stores the operational information needed to identify and manage that assignment, including:

- external Matterport/OpenTable Job ID;
- client and project names received from the source system;
- scheduled date and time;
- capture address;
- job status;
- requested capture size;
- contact and scheduling details;
- operational notes.

For recurring work, each visit is a separate Job linked to the same Project.

The user-facing Job identifier is `external_job_id`. The internal `job_id` is used only for database relationships and application operations.

### Job source records

One OpenTable Job may appear on multiple rows in an export. These rows may represent a component, a parent record, a payout-bearing row, travel, off-hours work, or another source-system detail.

`JobSourceRecords` preserves every imported OpenTable row without flattening or discarding source information.

This provides:

- import traceability;
- preservation of Record Numbers and AP Invoice Numbers;
- support for jobs containing multiple capture components;
- a reliable foundation for later payment reconciliation.

### Job assignments

`JobAssignments` connects technicians to individual Jobs.

Assignments are attached to the scheduled Job rather than only to the broader Project because:

- different visits may be performed by different technicians;
- a Job may be reassigned;
- large Jobs may require multiple technicians;
- assignment history must be preserved.

The assignment model keeps Job status and assignment status separate.

## Core relationship model

```text
Technician
    -> Job Assignment
         -> Job
              -> optional Project
              -> one or more Job Source Records
```

A recurring engagement is represented as:

```text
Project
    -> Job 1
         -> Job Source Records
         -> Technician Assignment
    -> Job 2
         -> Job Source Records
         -> Technician Assignment
    -> Job 3
         -> Job Source Records
         -> Technician Assignment
```

## Immediate sprint scope

The first usable release should provide:

1. a working technician repository;
2. database tables for Projects, Jobs, Job Source Records, and Job Assignments;
3. an OpenTable CSV importer;
4. technician matching from the imported `CT Name` value;
5. review handling for unmatched or uncertain technician names;
6. a Jobs Manager list;
7. a Job detail view;
8. technician assignment and reassignment;
9. basic search and filtering.

The Jobs Manager should initially support searching or filtering by:

- external Job ID;
- scheduled date;
- project name;
- client;
- location;
- job status;
- assigned technician.

## Deferred capabilities

The following capabilities are outside the immediate sprint and will be added later:

- Tipalti payment reconciliation;
- Airtable integration;
- Gmail order parsing;
- Google Calendar automation;
- Matterport API integration;
- dashboards and advanced reporting;
- revenue and payout analytics;
- formal client management;
- advanced Project matching and grouping;
- automated communications;
- broader workflow automation.

Deferring these capabilities does not remove them from the design. The current schema preserves the source identifiers and relationships needed to support them later.

## Design principles

### Preserve source data

Imported source values must be retained even after normalization. Original client names, project names, addresses, Record Numbers, Job IDs, and AP Invoice Numbers must remain available for traceability.

### Separate operational concepts

The system must not overload one status field. These remain separate concepts:

- Job status;
- technician assignment status;
- future payout status;
- future reconciliation status.

### Hide internal identifiers

Internal database IDs are implementation details. User interfaces should display meaningful operational identifiers and names instead.

### Preserve history

Technicians, Jobs, assignments, and source records are not physically deleted during normal operations when they are needed for historical reporting or relationships. Status changes and reassignment records preserve the history.

### Import safely

Imports must be repeatable and idempotent. Re-importing the same source records must not create duplicate Jobs, Job Source Records, or assignments.

### Review uncertainty

The application may suggest technician matches or Project groupings, but uncertain matches must be reviewable. The system must not silently guess or merge records when the source data is ambiguous.

## Technical foundation

Matterport Ops currently uses:

- Python;
- Tkinter;
- SQLite;
- forward-only numbered database migrations;
- service classes between the user interface and database;
- role-based access for Admin, Operator, and Viewer users;
- audit logging for meaningful changes.

The database schema is documented in [`database/schema/schema.md`](database/schema/schema.md). That document is the detailed source for table definitions, fields, constraints, indexes, and migration requirements.

## Near-term development sequence

```text
Technician repository
    -> Jobs database migration
    -> Job services
    -> OpenTable importer
    -> Technician matching and review
    -> Jobs Manager
    -> Job details and assignments
    -> Import verification
```

This architecture document is a guardrail, not a separate development phase. The priority is to establish a working technician and job repository quickly, then add integrations, financial controls, and reporting in later sprints.
