# Technician payment email drafts

Payment emails are reviewable drafts based on an already-recorded technician
payment and its allocations. Matterport Ops does not send the email automatically.

## Open a draft for a recorded payment

1. Open **Technician Details**.
2. Select **Finances**.
3. **Payment History** is shown when Finances opens. If you moved to **Account
   Ledger**, select **Payment History** again.
4. Select the current **Paid** payment (selecting one of its child job/allocation
   rows selects the same parent payment for this action).
5. Click **Generate Payment Email**, or **Regenerate Draft** when that payment
   already has an active draft.

The Technician Details tabs, in order, are **Jobs**, **Compensation**, **Finances**,
and **Profile**. There is no top-level Payments tab in Technician Details.

The action is disabled until an administrator or operator selects a current,
unreversed Paid payment. Draft, Approved, Scheduled, Cancelled, Failed, Voided,
and reversed payments cannot be used. Viewers may review Payment History but
cannot generate a draft.

An administrator or operator recording a paid payment in **Payments → Record
Technician Payment** can also leave **Generate payment email after recording**
selected to open the same review dialog. Both entry points use the recorded
payment and its authoritative allocations to produce the same deterministic
content.

## Draft contents

Each allocated job is identified first by its complete service address, followed
by service date, Job Code, and the allocated capture, travel, adjustment/other,
and total amounts. The address is built from the job's `address_1`, `address_2`,
`city`, `state`, and `postal_code` fields. When those structured fields are
incomplete, the preserved `capture_address_raw` value is displayed instead.

Job records use a multi-line layout so a long address can wrap without being
truncated or forcing every amount into an excessively wide table. A job with no
usable structured or raw address is labeled **Address not recorded** and retains
its Job Code. Reimbursements, bonuses, and other non-job items are shown in a
separate **Other payment items** section without an address placeholder.
