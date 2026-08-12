-- Reviewable technician remittance drafts.  A row records generation, not delivery.
CREATE TABLE IF NOT EXISTS TechnicianPaymentEmailDrafts (
    technician_payment_email_draft_id INTEGER PRIMARY KEY AUTOINCREMENT,
    technician_payment_id INTEGER NOT NULL,
    recipient_email TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    generated_by INTEGER NOT NULL,
    generation_number INTEGER NOT NULL DEFAULT 1,
    draft_status TEXT NOT NULL DEFAULT 'Draft Generated'
        CHECK (draft_status IN ('Draft Generated', 'Outdated')),
    FOREIGN KEY (technician_payment_id) REFERENCES TechnicianPayments(technician_payment_id),
    FOREIGN KEY (generated_by) REFERENCES Users(id)
);
CREATE INDEX IF NOT EXISTS idx_payment_email_drafts_payment
    ON TechnicianPaymentEmailDrafts(technician_payment_id, generated_at);

-- Batch finalization is the authorization control.  Promote only unpaid,
-- non-voided Matterport earnings left Pending by older releases.
UPDATE TechnicianJobEarnings
SET earning_status = 'Approved'
WHERE earning_status = 'Pending'
  AND payment_batch_id IS NOT NULL
  AND paid_at IS NULL
  AND voided_at IS NULL;

UPDATE CompanyRevenueAllocations
SET allocation_status = 'Approved'
WHERE allocation_status = 'Calculated'
  AND technician_earning_id IN (
      SELECT technician_earning_id
      FROM TechnicianJobEarnings
      WHERE earning_status = 'Approved' AND payment_batch_id IS NOT NULL
  );
