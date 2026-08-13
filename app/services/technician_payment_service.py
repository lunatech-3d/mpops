"""Manual technician payment-run lifecycle and safe payment-detail exports."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from typing import Any

from app.address_utils import format_service_address
from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError
from app.services.compensation_service import CompensationService

PAYMENT_METHODS = ("ACH", "Check", "Zelle", "Venmo", "PayPal", "Other")
DIRECT_PAYMENT_CATEGORIES = (
    "Mileage / Additional Travel", "Parking", "Tolls", "Lodging",
    "Supplies / Out-of-Pocket Expense", "Expense Reimbursement — Other", "Bonus",
    "Compensation Adjustment", "Payment Correction", "Advance", "Miscellaneous",
)
# Names written by older releases remain readable/creatable for compatibility,
# while every new UI uses the canonical classifications above.
LEGACY_DIRECT_PAYMENT_CATEGORIES = (
    "Expense reimbursement", "Special travel payment", "Compensation adjustment",
    "Payment correction", "Reimbursement", "Adjustment", "Other direct payment",
)
_PAYMENT_ITEM_STORAGE_TYPES = {
    **{name: "Reimbursement" for name in DIRECT_PAYMENT_CATEGORIES[:6]},
    "Bonus": "Bonus", "Compensation Adjustment": "Adjustment",
    "Payment Correction": "Adjustment", "Advance": "Other direct payment",
    "Miscellaneous": "Other direct payment",
}
DIRECT_PAYMENT_STATUSES = ("Draft", "Approved", "Scheduled", "Paid")
FINAL_PAYMENT_STATUSES = {"Paid"}
_BASIC_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# Stored compensation terminology is deliberately mapped here rather than
# guessed from substrings.  Adding a new earning component consequently
# requires an explicit decision about how it should appear in payment emails.
EMAIL_COMPONENT_CATEGORIES = {
    **{name.casefold(): "Capture" for name in (
        "Overall", "Base", "Base Pay", "Capture", "Capture Pay", "Capture Fee",
        "Standard Capture",
    )},
    **{name.casefold(): "Travel" for name in (
        "Travel", "Travel Pay", "Mileage", "Mileage Pay", "Additional Travel",
    )},
    **{name.casefold(): "Adjustment" for name in (
        "Adjustment", "Payment Adjustment", "Correction", "Payment Correction",
    )},
    **{name.casefold(): "Other" for name in (
        "Off Hours", "Rush", "Cancellation", "Equipment", "Parking", "Tolls",
    )},
}


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
        row=connection.execute("""SELECT e.*,a.allocation_status,
          COALESCE((SELECT SUM(pe.amount_applied_cents)
            FROM TechnicianPaymentEarnings pe JOIN TechnicianPayments p
              ON p.technician_payment_id=pe.technician_payment_id
            WHERE pe.technician_earning_id=e.technician_earning_id
              AND p.payment_status='Paid' AND p.reversed_at IS NULL),0) valid_paid_cents
          FROM TechnicianJobEarnings e
          LEFT JOIN CompanyRevenueAllocations a ON a.technician_earning_id=e.technician_earning_id
            AND a.allocation_status<>'Superseded'
          WHERE e.technician_earning_id=?""",(earning_id,)).fetchone()
        if not row: return None,"earning does not exist"
        if row["earning_status"] not in {"Approved", "Paid"}: return row,"earning is not approved"
        if row["valid_paid_cents"] >= row["net_earning_cents"]: return row,"earning is fully paid"
        if row["voided_at"] is not None: return row,"earning is voided"
        if row["entry_type"] != "Manual Adjustment" and row["allocation_status"] != "Approved":
            return row,"company allocation is not Approved"
        return row,None

    def list_approved_unpaid_earnings(self, **filters):
        filters={**filters,"status":"All","unpaid_only":True}
        return [row for row in CompensationService(self.auth).list_earnings_for_review(**filters)
                if row["voided_at"] is None and row["balance_due_cents"] > 0 and
                (row["entry_type"] == "Manual Adjustment" or row["allocation_status"] == "Approved")]

    def list_outstanding_earnings(self, technician_id: int):
        """Return allocatable balances for exactly one technician.

        Paid value is derived from allocations on valid paid payments, rather
        than from the legacy all-or-nothing earning status flag.
        """
        self._id(technician_id, "technician_id")
        with self.auth.connection() as c:
            rows = c.execute("""SELECT e.*,j.external_job_id,j.project_name_source,
              COALESCE(j.completed_at,j.actual_start_at,j.scheduled_start_at) service_date,
              COALESCE(SUM(CASE WHEN p.payment_status='Paid' AND p.reversed_at IS NULL
                THEN pe.amount_applied_cents ELSE 0 END),0) previously_paid_cents
              FROM TechnicianJobEarnings e
              LEFT JOIN Jobs j ON j.job_id=e.job_id
              LEFT JOIN TechnicianPaymentEarnings pe ON pe.technician_earning_id=e.technician_earning_id
              LEFT JOIN TechnicianPayments p ON p.technician_payment_id=pe.technician_payment_id
              WHERE e.tech_id=? AND e.earning_status IN ('Approved','Paid') AND e.voided_at IS NULL
              GROUP BY e.technician_earning_id
              HAVING e.net_earning_cents-previously_paid_cents>0
              ORDER BY COALESCE(service_date,e.created_at),e.technician_earning_id""", (technician_id,)).fetchall()
            result=[]
            for row in rows:
                item=dict(row)
                item["balance_due_cents"]=item["net_earning_cents"]-item["previously_paid_cents"]
                result.append(item)
            return result

    def build_fifo_allocations(self, technician_id: int, amount_cents: int):
        """Propose deterministic oldest-service-first allocations without writing.

        The order is the order returned by :meth:`list_outstanding_earnings`:
        service date (falling back to earning creation time), then earning ID.
        """
        self._id(technician_id, "technician_id")
        if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents < 0:
            raise ValueError("amount_cents must be a nonnegative integer")
        remaining = amount_cents
        proposed = []
        for earning in self.list_outstanding_earnings(technician_id):
            if remaining <= 0:
                break
            cents = min(remaining, int(earning["balance_due_cents"]))
            if cents:
                proposed.append({"earning_id": earning["technician_earning_id"],
                                 "amount_cents": cents})
                remaining -= cents
        return proposed

    def find_payment_duplicates(self, *, technician_id: int, payment_date: str,
                                amount_cents: int, reference: str | None = None):
        """Return blocking reference matches and review-only likely matches."""
        self._id(technician_id, "technician_id")
        with self.auth.connection() as c:
            reference_matches=[]
            if str(reference or "").strip():
                reference_matches=[dict(r) for r in c.execute(
                    "SELECT * FROM TechnicianPayments WHERE payment_reference=? COLLATE NOCASE",
                    (str(reference).strip(),))]
            likely=[dict(r) for r in c.execute("""SELECT * FROM TechnicianPayments
              WHERE tech_id=? AND payment_date=? AND payment_amount_cents=?
                AND payment_status<>'Cancelled'""",(technician_id,payment_date,amount_cents))]
            return {"reference_matches":reference_matches,"likely_matches":likely}

    def create_manual_payment(self, session: Session, *, technician_id: int,
            payment_date: str, amount_cents: int, payment_method: str,
            status: str = "Draft", reference: str | None = None,
            description: str | None = None, notes: str | None = None,
            allocations: list[dict] | None = None, non_job_items: list[dict] | None = None,
            historical: bool = False, technician_confirmed: bool = False):
        """Atomically create a centralized payment and all of its allocations.

        Historical mode only records an already completed transaction; it does
        not invoke the payment-run scheduling/issuance workflow.
        """
        self._write(session); self._id(technician_id,"technician_id")
        try: paid_date=date.fromisoformat(str(payment_date)).isoformat()
        except (TypeError,ValueError): raise ValueError("payment_date is required in YYYY-MM-DD format")
        if isinstance(amount_cents,bool) or not isinstance(amount_cents,int) or amount_cents<=0:
            raise ValueError("amount_cents must be a positive integer")
        if payment_method not in PAYMENT_METHODS: raise ValueError("Unsupported payment method")
        if status not in DIRECT_PAYMENT_STATUSES: raise ValueError("Unsupported payment status")
        reference=str(reference or "").strip() or None
        if status == "Paid":
            if not reference:
                raise ValueError("PINACLE confirmation/reference is required when recording a paid payment")
        if historical:
            if status != "Paid": raise ValueError("Historical payments must be recorded as Paid")
            if not technician_confirmed: raise ValueError("Confirm the technician before recording a historical payment")
            if not reference: raise ValueError("External bank reference is required for a historical payment")
        allocations=allocations or []; non_job_items=non_job_items or []
        normalized=[]
        for allocation in allocations:
            eid=self._id(allocation.get("earning_id"),"earning_id")
            cents=allocation.get("amount_cents")
            if isinstance(cents,bool) or not isinstance(cents,int) or cents<=0:
                raise ValueError("Allocation amounts must be positive whole cents")
            normalized.append((eid,cents))
        direct=[]
        allowed=set(DIRECT_PAYMENT_CATEGORIES) | set(LEGACY_DIRECT_PAYMENT_CATEGORIES)
        for item in non_job_items:
            category=item.get("type")
            cents=item.get("amount_cents")
            if category not in allowed: raise ValueError("Unsupported non-job payment type")
            if isinstance(cents,bool) or not isinstance(cents,int) or cents<=0:
                raise ValueError("Non-job amounts must be positive whole cents")
            if not str(item.get("description") or "").strip(): raise ValueError("Non-job item description is required")
            storage_type = _PAYMENT_ITEM_STORAGE_TYPES.get(category, category)
            direct.append((storage_type,cents,str(item["description"]).strip(),
                           str(item.get("notes") or "").strip() or None))
        allocated=sum(x[1] for x in normalized)+sum(x[1] for x in direct)
        if allocated>amount_cents: raise ValueError("Allocations exceed the payment total")
        if status in {"Approved","Scheduled","Paid"} and allocated != amount_cents:
            raise ValueError("Finalized payments cannot have an unclassified remainder")
        now=utc_now_iso()
        with self.auth.connection() as c:
            if not c.execute("SELECT 1 FROM Techs WHERE tech_id=?",(technician_id,)).fetchone(): raise LookupError("Technician not found")
            if reference and c.execute("SELECT 1 FROM TechnicianPayments WHERE payment_reference=? COLLATE NOCASE",(reference,)).fetchone():
                raise ValueError("A payment with this external reference already exists")
            checked=[]
            for eid,cents in normalized:
                earning=c.execute("SELECT * FROM TechnicianJobEarnings WHERE technician_earning_id=? AND tech_id=?",(eid,technician_id)).fetchone()
                if not earning or earning["earning_status"] not in {"Approved","Paid"} or earning["voided_at"] is not None:
                    raise ValueError(f"Earning {eid} is no longer approved and available")
                paid=c.execute("""SELECT COALESCE(SUM(pe.amount_applied_cents),0)
                  FROM TechnicianPaymentEarnings pe JOIN TechnicianPayments p ON p.technician_payment_id=pe.technician_payment_id
                  WHERE pe.technician_earning_id=? AND p.payment_status='Paid'
                    AND p.reversed_at IS NULL""",(eid,)).fetchone()[0]
                if cents>earning["net_earning_cents"]-paid: raise ValueError(f"Allocation exceeds earning {eid} balance")
                checked.append((earning,cents))
            # TechnicianPaymentRuns is a legacy non-null parent retained for schema
            # compatibility.  The payment plus its allocations is authoritative;
            # users never create or manage this internal wrapper in the active flow.
            run_status={"Draft":"Draft","Approved":"Approved","Scheduled":"Submitted","Paid":"Paid"}[status]
            run_id=int(c.execute("""INSERT INTO TechnicianPaymentRuns
              (payment_run_date,payment_status,total_amount_cents,notes,created_at,created_by,run_type)
              VALUES(?,?,?,?,?,?,'Manual')""",(paid_date,run_status,amount_cents,notes,now,session.user_id)).lastrowid)
            stored={"Draft":"Pending","Approved":"Approved","Scheduled":"Submitted","Paid":"Paid"}[status]
            pid=int(c.execute("""INSERT INTO TechnicianPayments
              (technician_payment_run_id,tech_id,payment_amount_cents,actual_amount_cents,payment_method,
               payment_status,payment_date,payment_reference,notes,recorded_at,recorded_by,created_at,
               approved_at,approved_by,payment_kind,description,is_historical)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(run_id,technician_id,amount_cents,
              amount_cents if status=="Paid" else None,payment_method,stored,paid_date,reference,notes,
              now if status=="Paid" else None,session.user_id if status=="Paid" else None,now,
              now if status in {"Approved","Scheduled","Paid"} else None,
              session.user_id if status in {"Approved","Scheduled","Paid"} else None,
              "Historical" if historical else "Manual",description,1 if historical else 0)).lastrowid)
            for earning,cents in checked:
                c.execute("INSERT INTO TechnicianPaymentEarnings(technician_payment_id,technician_earning_id,amount_applied_cents,created_at) VALUES(?,?,?,?)",(pid,earning["technician_earning_id"],cents,now))
                if status=="Paid" and cents==earning["net_earning_cents"]-c.execute("""SELECT COALESCE(SUM(pe.amount_applied_cents),0) FROM TechnicianPaymentEarnings pe JOIN TechnicianPayments p ON p.technician_payment_id=pe.technician_payment_id WHERE pe.technician_earning_id=? AND p.payment_status='Paid' AND p.technician_payment_id<>?""",(earning["technician_earning_id"],pid)).fetchone()[0]:
                    c.execute("UPDATE TechnicianJobEarnings SET earning_status='Paid',paid_at=? WHERE technician_earning_id=?",(now,earning["technician_earning_id"]))
            for category,cents,item_description,item_notes in direct:
                c.execute("INSERT INTO TechnicianPaymentItems(technician_payment_id,item_type,amount_cents,description,notes,created_at) VALUES(?,?,?,?,?,?)",(pid,category,cents,item_description,item_notes,now))
            record_event(c,"historical_technician_payment_recorded" if historical else "technician_payment_created",actor_user_id=session.user_id,details={"payment_id":pid,"technician_id":technician_id,"amount_cents":amount_cents,"status":status,"reference":reference})
            return self.get_payment_detail(pid, connection=c)

    def get_payment_detail(self, payment_id: int, connection=None):
        self._id(payment_id,"technician_payment_id")
        def read(c):
            row=c.execute("""SELECT p.*,COALESCE(t.preferred_name,t.first_name)||' '||t.last_name technician_name
              FROM TechnicianPayments p JOIN Techs t ON t.tech_id=p.tech_id WHERE p.technician_payment_id=?""",(payment_id,)).fetchone()
            if not row: raise LookupError("Technician payment not found")
            result=dict(row)
            result["allocations"]=[dict(x) for x in c.execute("""SELECT pe.*,e.entry_type,e.reason,e.adjustment_amount_cents,
              e.calculation_details_json,j.job_id,j.external_job_id,j.project_name_source,
              j.capture_address_raw,j.address_1,j.address_2,j.city,j.state,j.postal_code,
              substr(COALESCE(j.completed_at,j.actual_start_at,j.scheduled_start_at),1,10) service_date
              FROM TechnicianPaymentEarnings pe JOIN TechnicianJobEarnings e ON e.technician_earning_id=pe.technician_earning_id
              LEFT JOIN Jobs j ON j.job_id=e.job_id WHERE pe.technician_payment_id=? ORDER BY pe.technician_payment_earning_id""",(payment_id,))]
            result["non_job_items"]=[dict(x) for x in c.execute("SELECT * FROM TechnicianPaymentItems WHERE technician_payment_id=? ORDER BY technician_payment_item_id",(payment_id,))]
            return result
        if connection is not None:return read(connection)
        with self.auth.connection() as c:return read(c)

    def reverse_payment(self, session: Session, payment_id: int, reason: str):
        """Reverse a paid payment without deleting its header or line detail."""
        self._write(session);self._id(payment_id,"technician_payment_id")
        reason=str(reason or "").strip()
        if not reason:raise ValueError("Reversal reason is required")
        now=utc_now_iso()
        with self.auth.connection() as c:
            payment=c.execute("SELECT * FROM TechnicianPayments WHERE technician_payment_id=?",(payment_id,)).fetchone()
            if not payment:raise LookupError("Technician payment not found")
            if payment["payment_status"]!="Paid":raise ValueError("Only a paid payment may be reversed")
            c.execute("UPDATE TechnicianPayments SET payment_status='Cancelled',reversed_at=?,reversed_by=?,reversal_reason=?,updated_at=? WHERE technician_payment_id=?",(now,session.user_id,reason,now,payment_id))
            links=c.execute("SELECT technician_earning_id FROM TechnicianPaymentEarnings WHERE technician_payment_id=?",(payment_id,)).fetchall()
            for link in links:
                remaining=c.execute("""SELECT COALESCE(SUM(pe.amount_applied_cents),0) FROM TechnicianPaymentEarnings pe
                  JOIN TechnicianPayments p ON p.technician_payment_id=pe.technician_payment_id
                  WHERE pe.technician_earning_id=? AND p.payment_status='Paid'""",(link[0],)).fetchone()[0]
                earning=c.execute("SELECT net_earning_cents FROM TechnicianJobEarnings WHERE technician_earning_id=?",(link[0],)).fetchone()
                if earning and remaining<earning[0]:c.execute("UPDATE TechnicianJobEarnings SET earning_status='Approved',paid_at=NULL WHERE technician_earning_id=?",(link[0],))
            c.execute("UPDATE TechnicianPaymentRuns SET payment_status='Cancelled',cancelled_at=?,cancelled_by=?,version=version+1 WHERE technician_payment_run_id=?",(now,session.user_id,payment["technician_payment_run_id"]))
            c.execute("UPDATE TechnicianPaymentEmailDrafts SET draft_status='Outdated' WHERE technician_payment_id=? AND draft_status='Draft Generated'",(payment_id,))
            record_event(c,"technician_payment_reversed",actor_user_id=session.user_id,details={"payment_id":payment_id,"reason":reason,"timestamp":now})
            return self.get_payment_detail(payment_id,connection=c)

    @staticmethod
    def _email_components(allocation):
        try:
            details = json.loads(allocation.get("calculation_details_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            details = {}
        target = int(allocation["amount_applied_cents"])
        raw_components = details.get("technician_components") or []
        classified = []
        stored_names = []
        for component in raw_components:
            display_name = re.sub(r"\s+", " ", str(
                component.get("component") or component.get("component_type") or ""
            )).strip()
            stored_names.append(display_name or "<unnamed>")
            category = EMAIL_COMPONENT_CATEGORIES.get(display_name.casefold())
            if category is None:
                raise ValueError(
                    "Cannot generate technician payment email: unknown stored component "
                    f"{display_name or '<unnamed>'!r} for earning "
                    f"{allocation.get('technician_earning_id')} (job "
                    f"{allocation.get('external_job_id') or allocation.get('job_id') or 'unknown'})."
                )
            cents = int(component.get("calculated_amount_cents")
                        or component.get("amount_cents") or 0)
            classified.append((category, cents, display_name.casefold()))

        adjustment = int(allocation.get("adjustment_amount_cents") or 0)
        if adjustment and not any(category == "Adjustment" for category, _, _ in classified):
            classified.append(("Adjustment", adjustment, "adjustment"))
            stored_names.append("Adjustment")

        source_total = sum(cents for _, cents, _ in classified)
        stored_final = (details.get("final_amounts_cents") or {}).get("technician")
        earning_total = (int(stored_final) + adjustment
                         if stored_final is not None else source_total)
        single_overall = (len(raw_components) == 1 and
                          stored_names[0].casefold() == "overall")
        if source_total != earning_total and single_overall and earning_total >= source_total:
            # Older Overall earnings can lack a complete component amount.  An
            # Overall rule is, by definition here, ordinary capture earnings.
            classified.append(("Capture", earning_total - source_total, "overall"))
            source_total = earning_total
        if source_total != earning_total or target > earning_total:
            TechnicianPaymentService._component_reconciliation_error(
                allocation, target, source_total, stored_names)
        if source_total <= 0 and target == 0:
            return {name: 0 for name in ("Capture", "Travel", "Adjustment", "Other")}
        if source_total <= 0:
            TechnicianPaymentService._component_reconciliation_error(
                allocation, target, source_total, stored_names)

        # Allocate a partial payment in integer cents.  Largest remainders win;
        # ties prefer Capture and then stored order, so no cent is invented or lost.
        scaled = []
        for index, (category, cents, normalized_name) in enumerate(classified):
            numerator = cents * target
            scaled.append([category, numerator // source_total, numerator % source_total,
                           normalized_name, index])
        remainder = target - sum(item[1] for item in scaled)
        preference = {"Capture": 0, "Travel": 1, "Adjustment": 2, "Other": 3}
        for item in sorted(scaled, key=lambda x: (-x[2], preference[x[0]], x[4]))[:remainder]:
            item[1] += 1
        amounts = {"Capture": 0, "Travel": 0, "Adjustment": 0, "Other": 0}
        for category, cents, _, _, _ in scaled:
            amounts[category] += cents
        if sum(amounts.values()) != target:
            TechnicianPaymentService._component_reconciliation_error(
                allocation, target, sum(amounts.values()), stored_names)
        return amounts

    @staticmethod
    def _component_reconciliation_error(allocation, applied, classified, names):
        raise ValueError(
            "Cannot generate technician payment email: component reconciliation failed; "
            f"earning={allocation.get('technician_earning_id')}, "
            f"job={allocation.get('external_job_id') or allocation.get('job_id') or 'unknown'}, "
            f"applied={applied}, classified={classified}, difference={applied-classified}, "
            f"stored_components={names}."
        )

    def build_payment_email(self, payment_id: int):
        """Build a deterministic plain-text draft from the authoritative allocations."""
        self._id(payment_id, "technician_payment_id")
        with self.auth.connection() as c:
            payment = dict(c.execute("""SELECT p.*,t.first_name,t.last_name,t.preferred_name,t.email
              FROM TechnicianPayments p JOIN Techs t ON t.tech_id=p.tech_id
              WHERE p.technician_payment_id=?""", (payment_id,)).fetchone() or {})
            if not payment: raise LookupError("Technician payment not found")
            if payment["payment_status"] != "Paid" or payment.get("reversed_at"):
                raise ValueError("A current email draft can only be generated for a paid, unreversed payment")
            email = str(payment.get("email") or "").strip()
            if not email:
                raise ValueError("No email address is recorded for this technician.")
            if not _BASIC_EMAIL.fullmatch(email):
                raise ValueError("The technician email address is invalid. Open the Technician form to correct it.")
        detail = self.get_payment_detail(payment_id)
        lines=[]
        for allocation in detail["allocations"]:
            components=self._email_components(allocation)
            is_job = allocation.get("job_id") is not None
            lines.append({"kind":"job" if is_job else "non_job",
                "address":format_service_address(allocation) or "Address not recorded" if is_job else None,
                "job":allocation.get("external_job_id") or (f"Earning #{allocation['technician_earning_id']}" if is_job else None),
                "date":allocation.get("service_date") or "—",
                "description":allocation.get("reason") or allocation.get("entry_type") or "Adjustment",
                **components,"Total":int(allocation["amount_applied_cents"])})
        for item in detail["non_job_items"]:
            lines.append({"kind":"non_job","address":None,"job":None,"date":"—",
                "description":f"{item['item_type']} — {item.get('description') or 'Payment item'}",
                "Capture":0,"Travel":0,"Adjustment":0,"Other":int(item["amount_cents"]),
                "Total":int(item["amount_cents"])})
        if sum(line["Total"] for line in lines) != int(payment["payment_amount_cents"]):
            raise ValueError("Payment allocations do not equal the recorded payment total")
        job_records=[]
        for line in (line for line in lines if line["kind"] == "job"):
            address=line["address"]
            if address == "Address not recorded" and line["job"]:
                address += f" — Job {line['job']}"
            metadata=[f"Service Date: {line['date']}"]
            if line["job"] and " — Job " not in address: metadata.append(f"Job: {line['job']}")
            amounts=[f"{name}: ${line[name]/100:,.2f}"
                     for name in ("Capture", "Travel", "Adjustment", "Other") if line[name]]
            amounts.append(f"Total: ${line['Total']/100:,.2f}")
            job_records.append(f"{address}\n{'    '.join(metadata)}\n{'    '.join(amounts)}")
        sections=[]
        if job_records:
            sections.append("Jobs:\n\n" + "\n\n".join(job_records))
        non_job_lines=[f"{line['description']}    ${line['Total']/100:,.2f}"
                       for line in lines if line["kind"] == "non_job"]
        if non_job_lines:
            sections.append("Other payment items:\n\n" + "\n".join(non_job_lines))
        payment_detail="\n\n".join(sections)
        paid=date.fromisoformat(str(payment["payment_date"])[:10])
        total=f"${int(payment['payment_amount_cents'])/100:,.2f}"
        first=payment.get("preferred_name") or payment["first_name"]
        subject=f"LunaTech 3D payment details — {paid.strftime('%m/%d/%Y')}"
        body=(f"Hi {first},\n\nA payment of {total} was issued to you on {paid.strftime('%m/%d/%Y')} "
              f"by {payment.get('payment_method') or 'external payment'}.\n\nThis payment covers:\n\n{payment_detail}\n\n"
              f"Payment total: {total}\nPayment reference: {payment.get('payment_reference') or '—'}\n\n"
              "Please contact Hayley if you have any questions about this payment.\n\nThank you,\nLunaTech 3D")
        return {"payment_id":payment_id,"recipient":email,"subject":subject,"body":body,"lines":lines}

    def generate_payment_email_draft(self, session: Session, payment_id: int):
        """Audit draft generation without sending or changing the payment."""
        self._write(session)
        draft=self.build_payment_email(payment_id); now=utc_now_iso()
        with self.auth.connection() as c:
            number=c.execute("SELECT COUNT(*)+1 FROM TechnicianPaymentEmailDrafts WHERE technician_payment_id=?",(payment_id,)).fetchone()[0]
            c.execute("UPDATE TechnicianPaymentEmailDrafts SET draft_status='Outdated' WHERE technician_payment_id=? AND draft_status='Draft Generated'",(payment_id,))
            draft_id=int(c.execute("""INSERT INTO TechnicianPaymentEmailDrafts
              (technician_payment_id,recipient_email,generated_at,generated_by,generation_number,draft_status)
              VALUES(?,?,?,?,?,'Draft Generated')""",(payment_id,draft["recipient"],now,session.user_id,number)).lastrowid)
            record_event(c,"technician_payment_email_draft_generated",actor_user_id=session.user_id,
                         details={"payment_id":payment_id,"draft_id":draft_id,"recipient":draft["recipient"],"generation_number":number})
        return {**draft,"draft_id":draft_id,"generation_number":number,"generated_at":now}

    def get_payment_email_status(self, payment_id: int):
        self._id(payment_id,"technician_payment_id")
        with self.auth.connection() as c:
            row=c.execute("""SELECT * FROM TechnicianPaymentEmailDrafts WHERE technician_payment_id=?
              ORDER BY technician_payment_email_draft_id DESC LIMIT 1""",(payment_id,)).fetchone()
            return dict(row) if row else None

    def create_direct_payment(self, session: Session, *, technician_id: int,
                              payment_date: str, category: str, amount_cents: int,
                              description: str, status: str = "Draft",
                              job_id: int | None = None, financial_component: str | None = None,
                              payment_method: str = "ACH", reference: str | None = None):
        """Create a direct transaction using the established earning/allocation ledger.

        A direct item has its own one-technician run, but no Matterport batch.  Its
        manual earning is the payable obligation and ``TechnicianPaymentEarnings``
        remains the sole allocation source of truth.
        """
        self._write(session); self._id(technician_id, "technician_id")
        if category not in DIRECT_PAYMENT_CATEGORIES + LEGACY_DIRECT_PAYMENT_CATEGORIES:
            raise ValueError("Unsupported direct payment category")
        if status not in DIRECT_PAYMENT_STATUSES:
            raise ValueError("Unsupported direct payment status")
        if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents <= 0:
            raise ValueError("amount_cents must be a positive integer")
        if payment_method not in PAYMENT_METHODS:
            raise ValueError("Unsupported payment method")
        description = (description or "").strip()
        if not description: raise ValueError("Description is required")
        if job_id is not None: self._id(job_id, "job_id")
        stored_status = {"Draft": "Pending", "Approved": "Approved",
                         "Scheduled": "Submitted", "Paid": "Paid"}[status]
        earning_status = "Pending" if status == "Draft" else "Approved"
        now = utc_now_iso()
        with self.auth.connection() as c:
            if not c.execute("SELECT 1 FROM Techs WHERE tech_id=?", (technician_id,)).fetchone():
                raise LookupError("Technician not found")
            if job_id is not None and not c.execute("SELECT 1 FROM Jobs WHERE job_id=?", (job_id,)).fetchone():
                raise LookupError("Job not found")
            run_id = int(c.execute("""INSERT INTO TechnicianPaymentRuns
              (source_payment_batch_id,payment_run_date,payment_status,total_amount_cents,notes,
               created_at,created_by,run_type) VALUES (?,?,?, ?,?,?,?,'Direct')""",
              (None, payment_date, "Paid" if status == "Paid" else
               "Submitted" if status == "Scheduled" else status, amount_cents,
               description, now, session.user_id)).lastrowid)
            details = __import__("json").dumps({"direct_payment": True, "category": category,
                "financial_component": financial_component})
            earning_id = int(c.execute("""INSERT INTO TechnicianJobEarnings
              (tech_id,job_id,entry_type,revenue_basis_cents,calculated_amount_cents,
               adjustment_amount_cents,net_earning_cents,earning_status,calculation_details_json,
               reason,created_at,created_by,approved_at,approved_by)
              VALUES (?,?,'Manual Adjustment',0,0,?,?,?, ?,?,?,?, ?,?)""",
              (technician_id,job_id,amount_cents,amount_cents,earning_status,details,
               description,now,session.user_id,now if status != "Draft" else None,
               session.user_id if status != "Draft" else None)).lastrowid)
            payment_id = int(c.execute("""INSERT INTO TechnicianPayments
              (technician_payment_run_id,tech_id,payment_amount_cents,actual_amount_cents,
               payment_method,payment_status,payment_date,payment_reference,recorded_at,recorded_by,
               created_at,payment_kind,payment_category,financial_component,description)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (run_id,technician_id,amount_cents,amount_cents if status == "Paid" else None,
               payment_method,stored_status,payment_date,reference,now if status == "Paid" else None,
               session.user_id if status == "Paid" else None,now,"Direct",category,
               financial_component,description)).lastrowid)
            c.execute("INSERT INTO TechnicianPaymentEarnings(technician_payment_id,technician_earning_id,amount_applied_cents,created_at) VALUES (?,?,?,?)",
                      (payment_id,earning_id,amount_cents,now))
            if status == "Paid":
                c.execute("UPDATE TechnicianJobEarnings SET earning_status='Paid',paid_at=? WHERE technician_earning_id=?", (now,earning_id))
            record_event(c,"direct_technician_payment_created",actor_user_id=session.user_id,
              details={"payment_id":payment_id,"technician_id":technician_id,"job_id":job_id,
                       "category":category,"component":financial_component,"amount_cents":amount_cents,
                       "status":status,"timestamp":now})
            return dict(c.execute("SELECT * FROM TechnicianPayments WHERE technician_payment_id=?",(payment_id,)).fetchone())

    def void_direct_payment(self, session: Session, payment_id: int, reason: str):
        """Cancel an unpaid item or append a reversal for a paid direct payment."""
        self._write(session); self._id(payment_id, "payment_id")
        reason = (reason or "").strip()
        if not reason: raise ValueError("Reversal reason is required")
        with self.auth.connection() as c:
            payment = c.execute("SELECT * FROM TechnicianPayments WHERE technician_payment_id=? AND payment_kind='Direct'", (payment_id,)).fetchone()
            if not payment: raise LookupError("Direct payment not found")
            if payment["payment_status"] == "Cancelled": raise ValueError("Payment is already voided")
            now=utc_now_iso()
            c.execute("UPDATE TechnicianPayments SET payment_status='Cancelled',updated_at=?,notes=? WHERE technician_payment_id=?", (now,reason,payment_id))
            c.execute("UPDATE TechnicianPaymentRuns SET payment_status='Cancelled',cancelled_at=?,cancelled_by=? WHERE technician_payment_run_id=?", (now,session.user_id,payment["technician_payment_run_id"]))
            links=c.execute("SELECT technician_earning_id FROM TechnicianPaymentEarnings WHERE technician_payment_id=?",(payment_id,)).fetchall()
            for link in links:
                c.execute("UPDATE TechnicianJobEarnings SET earning_status='Voided',voided_at=?,voided_by=?,void_reason=? WHERE technician_earning_id=?",(now,session.user_id,reason,link[0]))
            record_event(c,"direct_technician_payment_voided",actor_user_id=session.user_id,details={"payment_id":payment_id,"reason":reason,"timestamp":now})

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
                    c.execute("UPDATE TechnicianJobEarnings SET included_in_payment_run_id=?, "
                              "included_in_payment_run_at=? WHERE technician_earning_id=?",
                              (run_id,now,earning["technician_earning_id"]))
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
                c.execute("UPDATE TechnicianJobEarnings SET included_in_payment_run_id=?, "
                          "included_in_payment_run_at=? WHERE technician_earning_id=?",(run_id,now,eid))
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
                c.execute("UPDATE TechnicianJobEarnings SET included_in_payment_run_id=NULL, "
                          "included_in_payment_run_at=NULL WHERE technician_earning_id=?",(eid,))
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
            if ids:
                placeholders=",".join("?" for _ in ids)
                c.execute(f"UPDATE TechnicianJobEarnings SET included_in_payment_run_id=NULL, "
                          f"included_in_payment_run_at=NULL WHERE technician_earning_id IN ({placeholders})",ids)
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
