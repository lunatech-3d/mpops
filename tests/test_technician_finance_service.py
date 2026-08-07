"""Technician-specific job balance and payment-history reporting tests."""

from app.services.technician_finance_service import TechnicianFinanceService
from app.services.technician_payment_service import TechnicianPaymentService
from tests.test_compensation_service import CompensationServiceTests


class TechnicianFinanceServiceTests(CompensationServiceTests):
    def setUp(self):
        super().setUp()
        self.finance = TechnicianFinanceService(self.auth)

    def test_job_is_visible_and_pending_earning_is_not_balance_due(self):
        earning_id = self.service.generate_technician_earnings(
            self.session, self.batch)["earning_ids"][0]
        jobs = self.finance.list_jobs(self.tech)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["finance_status"], "Pending Review")
        summary = self.finance.get_summary(self.tech)
        self.assertEqual(summary["balance_due_cents"], 0)
        self.assertEqual(summary["pending_cents"], 71)

    def test_approved_earning_is_due_then_payment_history_names_job(self):
        earning_id = self.service.generate_technician_earnings(
            self.session, self.batch)["earning_ids"][0]
        self.service.approve_technician_earning(self.session, earning_id)
        self.assertEqual(self.finance.get_summary(self.tech)["balance_due_cents"], 71)
        self.assertEqual(self.finance.list_jobs(self.tech, "Owed")[0]["approved_due_cents"], 71)
        payments = TechnicianPaymentService(self.auth)
        run = payments.create_payment_run(self.session, [earning_id])
        run = payments.approve_payment_run(self.session, run["technician_payment_run_id"])
        payment_id = run["payments"][0]["technician_payment_id"]
        payments.record_technician_payment(self.session, payment_id, payment_date="2026-08-07",
                                           payment_method="ACH", actual_amount_cents=71)
        self.assertEqual(self.finance.get_summary(self.tech)["balance_due_cents"], 0)
        history = self.finance.list_payments(self.tech)
        self.assertEqual(history[0]["payment_status"], "Paid")
        self.assertEqual(history[0]["jobs"][0]["external_job_id"], "JOB-1")
        self.assertEqual(history[0]["jobs"][0]["amount_applied_cents"], 71)

    def test_unknown_technician_and_filter_are_rejected(self):
        with self.assertRaises(LookupError): self.finance.get_summary(999999)
        with self.assertRaises(ValueError): self.finance.list_jobs(self.tech, "Maybe")

    def test_direct_reimbursement_is_not_attached_to_a_job_and_pending_is_not_paid(self):
        payments = TechnicianPaymentService(self.auth)
        item = payments.create_direct_payment(self.session, technician_id=self.tech,
            payment_date="2026-08-07", category="Expense reimbursement", amount_cents=2500,
            description="Replacement tablet charging cord", status="Approved", payment_method="ACH")
        self.assertEqual(item["payment_kind"], "Direct")
        self.assertEqual(self.finance.get_summary(self.tech)["balance_due_cents"], 2500)
        self.assertEqual(self.finance.get_summary(self.tech)["total_paid_cents"], 0)
        detail = self.finance.list_payments(self.tech)[0]
        self.assertIsNone(detail["jobs"][0]["job_id"])
        self.assertEqual(detail["payment_category"], "Expense reimbursement")

    def test_paid_direct_travel_is_allocated_to_the_real_job_and_can_be_voided(self):
        payments = TechnicianPaymentService(self.auth)
        item = payments.create_direct_payment(self.session, technician_id=self.tech,
            payment_date="2026-08-07", category="Special travel payment", amount_cents=4000,
            description="Weekend travel", status="Paid", job_id=self.job,
            financial_component="Travel", payment_method="Check", reference="CHK-7")
        job = self.finance.list_jobs(self.tech)[0]
        self.assertEqual(job["paid_cents"], 4000)
        self.assertEqual(self.finance.get_summary(self.tech)["total_paid_cents"], 4000)
        history = self.finance.list_payments(self.tech)[0]
        self.assertEqual(history["jobs"][0]["job_id"], self.job)
        self.assertEqual(history["financial_component"], "Travel")
        payments.void_direct_payment(self.session, item["technician_payment_id"], "Entered twice")
        self.assertEqual(self.finance.get_summary(self.tech)["total_paid_cents"], 0)

    def test_cancelled_filter_includes_cancelled_assignment(self):
        with self.auth.connection() as connection:
            connection.execute("UPDATE Jobs SET job_status='Cancelled',cancelled_at='2026-08-07' WHERE job_id=?", (self.job,))
        self.assertEqual(self.finance.list_jobs(self.tech, "Cancelled")[0]["job_id"], self.job)
