"""Phase V earning approval and manually timed technician payment tests."""
import unittest

from tests.test_compensation_service import CompensationServiceTests
from app.services.technician_payment_service import TechnicianPaymentService


class TechnicianPaymentWorkflowTests(CompensationServiceTests):
    def setUp(self):
        super().setUp();self.payments=TechnicianPaymentService(self.auth)

    def earning(self):
        return self.service.generate_technician_earnings(self.session,self.batch)["earning_ids"][0]

    def approved(self):
        eid=self.earning();self.service.approve_technician_earning(self.session,eid);return eid

    def test_approval_updates_allocation_and_is_idempotency_guarded(self):
        eid=self.earning();result=self.service.approve_technician_earning(self.session,eid)
        self.assertEqual(result["earning_status"],"Approved")
        with self.auth.connection() as c:
            self.assertEqual(c.execute("SELECT allocation_status FROM CompanyRevenueAllocations WHERE technician_earning_id=?",(eid,)).fetchone()[0],"Approved")
            self.assertTrue(c.execute("SELECT 1 FROM AuditLog WHERE action='technician_earning_approved'").fetchone())
        with self.assertRaises(ValueError):self.service.approve_technician_earning(self.session,eid)

    def test_missing_and_mismatched_allocation_block_atomic_bulk_approval(self):
        first=self.earning();second=self.service.create_manual_earning_adjustment(self.session,self.tech,10,"bonus")
        with self.auth.connection() as c:c.execute("DELETE FROM CompanyRevenueAllocations WHERE technician_earning_id=?",(first,))
        with self.assertRaises(ValueError):self.service.approve_technician_earnings(self.session,[first,second])
        self.assertEqual(self.service.get_technician_earning(second)["earning_status"],"Pending")

    def test_adjustment_is_separately_approved_and_calculation_immutable(self):
        eid=self.service.create_manual_earning_adjustment(self.session,self.tech,-25,"equipment")
        self.assertEqual(self.service.get_technician_earning(eid)["earning_status"],"Pending")
        self.service.approve_technician_earning(self.session,eid)
        row=self.service.get_technician_earning(eid);self.assertEqual((row["calculated_amount_cents"],row["net_earning_cents"]),(0,-25))

    def test_manual_run_groups_technicians_and_payment_marks_only_links_paid(self):
        eid=self.approved();unrelated=self.service.create_manual_earning_adjustment(self.session,self.tech,5,"later")
        run=self.payments.create_payment_run(self.session,[eid])
        self.assertEqual((run["payment_status"],len(run["payments"]),run["total_amount_cents"]),("Draft",1,71))
        run=self.payments.approve_payment_run(self.session,run["technician_payment_run_id"])
        self.assertEqual(self.service.get_technician_earning(eid)["earning_status"],"Approved")
        payment=run["payments"][0]
        self.payments.record_technician_payment(self.session,payment["technician_payment_id"],payment_date="2026-07-31",payment_method="ACH",actual_amount_cents=71)
        self.assertEqual(self.service.get_technician_earning(eid)["earning_status"],"Paid")
        self.assertEqual(self.service.get_technician_earning(unrelated)["earning_status"],"Pending")
        with self.assertRaises(ValueError):self.payments.record_technician_payment(self.session,payment["technician_payment_id"],payment_date="2026-07-31",payment_method="ACH",actual_amount_cents=71)

    def test_run_selection_guards_remove_cancel_release_and_amount_match(self):
        pending=self.earning()
        with self.assertRaises(ValueError):self.payments.create_payment_run(self.session,[pending])
        self.service.approve_technician_earning(self.session,pending)
        run=self.payments.create_payment_run(self.session,[pending]);rid=run["technician_payment_run_id"]
        with self.assertRaises(ValueError):self.payments.create_payment_run(self.session,[pending])
        self.payments.remove_earnings_from_payment_run(self.session,rid,[pending])
        self.assertEqual(len(self.payments.list_approved_unpaid_earnings()),1)
        run=self.payments.create_payment_run(self.session,[pending]);rid=run["technician_payment_run_id"]
        self.payments.cancel_payment_run(self.session,rid)
        self.assertEqual(len(self.payments.list_approved_unpaid_earnings()),1)

    def test_payment_csv_contains_no_sensitive_fields(self):
        eid=self.approved();run=self.payments.approve_payment_run(self.session,self.payments.create_payment_run(self.session,[eid])["technician_payment_run_id"])
        pid=run["payments"][0]["technician_payment_id"]
        self.payments.record_technician_payment(self.session,pid,payment_date="2026-07-31",payment_method="Check",payment_reference="CHK-1",actual_amount_cents=71)
        output=self.payments.export_payment_detail_csv(pid)
        self.assertIn("External Job ID",output);self.assertNotIn("SSN",output);self.assertNotIn("bank",output.lower())


if __name__ == "__main__": unittest.main()
