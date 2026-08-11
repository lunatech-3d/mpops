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
        self.assertEqual(jobs[0]["finance_status"], "Generated")
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

    def test_completed_job_without_ledger_uses_percentage_rule_read_only(self):
        with self.auth.connection() as connection:
            connection.execute("INSERT INTO JobFinancials(job_id,ct_rate,ct_travel_payout) "
                               "VALUES(?,?,?)", (self.job, "100.00", "25.00"))
            before = connection.execute("SELECT COUNT(*) FROM TechnicianJobEarnings").fetchone()[0]
        job = self.finance.list_jobs(self.tech)[0]
        self.assertEqual(job["earned_cents"], 8750)
        self.assertEqual(job["finance_status"], "Calculated—not generated")
        self.assertIsNone(job["base_pay_cents"])
        self.assertIsNone(job["paid_cents"])
        self.assertIsNone(job["approved_due_cents"])
        with self.auth.connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM TechnicianJobEarnings").fetchone()[0], before)

    def test_component_rules_include_travel_and_off_hours_once(self):
        with self.auth.connection() as connection:
            connection.execute("INSERT INTO JobFinancials(job_id,ct_rate,ct_travel_payout,"
                               "ct_off_hours_payout) VALUES(?,?,?,?)",
                               (self.job, "100.00", "20.00", "10.00"))
            connection.execute("INSERT INTO TechnicianCompensationRules(scope_type,scope_id,"
                               "rule_type,rule_value,compensation_component,created_by) "
                               "VALUES('Technician',?,'Percentage',5000,'Travel',?)",
                               (self.tech, self.admin_id))
            connection.execute("INSERT INTO TechnicianCompensationRules(scope_type,scope_id,"
                               "rule_type,rule_value,compensation_component,created_by) "
                               "VALUES('Technician',?,'Flat Amount',300,'Off Hours',?)",
                               (self.tech, self.admin_id))
        job = self.finance.list_jobs(self.tech)[0]
        self.assertEqual(job["earned_cents"], 8300)
        self.assertEqual((job["base_pay_cents"], job["travel_pay_cents"],
                          job["off_hours_pay_cents"]), (7000, 1000, 300))

    def test_missing_rule_or_financial_data_is_not_reported_as_zero(self):
        job = self.finance.list_jobs(self.tech)[0]
        self.assertIsNone(job["earned_cents"])
        self.assertEqual(job["finance_status"], "Missing job financial data")
        with self.auth.connection() as connection:
            connection.execute("INSERT INTO JobFinancials(job_id,ct_rate) VALUES(?,?)",
                               (self.job, "100.00"))
            connection.execute("DELETE FROM TechnicianCompensationRules")
        job = self.finance.list_jobs(self.tech)[0]
        self.assertIsNone(job["earned_cents"])
        self.assertEqual(job["finance_status"], "No applicable compensation rule")

    def test_persisted_earning_precedes_changed_display_inputs(self):
        earning_id = self.service.generate_technician_earnings(
            self.session, self.batch)["earning_ids"][0]
        with self.auth.connection() as connection:
            connection.execute("INSERT INTO JobFinancials(job_id,ct_rate) VALUES(?,?)",
                               (self.job, "999.00"))
        job = self.finance.list_jobs(self.tech)[0]
        self.assertEqual(job["earned_cents"], 71)
        self.assertEqual(job["finance_status"], "Generated")
        self.assertEqual(earning_id > 0, True)

    def test_multiple_partial_allocations_do_not_duplicate_earned_amount(self):
        earning_id = self.service.generate_technician_earnings(
            self.session, self.batch)["earning_ids"][0]
        self.service.approve_technician_earning(self.session, earning_id)
        with self.auth.connection() as connection:
            for amount in (30, 20):
                run_id = connection.execute("""INSERT INTO TechnicianPaymentRuns
                  (payment_run_date,payment_status,total_amount_cents,created_by)
                  VALUES('2026-08-07','Paid',?,?)""", (amount, self.admin_id)).lastrowid
                payment_id = connection.execute("""INSERT INTO TechnicianPayments
                  (technician_payment_run_id,tech_id,payment_amount_cents,payment_status,
                   actual_amount_cents,payment_date) VALUES(?,?,?,'Paid',?,'2026-08-07')""",
                  (run_id, self.tech, amount, amount)).lastrowid
                connection.execute("""INSERT INTO TechnicianPaymentEarnings
                  (technician_payment_id,technician_earning_id,amount_applied_cents)
                  VALUES(?,?,?)""", (payment_id, earning_id, amount))
        job = self.finance.list_jobs(self.tech)[0]
        self.assertEqual(job["earned_cents"], 71)
        self.assertEqual(job["paid_cents"], 50)
        self.assertEqual(job["approved_due_cents"], 21)
        self.assertEqual(job["finance_status"], "Approved")

    def test_upcoming_and_cancelled_jobs_do_not_calculate(self):
        with self.auth.connection() as connection:
            connection.execute("INSERT INTO JobFinancials(job_id,ct_rate) VALUES(?,?)",
                               (self.job, "100.00"))
            connection.execute("UPDATE Jobs SET completed_at=NULL,job_status='Scheduled',"
                               "scheduled_start_at='2099-01-01' WHERE job_id=?", (self.job,))
        job = self.finance.list_jobs(self.tech)[0]
        self.assertIsNone(job["earned_cents"])

    def test_account_activity_mixes_job_expense_bonus_and_historical_payment(self):
        earning_id = self.service.generate_technician_earnings(
            self.session, self.batch)["earning_ids"][0]
        self.service.approve_technician_earning(self.session, earning_id)
        payments=TechnicianPaymentService(self.auth)
        parking=payments.create_direct_payment(self.session,technician_id=self.tech,
            payment_date="2026-08-08",category="Parking",amount_cents=2800,
            description="Downtown garage",status="Approved",job_id=self.job)
        payments.create_direct_payment(self.session,technician_id=self.tech,
            payment_date="2026-08-08",category="Bonus",amount_cents=7200,
            description="Great work",status="Approved")
        payments.create_manual_payment(self.session,technician_id=self.tech,
            payment_date="2026-08-09",amount_cents=4000,payment_method="Zelle",status="Paid",
            reference="ZELLE-ACCOUNT",allocations=[{"earning_id":earning_id,"amount_cents":71}],
            non_job_items=[{"type":"Advance","amount_cents":3929,"description":"Advance"}],
            historical=True,technician_confirmed=True)
        activity=self.finance.list_account_activity(self.tech)
        self.assertEqual(activity[-1]["activity_type"],"Zelle Payment")
        self.assertEqual(activity[-1]["payment_reference"],"ZELLE-ACCOUNT")
        self.assertEqual(activity[-1]["running_balance_cents"],6071)
        parking_row=next(x for x in activity if x["activity_type"]=="Parking")
        self.assertEqual(parking_row["external_job_id"],"JOB-1")
        self.assertTrue(any(x["activity_type"]=="Bonus" and x["job_id"] is None for x in activity))
        self.assertEqual(self.finance.get_summary(self.tech)["balance_due_cents"],6071)
        payments.reverse_payment(self.session,activity[-1]["source_record_id"],"Bank reversal")
        self.assertEqual(self.finance.get_summary(self.tech)["balance_due_cents"],10071)
        self.assertFalse(any(x["payment_reference"]=="ZELLE-ACCOUNT"
                             for x in self.finance.list_account_activity(self.tech)))

    def test_same_day_account_order_is_deterministic(self):
        payments=TechnicianPaymentService(self.auth)
        for category in ("Parking","Tolls"):
            payments.create_direct_payment(self.session,technician_id=self.tech,
                payment_date="2026-08-08",category=category,amount_cents=100,
                description=category,status="Approved")
        first=self.finance.list_account_activity(self.tech)
        second=self.finance.list_account_activity(self.tech)
        self.assertEqual([(x["source_record_type"],x["source_record_id"]) for x in first],
                         [(x["source_record_type"],x["source_record_id"]) for x in second])
        with self.auth.connection() as connection:
            connection.execute("UPDATE Jobs SET job_status='Cancelled',cancelled_at='2026-08-07' "
                               "WHERE job_id=?", (self.job,))
        job = self.finance.list_jobs(self.tech)[0]
        self.assertIsNone(job["earned_cents"])
