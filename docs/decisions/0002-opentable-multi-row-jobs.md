# 0002: Preserve OpenTable rows beneath a Job

## Status

Accepted as the import and reconciliation model. A dedicated billing table is deferred.

## Context and root cause

The OpenTable import was originally treated as if one CSV row represented one Job. The
Job ID is actually the identity of a scheduled assignment, and multiple report records
can share it. An early descriptive or zero-dollar row could therefore create the Job;
later rows were treated as duplicate Jobs and skipped. That lost parent-row rates,
travel/off-hours payouts, and AP invoice references needed for Tipalti reconciliation.

The observed rows are not duplicate scheduled jobs. Their distinct Record Numbers and
descriptions show that they are source-system detail records beneath one Job. They may
be descriptive capture components, a `Parent Record`, or billing/payout-bearing lines.
They should not be collapsed into a single winner because more than one invoice number
can legitimately be present.

## Decision

Group an export by external Job ID, create or update one `Jobs` row, and upsert every
CSV row into `JobSourceRecords` using its source Record Number. Parent records provide
the preferred operational summary. For the legacy `Jobs.ap_invoice_number` summary,
prefer an invoice from a payout-bearing row and then a parent row. All invoice numbers
remain authoritative on `JobSourceRecords`, and reconciliation searches both the Job
summary and source records while deduplicating matches that point to the same Job.

`CT Rate`, `CT Travel Payout`, and `CT Off Hours Payout` remain on
`JobSourceRecords`, not `Jobs`, because they describe individual source lines. The
singular `Jobs.ap_invoice_number` remains temporarily for compatibility with existing
forms and manually created Jobs, but it is only a representative value.

## Longer-term billing recommendation

Introduce `JobBilling` (with child invoice/line records if needed) when the application
must manage billing lifecycle rather than merely retain imported evidence. It should
represent invoice identity, status, dates, currency, adjustments, totals, and payment
links; it should relate many billing records to one Job and retain source-record lineage.
Do not migrate to it until real exports establish invoice and adjustment cardinality.

### Benefits

* Models multiple invoices, adjustments, partial payments, and status history directly.
* Separates operational scheduling from accounts-payable and compensation concerns.
* Provides stable reconciliation keys and avoids choosing one invoice for a Job.
* Allows monetary values to use integer cents and explicit currency semantics.

### Costs and risks

* Requires a migration and changes across import, forms, reporting, and reconciliation.
* Adds joins and lifecycle rules before the source system's exact semantics are known.
* Risks inventing incorrect invoice/adjustment relationships from report rows alone.

Until then, `JobSourceRecords` is the lossless boundary and `Jobs` should contain only
the compatibility invoice summary—not CT rate, travel payout, or off-hours payout.
