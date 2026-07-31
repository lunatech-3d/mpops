"""Manual technician payment-run lifecycle and safe payment-detail exports."""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError
from app.services.compensation_service import CompensationService

PAYMENT_METHODS = ("ACH", "Check", "Zelle", "PayPal", "Other")


class TechnicianPaymentService:
    def __init__(self, auth: AuthService): self.auth = auth

    @staticmethod
    def _write(session: Session | None):
        if session is None or session.role not in {"admin", "operator"}:
            raise AuthorizationError("Administrator or operator role required")

    @staticmethod
    def _id(value, name="id"):
        if isinstance(value,bool) or not isinstance(value,int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _eligible(connection, earning_id):
        row=connection.execute("""SELECT e.*,a.allocation_status,pe.technician_payment_id
          FROM TechnicianJobEarnings e
          LEFT JOIN CompanyRevenueAllocations a ON a.technician_earning_id=e.technician_earning_id
            AND a.allocation_status<>'Superseded'
          LEFT JOIN TechnicianPaymentEarnings pe ON pe.technician_earning_id=e.technician_earning_id
          WHERE e.technician_earning_id=?""",(earning_id,)).fetchone()
        if not row: return None,"earning does not exist"
        if row["earning_status"] != "Approved": return row,"earning is not Approved"
        if row["technician_payment_id"] is not None: return row,"earning is already linked to a payment"
        if row["entry_type"] != "Manual Adjustment" and row["allocation_status"] != "Approved":
            return row,"company allocation is not Approved"
        return row,None

    def list_approved_unpaid_earnings(self, **filters):
        filters={**filters,"status":"Approved","unpaid_only":True}
        return [row for row in CompensationService(self.auth).list_earnings_for_review(**filters)
                if row["technician_payment_id"] is None and
                (row["entry_type"] == "Manual Adjustment" or row["allocation_status"] == "Approved")]

    def create_payment_run(self, session: Session, earning_ids: list[int], notes: str | None=None,
                           source_payment_batch_id: int | None=None):
        self._write(session); ids=list(dict.fromkeys(self._id(x,"earning_id") for x in earning_ids))
        if not ids: raise ValueError("At least one approved earning must be selected")
        with self.auth.connection() as c:
            rows=[]; failures=[]
            for eid in ids:
                row,error=self._eligible(c,eid)
                if error: failures.append(f"{eid}: {error}")
                else: rows.append(row)
            if failures: raise ValueError("Payment run validation failed: "+"; ".join(failures))
            grouped={}
            for row in rows: grouped.setdefault(row["tech_id"],[]).append(row)
            for tech, earnings in grouped.items():
                if sum(e["net_earning_cents"] for e in earnings) < 0:
                    raise ValueError(f"Selected payment total for technician {tech} cannot be negative")
            now=utc_now_iso()
            run_id=int(c.execute("""INSERT INTO TechnicianPaymentRuns
              (source_payment_batch_id,payment_status,total_amount_cents,notes,created_at,created_by)
              VALUES (?,'Draft',0,?,?,?)""",(source_payment_batch_id,(notes or "").strip() or None,now,session.user_id)).lastrowid)
            for tech, earnings in grouped.items():
                total=sum(e["net_earning_cents"] for e in earnings)
                payment_id=int(c.execute("""INSERT INTO TechnicianPayments
                  (technician_payment_run_id,tech_id,payment_amount_cents,payment_status,created_at)
                  VALUES (?,?,?,'Pending',?)""",(run_id,tech,total,now)).lastrowid)
                for earning in earnings:
                    c.execute("INSERT INTO TechnicianPaymentEarnings(technician_payment_id,technician_earning_id,amount_applied_cents,created_at) VALUES (?,?,?,?)",
                              (payment_id,earning["technician_earning_id"],earning["net_earning_cents"],now))
            total=sum(e["net_earning_cents"] for e in rows)
            c.execute("UPDATE TechnicianPaymentRuns SET total_amount_cents=? WHERE technician_payment_run_id=?",(total,run_id))
            record_event(c,"technician_payment_run_created",actor_user_id=session.user_id,details={
              "payment_run_id":run_id,"earning_ids":ids,"technician_ids":list(grouped),
              "amount_cents":total,"status":"Draft","timestamp":now})
            return self._get(c,run_id)

    @staticmethod
    def _get(c, run_id):
        run=c.execute("""SELECT r.*,u.username created_by_name FROM TechnicianPaymentRuns r
          LEFT JOIN Users u ON u.id=r.created_by WHERE technician_payment_run_id=?""",(run_id,)).fetchone()
        if not run: raise LookupError("Technician payment run not found")
        result=dict(run)
        result["payments"]=[]
        for p in c.execute("""SELECT p.*,COALESCE(t.preferred_name,t.first_name)||' '||t.last_name technician_name,
          COUNT(pe.technician_earning_id) earning_count,
          SUM(CASE WHEN e.entry_type='Calculated' THEN 1 ELSE 0 END) job_count,
          COALESCE(SUM(CASE WHEN e.entry_type='Calculated' THEN e.revenue_basis_cents ELSE 0 END),0) gross_revenue_cents,
          COALESCE(SUM(CASE WHEN e.entry_type='Calculated' THEN e.net_earning_cents ELSE 0 END),0) earnings_cents,
          COALESCE(SUM(CASE WHEN e.entry_type='Manual Adjustment' THEN e.net_earning_cents ELSE 0 END),0) adjustments_cents
          FROM TechnicianPayments p JOIN Techs t ON t.tech_id=p.tech_id
          LEFT JOIN TechnicianPaymentEarnings pe ON pe.technician_payment_id=p.technician_payment_id
          LEFT JOIN TechnicianJobEarnings e ON e.technician_earning_id=pe.technician_earning_id
          WHERE p.technician_payment_run_id=? GROUP BY p.technician_payment_id ORDER BY technician_name""",(run_id,)):
            item=dict(p); item["earnings"]=[dict(e) for e in c.execute("""SELECT e.*,pe.amount_applied_cents,
              j.external_job_id FROM TechnicianPaymentEarnings pe JOIN TechnicianJobEarnings e
              ON e.technician_earning_id=pe.technician_earning_id LEFT JOIN Jobs j ON j.job_id=e.job_id
              WHERE pe.technician_payment_id=? ORDER BY e.technician_earning_id""",(p["technician_payment_id"],))]
            result["payments"].append(item)
        return result

    def get_payment_run(self, run_id):
        self._id(run_id,"payment_run_id")
        with self.auth.connection() as c: return self._get(c,run_id)

    def list_payment_runs(self, status=None, technician_id=None):
        clauses=[];params=[]
        if status not in (None,"All"): clauses.append("r.payment_status=?");params.append(status)
        if technician_id: clauses.append("EXISTS(SELECT 1 FROM TechnicianPayments x WHERE x.technician_payment_run_id=r.technician_payment_run_id AND x.tech_id=?)");params.append(technician_id)
        where=" WHERE "+" AND ".join(clauses) if clauses else ""
        with self.auth.connection() as c:
            return [dict(r) for r in c.execute("""SELECT r.*,u.username created_by_name,
              COUNT(DISTINCT p.tech_id) technician_count,COUNT(pe.technician_earning_id) earning_count,
              MAX(p.payment_date) payment_date FROM TechnicianPaymentRuns r LEFT JOIN Users u ON u.id=r.created_by
              LEFT JOIN TechnicianPayments p ON p.technician_payment_run_id=r.technician_payment_run_id
              LEFT JOIN TechnicianPaymentEarnings pe ON pe.technician_payment_id=p.technician_payment_id"""+where+
              " GROUP BY r.technician_payment_run_id ORDER BY r.technician_payment_run_id DESC",params)]

    def recalculate_payment_run(self, session, run_id, expected_version=None):
        self._write(session);self._id(run_id,"payment_run_id")
        with self.auth.connection() as c:
            run=c.execute("SELECT * FROM TechnicianPaymentRuns WHERE technician_payment_run_id=?",(run_id,)).fetchone()
            if not run: raise LookupError("Technician payment run not found")
            if run["payment_status"] != "Draft": raise ValueError("Only Draft runs may be recalculated")
            if expected_version is not None and run["version"] != expected_version: raise ValueError("Payment run was modified by another user")
            payments=c.execute("SELECT technician_payment_id FROM TechnicianPayments WHERE technician_payment_run_id=?",(run_id,)).fetchall()
            total=0
            for payment in payments:
                amount=c.execute("SELECT COALESCE(SUM(amount_applied_cents),0) FROM TechnicianPaymentEarnings WHERE technician_payment_id=?",(payment[0],)).fetchone()[0]
                if amount < 0: raise ValueError("A technician payment cannot have a negative total")
                c.execute("UPDATE TechnicianPayments SET payment_amount_cents=?,updated_at=? WHERE technician_payment_id=?",(amount,utc_now_iso(),payment[0]));total+=amount
            c.execute("UPDATE TechnicianPaymentRuns SET total_amount_cents=?,updated_at=?,updated_by=?,version=version+1 WHERE technician_payment_run_id=?",(total,utc_now_iso(),session.user_id,run_id))
            return self._get(c,run_id)

    def add_earnings_to_payment_run(self, session, run_id, earning_ids, expected_version=None):
        self._write(session); self._id(run_id,"payment_run_id")
        ids=list(dict.fromkeys(self._id(x,"earning_id") for x in earning_ids))
        with self.auth.connection() as c:
            run=c.execute("SELECT * FROM TechnicianPaymentRuns WHERE technician_payment_run_id=?",(run_id,)).fetchone()
            if not run or run["payment_status"] != "Draft": raise ValueError("Only a Draft run may be modified")
            if expected_version is not None and run["version"] != expected_version: raise ValueError("Payment run was modified by another user")
            now=utc_now_iso()
            for eid in ids:
                earning,error=self._eligible(c,eid)
                if error: raise ValueError(f"Earning {eid}: {error}")
                payment=c.execute("SELECT * FROM TechnicianPayments WHERE technician_payment_run_id=? AND tech_id=?",(run_id,earning["tech_id"])).fetchone()
                if not payment:
                    pid=int(c.execute("INSERT INTO TechnicianPayments(technician_payment_run_id,tech_id,payment_amount_cents,payment_status,created_at) VALUES (?,?,0,'Pending',?)",(run_id,earning["tech_id"],now)).lastrowid)
                else: pid=payment["technician_payment_id"]
                c.execute("INSERT INTO TechnicianPaymentEarnings(technician_payment_id,technician_earning_id,amount_applied_cents,created_at) VALUES (?,?,?,?)",(pid,eid,earning["net_earning_cents"],now))
                record_event(c,"technician_earning_added_to_payment_run",actor_user_id=session.user_id,details={"payment_run_id":run_id,"earning_id":eid,"technician_id":earning["tech_id"],"amount_cents":earning["net_earning_cents"],"timestamp":now})
            c.execute("UPDATE TechnicianPaymentRuns SET version=version+1 WHERE technician_payment_run_id=?",(run_id,))
        return self.recalculate_payment_run(session,run_id)

    def remove_earnings_from_payment_run(self, session, run_id, earning_ids, expected_version=None):
        self._write(session); ids=list(dict.fromkeys(self._id(x,"earning_id") for x in earning_ids))
        with self.auth.connection() as c:
            run=c.execute("SELECT * FROM TechnicianPaymentRuns WHERE technician_payment_run_id=?",(run_id,)).fetchone()
            if not run or run["payment_status"] != "Draft": raise ValueError("Only a Draft run may be modified")
            if expected_version is not None and run["version"] != expected_version: raise ValueError("Payment run was modified by another user")
            now=utc_now_iso()
            for eid in ids:
                link=c.execute("""SELECT pe.technician_payment_earning_id,p.tech_id,p.technician_payment_id,e.net_earning_cents
                  FROM TechnicianPaymentEarnings pe JOIN TechnicianPayments p ON p.technician_payment_id=pe.technician_payment_id
                  JOIN TechnicianJobEarnings e ON e.technician_earning_id=pe.technician_earning_id
                  WHERE p.technician_payment_run_id=? AND pe.technician_earning_id=?""",(run_id,eid)).fetchone()
                if not link: raise ValueError(f"Earning {eid} is not in this run")
                c.execute("DELETE FROM TechnicianPaymentEarnings WHERE technician_payment_earning_id=?",(link[0],))
                record_event(c,"technician_earning_removed_from_payment_run",actor_user_id=session.user_id,details={"payment_run_id":run_id,"earning_id":eid,"technician_id":link["tech_id"],"amount_cents":link["net_earning_cents"],"timestamp":now})
            c.execute("DELETE FROM TechnicianPayments WHERE technician_payment_run_id=? AND NOT EXISTS(SELECT 1 FROM TechnicianPaymentEarnings pe WHERE pe.technician_payment_id=TechnicianPayments.technician_payment_id)",(run_id,))
            c.execute("UPDATE TechnicianPaymentRuns SET version=version+1 WHERE technician_payment_run_id=?",(run_id,))
        return self.recalculate_payment_run(session,run_id)

    def approve_payment_run(self, session, run_id, expected_version=None):
        self._write(session)
        run=self.recalculate_payment_run(session,run_id,expected_version)
        with self.auth.connection() as c:
            fresh=self._get(c,run_id)
            if not fresh["payments"] or any(not p["earnings"] for p in fresh["payments"]): raise ValueError("Every payment must contain an earning")
            for payment in fresh["payments"]:
                if payment["payment_amount_cents"] < 0: raise ValueError("Payment total cannot be negative")
                for earning in payment["earnings"]:
                    row,error=self._eligible_for_approval(c,earning["technician_earning_id"],payment["technician_payment_id"])
                    if error: raise ValueError(error)
            now=utc_now_iso()
            c.execute("UPDATE TechnicianPayments SET payment_status='Approved',approved_at=?,approved_by=? WHERE technician_payment_run_id=?",(now,session.user_id,run_id))
            c.execute("UPDATE TechnicianPaymentRuns SET payment_status='Approved',approved_at=?,approved_by=?,version=version+1 WHERE technician_payment_run_id=? AND payment_status='Draft'",(now,session.user_id,run_id))
            record_event(c,"technician_payment_run_approved",actor_user_id=session.user_id,details={"payment_run_id":run_id,"earning_ids":[e["technician_earning_id"] for p in fresh["payments"] for e in p["earnings"]],"previous_status":"Draft","new_status":"Approved","amount_cents":fresh["total_amount_cents"],"timestamp":now})
            return self._get(c,run_id)

    @staticmethod
    def _eligible_for_approval(c,eid,pid):
        row=c.execute("""SELECT e.*,a.allocation_status,pe.technician_payment_id FROM TechnicianJobEarnings e
          JOIN TechnicianPaymentEarnings pe ON pe.technician_earning_id=e.technician_earning_id
          LEFT JOIN CompanyRevenueAllocations a ON a.technician_earning_id=e.technician_earning_id AND a.allocation_status<>'Superseded'
          WHERE e.technician_earning_id=?""",(eid,)).fetchone()
        if not row or row["technician_payment_id"] != pid: return row,"Earning payment link changed"
        if row["earning_status"] != "Approved": return row,"Every included earning must remain Approved"
        if row["entry_type"] != "Manual Adjustment" and row["allocation_status"] != "Approved": return row,"Every allocation must remain Approved"
        return row,None

    def cancel_payment_run(self, session, run_id):
        self._write(session)
        with self.auth.connection() as c:
            run=self._get(c,run_id)
            if run["payment_status"] != "Draft": raise ValueError("Only Draft runs may be cancelled")
            ids=[e["technician_earning_id"] for p in run["payments"] for e in p["earnings"]];now=utc_now_iso()
            c.execute("DELETE FROM TechnicianPaymentEarnings WHERE technician_payment_id IN (SELECT technician_payment_id FROM TechnicianPayments WHERE technician_payment_run_id=?)",(run_id,))
            c.execute("UPDATE TechnicianPayments SET payment_status='Cancelled',updated_at=? WHERE technician_payment_run_id=?",(now,run_id))
            c.execute("UPDATE TechnicianPaymentRuns SET payment_status='Cancelled',cancelled_at=?,cancelled_by=?,version=version+1 WHERE technician_payment_run_id=?",(now,session.user_id,run_id))
            record_event(c,"technician_payment_run_cancelled",actor_user_id=session.user_id,details={"payment_run_id":run_id,"released_earning_ids":ids,"previous_status":"Draft","new_status":"Cancelled","timestamp":now})
            return self._get(c,run_id)

    def record_technician_payment(self, session, payment_id, *, payment_date, payment_method,
            payment_reference=None, notes=None, actual_amount_cents=None):
        self._write(session);self._id(payment_id,"technician_payment_id")
        try: paid_date=date.fromisoformat(str(payment_date)).isoformat()
        except (TypeError,ValueError): raise ValueError("payment_date is required in YYYY-MM-DD format")
        if payment_method not in PAYMENT_METHODS: raise ValueError("Unsupported payment method")
        if payment_method in {"Check","Zelle","PayPal"} and not str(payment_reference or "").strip():
            raise ValueError("payment_reference is required for this method")
        with self.auth.connection() as c:
            payment=c.execute("""SELECT p.*,r.payment_status run_status FROM TechnicianPayments p JOIN TechnicianPaymentRuns r
              ON r.technician_payment_run_id=p.technician_payment_run_id WHERE p.technician_payment_id=?""",(payment_id,)).fetchone()
            if not payment: raise LookupError("Technician payment not found")
            if payment["run_status"] not in {"Approved","Partially Paid"} or payment["payment_status"] != "Approved": raise ValueError("Only an approved unpaid payment may be recorded")
            actual=payment["payment_amount_cents"] if actual_amount_cents is None else actual_amount_cents
            if isinstance(actual,bool) or not isinstance(actual,int) or actual != payment["payment_amount_cents"]: raise ValueError("Actual amount must equal payment total; create an approved adjustment first")
            earnings=c.execute("""SELECT e.* FROM TechnicianPaymentEarnings pe JOIN TechnicianJobEarnings e
              ON e.technician_earning_id=pe.technician_earning_id WHERE pe.technician_payment_id=?""",(payment_id,)).fetchall()
            if not earnings or any(e["earning_status"] != "Approved" for e in earnings): raise ValueError("Included earnings are no longer approved and unpaid")
            now=utc_now_iso(); reference=str(payment_reference or "").strip() or None
            changed=c.execute("""UPDATE TechnicianPayments SET payment_status='Paid',payment_date=?,payment_method=?,
              payment_reference=?,actual_amount_cents=?,notes=?,settled_at=?,recorded_at=?,recorded_by=?,updated_at=?
              WHERE technician_payment_id=? AND payment_status='Approved'""",(paid_date,payment_method,reference,actual,(notes or "").strip() or None,now,now,session.user_id,now,payment_id)).rowcount
            if changed != 1: raise ValueError("Payment was already recorded")
            ids=[e["technician_earning_id"] for e in earnings]
            placeholders=",".join("?" for _ in ids)
            if c.execute(f"UPDATE TechnicianJobEarnings SET earning_status='Paid',paid_at=? WHERE technician_earning_id IN ({placeholders}) AND earning_status='Approved'",(now,*ids)).rowcount != len(ids): raise ValueError("Concurrent earning payment detected")
            unpaid=c.execute("SELECT COUNT(*) FROM TechnicianPayments WHERE technician_payment_run_id=? AND payment_status<>'Paid'",(payment["technician_payment_run_id"],)).fetchone()[0]
            status="Paid" if not unpaid else "Partially Paid"
            c.execute("UPDATE TechnicianPaymentRuns SET payment_status=?,payment_run_date=?,version=version+1 WHERE technician_payment_run_id=?",(status,paid_date,payment["technician_payment_run_id"]))
            record_event(c,"technician_payment_recorded",actor_user_id=session.user_id,details={"payment_run_id":payment["technician_payment_run_id"],"technician_payment_id":payment_id,"technician_id":payment["tech_id"],"earning_ids":ids,"amount_cents":actual,"payment_method":payment_method,"payment_reference":reference,"timestamp":now})
            record_event(c,"technician_earnings_marked_paid",actor_user_id=session.user_id,details={"earning_ids":ids,"previous_status":"Approved","new_status":"Paid","technician_payment_id":payment_id,"timestamp":now})
            return self._get(c,payment["technician_payment_run_id"])

    def export_payment_detail_csv(self, payment_id):
        self._id(payment_id,"technician_payment_id")
        with self.auth.connection() as c:
            rows=c.execute("""SELECT COALESCE(t.preferred_name,t.first_name)||' '||t.last_name Technician,
              p.payment_date "Payment date",p.payment_method "Payment method",p.payment_reference "Payment reference",
              j.job_id "Job ID",j.external_job_id "External Job ID",COALESCE(j.capture_address_raw,j.address_1,'') "Job address",
              substr(COALESCE(j.completed_at,j.actual_start_at,j.scheduled_start_at),1,10) "Job date",
              e.revenue_basis_cents "Gross revenue",e.compensation_rule_value "Technician rate",
              e.calculated_amount_cents "Technician earning",e.adjustment_amount_cents Adjustment,
              e.net_earning_cents "Net amount",e.payment_batch_id "Matterport payment batch",b.payment_date "Matterport payment date"
              FROM TechnicianPayments p JOIN Techs t ON t.tech_id=p.tech_id JOIN TechnicianPaymentEarnings pe ON pe.technician_payment_id=p.technician_payment_id
              JOIN TechnicianJobEarnings e ON e.technician_earning_id=pe.technician_earning_id LEFT JOIN Jobs j ON j.job_id=e.job_id
              LEFT JOIN MatterportPaymentBatches b ON b.payment_batch_id=e.payment_batch_id WHERE p.technician_payment_id=? ORDER BY e.technician_earning_id""",(payment_id,)).fetchall()
        output=io.StringIO(); fields=["Technician","Payment date","Payment method","Payment reference","Job ID","External Job ID","Job address","Job date","Gross revenue","Technician rate","Technician earning","Adjustment","Net amount","Matterport payment batch","Matterport payment date"]
        writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader();writer.writerows(dict(r) for r in rows);return output.getvalue()
