"""Read-only technician job, balance, and payment-history reporting."""

from __future__ import annotations

import json
from datetime import date

from app.security.auth import AuthService


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
          j.job_status,j.scheduled_start_at,j.completed_at,
          COALESCE(j.capture_address_raw,j.address_1,'') job_address,
          e.technician_earning_id,e.entry_type,e.net_earning_cents,e.earning_status,
          e.calculation_details_json,pe.amount_applied_cents,p.payment_status,
          p.technician_payment_id,p.payment_date
          FROM (SELECT DISTINCT job_id,tech_id FROM JobAssignments
                WHERE assignment_status NOT IN ('Declined','Unassigned','Reassigned')) a
          JOIN Jobs j ON j.job_id=a.job_id
          LEFT JOIN TechnicianJobEarnings e ON e.job_id=j.job_id AND e.tech_id=a.tech_id
            AND e.earning_status<>'Voided'
          LEFT JOIN TechnicianPaymentEarnings pe ON pe.technician_earning_id=e.technician_earning_id
          LEFT JOIN TechnicianPayments p ON p.technician_payment_id=pe.technician_payment_id
          WHERE """ + " AND ".join(clauses) + " ORDER BY COALESCE(j.scheduled_start_at,j.completed_at) DESC,j.job_id DESC"
        with self.auth.connection() as connection:
            self._require_technician(connection, technician_id)
            rows = connection.execute(sql, params).fetchall()
        jobs = {}
        for raw in rows:
            row = dict(raw); job = jobs.setdefault(row["job_id"], {
                key: row[key] for key in ("job_id", "external_job_id", "project_name_source",
                    "client_name_source", "job_status", "scheduled_start_at", "completed_at", "job_address")})
            job.setdefault("earned_cents", 0); job.setdefault("approved_due_cents", 0)
            job.setdefault("paid_cents", 0); job.setdefault("base_pay_cents", None)
            job.setdefault("travel_pay_cents", None); job.setdefault("off_hours_pay_cents", None)
            job.setdefault("earning_statuses", set())
            if row["technician_earning_id"] is None:
                continue
            job["earned_cents"] += row["net_earning_cents"]
            if row["earning_status"] == "Approved":
                job["approved_due_cents"] += row["net_earning_cents"]
            if row["payment_status"] == "Paid":
                job["paid_cents"] += row["amount_applied_cents"]
            base, travel, off_hours = self._component_amounts(row["calculation_details_json"])
            if base is not None:
                job["base_pay_cents"] = (job["base_pay_cents"] or 0) + base
                job["travel_pay_cents"] = (job["travel_pay_cents"] or 0) + travel
                job["off_hours_pay_cents"] = (job["off_hours_pay_cents"] or 0) + off_hours
            job["earning_statuses"].add(row["earning_status"])
        result = []
        for job in jobs.values():
            statuses = job.pop("earning_statuses")
            job["approved_due_cents"] = max(0, job["approved_due_cents"] - job["paid_cents"])
            job["finance_status"] = ("Owed" if job["approved_due_cents"] else
                "Paid" if "Paid" in statuses and statuses <= {"Paid"} else
                "Pending Review" if "Pending" in statuses else
                "Not Calculated")
            result.append(job)
        return result

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
