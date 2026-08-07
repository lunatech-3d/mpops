"""Read-only technician job, balance, and payment-history reporting."""

from __future__ import annotations

import json
from datetime import date

from app.security.auth import AuthService
from app.services.compensation_service import CompensationService
from app.services.revenue_rule_service import RuleConfigurationError, RuleDataIntegrityError


class TechnicianFinanceService:
    """Consolidate existing operational and ledger data for one technician."""

    def __init__(self, auth: AuthService):
        self.auth = auth

    @staticmethod
    def _id(value):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("technician_id must be a positive integer")
        return value

    def _require_technician(self, connection, technician_id):
        self._id(technician_id)
        if not connection.execute(
                "SELECT 1 FROM Techs WHERE tech_id=?", (technician_id,)).fetchone():
            raise LookupError("Technician not found")

    @staticmethod
    def _component_amounts(raw_details):
        try:
            details = json.loads(raw_details or "{}")
        except (TypeError, ValueError):
            return None, None, None
        amounts = {"Base": 0, "Travel": 0, "Off Hours": 0}
        found = False
        for component in details.get("technician_components", []):
            name = component.get("component")
            if name in amounts:
                found = True
                amounts[name] += int(component.get("calculated_amount_cents") or 0)
        if not found:
            return None, None, None
        return amounts["Base"], amounts["Travel"], amounts["Off Hours"]

    def get_summary(self, technician_id: int):
        with self.auth.connection() as connection:
            self._require_technician(connection, technician_id)
            jobs = connection.execute("""SELECT
              COUNT(DISTINCT CASE WHEN date(j.scheduled_start_at)>=date(?)
                AND j.completed_at IS NULL AND j.cancelled_at IS NULL
                AND j.job_status NOT IN ('Completed','Cancelled') THEN j.job_id END) upcoming_jobs,
              COUNT(DISTINCT CASE WHEN j.completed_at IS NOT NULL OR j.job_status='Completed'
                THEN j.job_id END) completed_jobs
              FROM (SELECT DISTINCT job_id,tech_id FROM JobAssignments
                    WHERE assignment_status NOT IN ('Declined','Unassigned','Reassigned')) a
              JOIN Jobs j ON j.job_id=a.job_id WHERE a.tech_id=?""",
              (date.today().isoformat(), technician_id)).fetchone()
            ledger = connection.execute("""SELECT
              COALESCE(SUM(CASE WHEN earning_status='Pending' THEN net_earning_cents ELSE 0 END),0) pending_cents,
              COALESCE(SUM(CASE WHEN earning_status='Approved' THEN net_earning_cents ELSE 0 END),0) balance_due_cents,
              COALESCE(SUM(CASE WHEN entry_type='Calculated' AND earning_status='Pending' THEN net_earning_cents ELSE 0 END),0) pending_approval_cents,
              COALESCE(SUM(CASE WHEN entry_type='Manual Adjustment' AND earning_status IN ('Pending','Approved') THEN net_earning_cents ELSE 0 END),0) pending_direct_cents,
              COALESCE(SUM(CASE WHEN entry_type='Calculated' AND earning_status<>'Voided' THEN net_earning_cents ELSE 0 END),0) completed_earnings_cents,
              COUNT(CASE WHEN earning_status='Approved' THEN 1 END) unpaid_earning_count
              FROM TechnicianJobEarnings WHERE tech_id=? AND earning_status<>'Voided'""",
              (technician_id,)).fetchone()
            paid = connection.execute("""SELECT COALESCE(SUM(
              CASE WHEN payment_status='Paid' THEN COALESCE(actual_amount_cents,payment_amount_cents)
                   ELSE 0 END),0) total_paid_cents
              FROM TechnicianPayments WHERE tech_id=?""", (technician_id,)).fetchone()
            upcoming = connection.execute("""SELECT COALESCE(SUM(CAST(ROUND((COALESCE(jf.ct_rate,0)+COALESCE(jf.ct_travel_payout,0)+COALESCE(jf.ct_off_hours_payout,0))*100) AS INTEGER)),0)
              FROM (SELECT DISTINCT job_id FROM JobAssignments WHERE tech_id=? AND assignment_status NOT IN ('Declined','Unassigned','Reassigned')) a
              JOIN Jobs j ON j.job_id=a.job_id LEFT JOIN JobFinancials jf ON jf.job_id=j.job_id
              WHERE date(j.scheduled_start_at)>=date(?) AND j.completed_at IS NULL AND j.cancelled_at IS NULL""",
              (technician_id,date.today().isoformat())).fetchone()[0]
            return {**dict(jobs), **dict(ledger), **dict(paid), "upcoming_expected_cents": upcoming}

    def list_jobs(self, technician_id: int, view="All"):
        allowed = {"All", "Upcoming", "Completed", "Cancelled", "Owed"}
        if view not in allowed:
            raise ValueError("Unsupported technician job view")
        clauses = ["a.tech_id=?"]
        params = [technician_id]
        today = date.today().isoformat()
        if view == "Upcoming":
            clauses.append("date(j.scheduled_start_at)>=date(?) AND j.completed_at IS NULL "
                           "AND j.cancelled_at IS NULL AND j.job_status NOT IN ('Completed','Cancelled')")
            params.append(today)
        elif view == "Completed":
            clauses.append("(j.completed_at IS NOT NULL OR j.job_status='Completed')")
        elif view == "Cancelled":
            clauses.append("(j.cancelled_at IS NOT NULL OR j.job_status IN ('Cancelled','Archived'))")
        elif view == "Owed":
            clauses.append("EXISTS(SELECT 1 FROM TechnicianJobEarnings due "
                           "WHERE due.job_id=j.job_id AND due.tech_id=a.tech_id "
                           "AND due.earning_status='Approved')")
        sql = """SELECT j.job_id,j.external_job_id,j.project_name_source,j.client_name_source,
          j.job_status,j.scheduled_start_at,j.actual_start_at,j.completed_at,j.cancelled_at,j.market_id,
          COALESCE(j.capture_address_raw,j.address_1,'') job_address
          FROM (SELECT DISTINCT job_id,tech_id FROM JobAssignments
                WHERE assignment_status NOT IN ('Declined','Unassigned','Reassigned')) a
          JOIN Jobs j ON j.job_id=a.job_id
          WHERE """ + " AND ".join(clauses) + " ORDER BY COALESCE(j.scheduled_start_at,j.completed_at) DESC,j.job_id DESC"
        with self.auth.connection() as connection:
            self._require_technician(connection, technician_id)
            jobs = [dict(row) for row in connection.execute(sql, params)]
            calculator = CompensationService(self.auth)
            for job in jobs:
                earnings = connection.execute("""SELECT technician_earning_id,
                  net_earning_cents,earning_status,calculation_details_json
                  FROM TechnicianJobEarnings WHERE job_id=? AND tech_id=?
                    AND earning_status<>'Voided' ORDER BY technician_earning_id""",
                    (job["job_id"], technician_id)).fetchall()
                job.update(earned_cents=None, paid_cents=None, approved_due_cents=None,
                    base_pay_cents=None, travel_pay_cents=None, off_hours_pay_cents=None)
                if earnings:
                    job["earned_cents"] = sum(row["net_earning_cents"] for row in earnings)
                    statuses = {row["earning_status"] for row in earnings}
                    component_totals = [0, 0, 0]; has_components = False
                    for row in earnings:
                        amounts = self._component_amounts(row["calculation_details_json"])
                        if amounts[0] is not None:
                            has_components = True
                            component_totals = [a + b for a, b in zip(component_totals, amounts)]
                    if has_components:
                        (job["base_pay_cents"], job["travel_pay_cents"],
                         job["off_hours_pay_cents"]) = component_totals
                    approved_ids = [row["technician_earning_id"] for row in earnings
                                    if row["earning_status"] in {"Approved", "Paid"}]
                    if approved_ids:
                        placeholders = ",".join("?" for _ in approved_ids)
                        paid = connection.execute(f"""SELECT COALESCE(SUM(pe.amount_applied_cents),0)
                          FROM TechnicianPaymentEarnings pe JOIN TechnicianPayments p
                            ON p.technician_payment_id=pe.technician_payment_id
                          WHERE pe.technician_earning_id IN ({placeholders})
                            AND p.payment_status='Paid'""", approved_ids).fetchone()[0]
                        approved = sum(row["net_earning_cents"] for row in earnings
                                       if row["earning_status"] in {"Approved", "Paid"})
                        job["paid_cents"] = paid
                        job["approved_due_cents"] = max(0, approved - paid)
                    job["finance_status"] = ("Paid" if statuses == {"Paid"} else
                        "Approved" if statuses & {"Approved", "Paid"} else "Generated")
                    continue

                completed = job["completed_at"] is not None or job["job_status"] == "Completed"
                cancelled = job["cancelled_at"] is not None or job["job_status"] in {"Cancelled", "Archived"}
                if not completed or cancelled:
                    job["finance_status"] = "Not generated"
                    continue
                try:
                    preview = calculator.preview_completed_job_compensation(
                        connection, job=job, tech_id=technician_id)
                    job["earned_cents"] = preview["amount_cents"]
                    parts = preview["components"]
                    if not (len(parts) == 1 and parts[0]["component"] == "Overall"):
                        totals = {"Base": 0, "Travel": 0, "Off Hours": 0}
                        for part in parts:
                            if part["component"] in totals:
                                totals[part["component"]] += part["calculated_amount_cents"]
                        job.update(base_pay_cents=totals["Base"],
                            travel_pay_cents=totals["Travel"],
                            off_hours_pay_cents=totals["Off Hours"])
                    job["finance_status"] = "Calculated—not generated"
                except RuleConfigurationError:
                    job["finance_status"] = "No applicable compensation rule"
                except RuleDataIntegrityError:
                    job["finance_status"] = "Ambiguous compensation rules"
                except ValueError as exc:
                    job["finance_status"] = ("Missing job financial data"
                        if "financial" in str(exc).lower() else "Unable to calculate")
            return jobs

    def list_payments(self, technician_id: int):
        with self.auth.connection() as connection:
            self._require_technician(connection, technician_id)
            payments = [dict(row) for row in connection.execute("""SELECT p.*,
              COUNT(pe.technician_earning_id) earning_count
              FROM TechnicianPayments p LEFT JOIN TechnicianPaymentEarnings pe
                ON pe.technician_payment_id=p.technician_payment_id
              WHERE p.tech_id=? GROUP BY p.technician_payment_id
              ORDER BY COALESCE(p.payment_date,p.created_at) DESC,p.technician_payment_id DESC""",
              (technician_id,))]
            for payment in payments:
                payment["jobs"] = [dict(row) for row in connection.execute("""SELECT
                  e.technician_earning_id,e.entry_type,e.net_earning_cents,
                  pe.amount_applied_cents,j.job_id,j.external_job_id,
                  COALESCE(j.project_name_source,j.capture_address_raw,j.address_1,e.reason,'Adjustment') job_name,
                  e.calculation_details_json
                  FROM TechnicianPaymentEarnings pe JOIN TechnicianJobEarnings e
                    ON e.technician_earning_id=pe.technician_earning_id
                  LEFT JOIN Jobs j ON j.job_id=e.job_id
                  WHERE pe.technician_payment_id=? ORDER BY e.technician_earning_id""",
                  (payment["technician_payment_id"],))]
                for item in payment["jobs"]:
                    base, travel, off_hours = self._component_amounts(item.pop("calculation_details_json"))
                    if payment.get("payment_kind") == "Direct":
                        component = payment.get("financial_component")
                        base = item["amount_applied_cents"] if component == "Capture" else None
                        travel = item["amount_applied_cents"] if component == "Travel" else None
                        off_hours = item["amount_applied_cents"] if component == "Off Hours" else None
                    item.update(base_pay_cents=base, travel_pay_cents=travel,
                                off_hours_pay_cents=off_hours)
            return payments
