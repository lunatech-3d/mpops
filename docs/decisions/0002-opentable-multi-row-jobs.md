# 0002: Separate OpenTable financial rows from Jobs

## Status

Accepted.

## Context

Multiple OpenTable rows can share one external Job ID. These rows are not duplicate
operational jobs: each can carry a distinct AP invoice number and CT payout values.
Treating the first row as the complete Job lost later invoice information.

## Decision

The importer groups rows by external Job ID and creates or updates one operational
`Jobs` record. It retains each source row in `JobSourceRecords` for lineage and writes
one corresponding record to `JobFinancials`. The source-record foreign key makes
re-imports idempotent while allowing any number of financial records per Job.

`Jobs` and `JobSourceRecords` do not own AP invoice numbers or CT payout amounts.
Payment reconciliation follows `PaymentItem.document_number` (the payment invoice
number) to `JobFinancials.ap_invoice_number`, then follows `JobFinancials.job_id` to
the operational Job. Multiple financial rows for the same Job are deduplicated during
matching; the same invoice on different Jobs remains an ambiguous match.
