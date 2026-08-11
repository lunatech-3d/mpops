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
        included=self.service.get_technician_earning(eid)
        self.assertEqual(included["included_in_payment_run_id"],run["technician_payment_run_id"])
        self.assertIsNotNone(included["included_in_payment_run_at"])
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
        released=self.service.get_technician_earning(pending)
        self.assertIsNone(released["included_in_payment_run_id"])
        self.assertIsNone(released["included_in_payment_run_at"])
        self.assertEqual(len(self.payments.list_approved_unpaid_earnings()),1)
        run=self.payments.create_payment_run(self.session,[pending]);rid=run["technician_payment_run_id"]
        self.payments.cancel_payment_run(self.session,rid)
        self.assertIsNone(self.service.get_technician_earning(pending)["included_in_payment_run_id"])
        self.assertEqual(len(self.payments.list_approved_unpaid_earnings()),1)

    def test_new_run_eligibility_requires_approved_unincluded_unpaid_unvoided(self):
        eligible=self.approved()
        paid=self.service.create_manual_earning_adjustment(self.session,self.tech,5,"paid")
        voided=self.service.create_manual_earning_adjustment(self.session,self.tech,6,"voided")
        included=self.service.create_manual_earning_adjustment(self.session,self.tech,7,"included")
        self.service.approve_technician_earnings(self.session,[paid,voided,included])
        with self.auth.connection() as c:
            dummy_run=c.execute("INSERT INTO TechnicianPaymentRuns(payment_status,total_amount_cents,created_by) "
                                "VALUES('Draft',0,?)",(self.admin_id,)).lastrowid
            c.execute("UPDATE TechnicianJobEarnings SET paid_at='2026-07-31' WHERE technician_earning_id=?",(paid,))
            c.execute("UPDATE TechnicianJobEarnings SET voided_at='2026-07-31' WHERE technician_earning_id=?",(voided,))
            c.execute("UPDATE TechnicianJobEarnings SET included_in_payment_run_id=? WHERE technician_earning_id=?",(dummy_run,included))
        self.assertEqual([row["technician_earning_id"] for row in
                          self.payments.list_approved_unpaid_earnings()],[eligible])

    def test_payment_csv_contains_no_sensitive_fields(self):
        eid=self.approved();run=self.payments.approve_payment_run(self.session,self.payments.create_payment_run(self.session,[eid])["technician_payment_run_id"])
        pid=run["payments"][0]["technician_payment_id"]
        self.payments.record_technician_payment(self.session,pid,payment_date="2026-07-31",payment_method="Check",payment_reference="CHK-1",actual_amount_cents=71)
        output=self.payments.export_payment_detail_csv(pid)
        self.assertIn("External Job ID",output);self.assertNotIn("SSN",output);self.assertNotIn("bank",output.lower())

    def test_fifo_uses_remaining_balances_and_partially_allocates_last(self):
        ids=[]
        for cents in (10000,15000,20000):
            eid=self.service.create_manual_earning_adjustment(self.session,self.tech,cents,"FIFO")
            self.service.approve_technician_earning(self.session,eid);ids.append(eid)
        self.payments.create_manual_payment(self.session,technician_id=self.tech,
            payment_date="2026-08-01",amount_cents=2500,payment_method="ACH",status="Paid",
            reference="FIFO-PARTIAL",allocations=[{"earning_id":ids[0],"amount_cents":2500}])
        proposed=self.payments.build_fifo_allocations(self.tech,30000)
        self.assertEqual(proposed,[{"earning_id":ids[0],"amount_cents":7500},
            {"earning_id":ids[1],"amount_cents":15000},
            {"earning_id":ids[2],"amount_cents":7500}])
        self.assertEqual(sum(x["amount_cents"] for x in proposed),30000)
        self.assertEqual(self.payments.list_outstanding_earnings(self.tech)[0]["balance_due_cents"],7500)

    def test_fifo_is_read_only_and_rejects_invalid_amount(self):
        eid=self.approved();before=self.payments.list_outstanding_earnings(self.tech)
        self.assertEqual(self.payments.build_fifo_allocations(self.tech,9999),
                         [{"earning_id":eid,"amount_cents":71}])
        self.assertEqual(self.payments.list_outstanding_earnings(self.tech),before)
        with self.assertRaises(ValueError):self.payments.build_fifo_allocations(self.tech,-1)


if __name__ == "__main__": unittest.main()
