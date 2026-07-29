"""Compensation calculation and append-only technician earnings ledger services."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError

ELIGIBLE_BATCH_STATUSES = frozenset({"Reconciled", "Approved", "Closed"})
FINANCIAL_FIELDS = frozenset({"revenue_basis_cents", "compensation_rule_type",
                              "compensation_rule_value", "calculated_amount_cents",
                              "adjustment_amount_cents", "net_earning_cents"})


class CompensationService:
    """Evaluate current rules, then persist their immutable financial result."""

    def __init__(self, auth: AuthService):
        self.auth = auth

    @staticmethod
    def _write(session: Session | None) -> None:
        if session is None or session.role not in {"admin", "operator"}:
            raise AuthorizationError("Administrator or operator role required")

    @staticmethod
    def _id(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _reason(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("reason is required")
        value = value.strip()
        if len(value) > 1000:
            raise ValueError("reason may not exceed 1000 characters")
        return value

    @staticmethod
    def calculate_amount(revenue_cents: int, rule_type: str, rule_value: int) -> int:
        if rule_type == "Percentage":
            return int((Decimal(revenue_cents) * Decimal(rule_value) / Decimal(10000))
                       .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if rule_type == "Flat Amount":
            return rule_value
        raise ValueError("Unsupported compensation rule type")

    @staticmethod
    def _technician(connection: sqlite3.Connection, job: sqlite3.Row):
        completed = job["completed_at"]
        rows = connection.execute("""
          SELECT a.*,t.first_name,t.last_name,t.preferred_name,t.status AS technician_status
          FROM JobAssignments a JOIN Techs t ON t.tech_id=a.tech_id
          WHERE a.job_id=? AND a.assignment_role='Primary' AND t.status='Active'
          ORDER BY a.job_assignment_id
        """, (job["job_id"],)).fetchall()
        # A completed assignment explicitly records who performed the work.
        explicit = [r for r in rows if r["assignment_status"] == "Completed" or r["completed_at"]]
        if explicit:
            return explicit
        if completed:
            historical = [r for r in rows if r["assigned_at"] <= completed and
                          (r["unassigned_at"] is None or r["unassigned_at"] >= completed)]
            if historical:
                return historical
        return [r for r in rows if r["unassigned_at"] is None and
                r["assignment_status"] == "Assigned"]

    @staticmethod
    def _rule(connection: sqlite3.Connection, job: sqlite3.Row, tech_id: int):
        choices = (("Job", job["job_id"], "Job Override"),
                   ("Technician", tech_id, "Technician Default"),
                   ("Market", job["market_id"], "Market Default"),
                   ("System", None, "System Default"))
        for scope, scope_id, label in choices:
            if scope != "System" and scope_id is None:
                continue
            row = connection.execute(
                "SELECT * FROM TechnicianCompensationRules WHERE scope_type=? "
                "AND scope_id IS ? AND is_active=1", (scope, scope_id)).fetchone()
            if row:
                return row, label
        return None, None

    def _preview(self, connection: sqlite3.Connection, batch_id: int) -> dict[str, Any]:
        batch = connection.execute("SELECT * FROM MatterportPaymentBatches WHERE payment_batch_id=?",
                                   (batch_id,)).fetchone()
        if not batch:
            raise LookupError("Payment batch not found")
        entries, exceptions = [], []
        has_market = any(r[1] == "market_id" for r in connection.execute("PRAGMA table_info(Jobs)"))
        market_select = "j.market_id" if has_market else "NULL AS market_id"
        items = connection.execute(f"""
          SELECT i.*,j.external_job_id,j.completed_at,{market_select}
          FROM MatterportPaymentItems i LEFT JOIN Jobs j ON j.job_id=i.job_id
          WHERE i.payment_batch_id=? AND i.match_status<>'Excluded' ORDER BY i.payment_item_id
        """, (batch_id,)).fetchall()
        if batch["batch_status"] not in ELIGIBLE_BATCH_STATUSES:
            exceptions.append({"payment_item_id": None, "job_id": None, "document_number": None,
              "reason_code": "BATCH_NOT_ELIGIBLE", "message": "Batch must be Reconciled, Approved, or Closed."})
        else:
            for item in items:
                base = {"payment_item_id": item["payment_item_id"], "job_id": item["job_id"],
                        "document_number": item["document_number"]}
                if item["match_status"] != "Matched" or item["job_id"] is None or item["external_job_id"] is None:
                    exceptions.append({**base, "reason_code": "ITEM_NOT_MATCHED",
                                       "message": "Payment item is not matched to a valid job."}); continue
                techs = self._technician(connection, item)
                if len(techs) != 1:
                    code = "NO_TECHNICIAN" if not techs else "AMBIGUOUS_TECHNICIAN"
                    message = ("No eligible primary technician assignment found." if not techs else
                               "Multiple eligible primary technician assignments found.")
                    exceptions.append({**base, "reason_code": code, "message": message}); continue
                tech = techs[0]
                rule, source = self._rule(connection, item, tech["tech_id"])
                if not rule:
                    exceptions.append({**base, "reason_code": "NO_COMPENSATION_RULE",
                                       "message": "No valid compensation rule could be resolved."}); continue
                revenue = int(item["resolved_amount_cents"] if item["resolved_amount_cents"] is not None
                              else item["amount_received_cents"])
                amount = self.calculate_amount(revenue, rule["rule_type"], int(rule["rule_value"]))
                existing = connection.execute("SELECT technician_earning_id FROM TechnicianJobEarnings "
                    "WHERE payment_item_id=? AND tech_id=? AND entry_type='Calculated' "
                    "AND earning_status<>'Voided'", (item["payment_item_id"], tech["tech_id"])).fetchone()
                entries.append({**base, "external_job_id": item["external_job_id"],
                    "technician_id": tech["tech_id"], "technician_name": " ".join(filter(None,
                    (tech["preferred_name"] or tech["first_name"], tech["last_name"]))),
                    "revenue_basis_cents": revenue, "rule_type": rule["rule_type"],
                    "rule_value": int(rule["rule_value"]), "rule_source": source,
                    "calculated_amount_cents": amount,
                    "existing_earning_id": int(existing[0]) if existing else None})
        proposed = [e for e in entries if e["existing_earning_id"] is None]
        return {"payment_batch_id": batch_id, "batch_status": batch["batch_status"],
                "ready": not exceptions, "summary": {"eligible_item_count": len(entries),
                "proposed_earning_count": len(proposed), "exception_count": len(exceptions),
                "revenue_basis_total_cents": sum(e["revenue_basis_cents"] for e in proposed),
                "proposed_earnings_total_cents": sum(e["calculated_amount_cents"] for e in proposed)},
                "proposed_entries": entries, "exceptions": exceptions}

    def preview_technician_earnings(self, payment_batch_id: int) -> dict[str, Any]:
        self._id(payment_batch_id, "payment_batch_id")
        with self.auth.connection() as connection:
            return self._preview(connection, payment_batch_id)

    def generate_technician_earnings(self, session: Session, payment_batch_id: int) -> dict[str, Any]:
        self._write(session); self._id(payment_batch_id, "payment_batch_id")
        with self.auth.connection() as connection:
            preview = self._preview(connection, payment_batch_id)
            if preview["exceptions"]:
                raise ValueError("Earnings generation blocked by preview exceptions")
            all_entries = preview["proposed_entries"]
            new_entries = [e for e in all_entries if e["existing_earning_id"] is None]
            if not new_entries:  # documented idempotent no-op
                ids = [e["existing_earning_id"] for e in all_entries]
                return self._generation_result(payment_batch_id, all_entries, ids, True)
            if len(new_entries) != len(all_entries):
                raise ValueError("Batch has been partially generated; no entries were created")
            now, ids = utc_now_iso(), []
            for entry in new_entries:
                details = {"revenue_basis_cents": entry["revenue_basis_cents"],
                    "rule_type": entry["rule_type"], "rule_source": entry["rule_source"],
                    "rounding_method": "ROUND_HALF_UP", "calculated_amount_cents": entry["calculated_amount_cents"]}
                details["rule_value_basis_points" if entry["rule_type"] == "Percentage" else
                        "rule_value_cents"] = entry["rule_value"]
                cursor = connection.execute("""INSERT INTO TechnicianJobEarnings
                  (tech_id,job_id,payment_batch_id,payment_item_id,entry_type,revenue_basis_cents,
                   compensation_rule_type,compensation_rule_value,calculated_amount_cents,
                   adjustment_amount_cents,net_earning_cents,earning_status,calculation_details_json,
                   created_at,created_by) VALUES (?,?,?,?, 'Calculated',?,?,?,?,0,?,'Pending',?,?,?)""",
                  (entry["technician_id"],entry["job_id"],payment_batch_id,entry["payment_item_id"],
                   entry["revenue_basis_cents"],entry["rule_type"],entry["rule_value"],
                   entry["calculated_amount_cents"],entry["calculated_amount_cents"],
                   json.dumps(details, sort_keys=True),now,session.user_id))
                ids.append(int(cursor.lastrowid))
            result = self._generation_result(payment_batch_id, new_entries, ids, False)
            record_event(connection, "technician_earnings_generated", actor_user_id=session.user_id,
                         details={**result, "actor": session.user_id, "timestamp": now})
            return result

    @staticmethod
    def _generation_result(batch_id, entries, ids, idempotent):
        return {"payment_batch_id": batch_id, "generated_count": 0 if idempotent else len(ids),
                "earning_ids": ids, "revenue_basis_total_cents": sum(e["revenue_basis_cents"] for e in entries),
                "earnings_total_cents": sum(e["calculated_amount_cents"] for e in entries),
                "idempotent": idempotent}

    def create_manual_earning_adjustment(self, session: Session, technician_id: int,
            amount_cents: int, reason: str, job_id: int | None = None,
            related_earning_id: int | None = None) -> int:
        self._write(session); self._id(technician_id, "technician_id")
        if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents == 0:
            raise ValueError("amount_cents must be non-zero integer cents")
        reason = self._reason(reason)
        with self.auth.connection() as connection:
            tech = connection.execute("SELECT status FROM Techs WHERE tech_id=?", (technician_id,)).fetchone()
            if not tech: raise LookupError("Technician not found")
            if tech[0] != "Active": raise ValueError("Technician is not eligible")
            if job_id and not connection.execute("SELECT 1 FROM Jobs WHERE job_id=?", (job_id,)).fetchone():
                raise LookupError("Job not found")
            if related_earning_id and not connection.execute("SELECT 1 FROM TechnicianJobEarnings WHERE technician_earning_id=?", (related_earning_id,)).fetchone():
                raise LookupError("Related earning not found")
            now = utc_now_iso()
            cursor = connection.execute("""INSERT INTO TechnicianJobEarnings
              (tech_id,job_id,entry_type,source_earning_id,calculated_amount_cents,
               adjustment_amount_cents,net_earning_cents,earning_status,reason,created_at,created_by)
              VALUES (?,?,'Manual Adjustment',?,0,?,?,'Pending',?,?,?)""",
              (technician_id,job_id,related_earning_id,amount_cents,amount_cents,reason,now,session.user_id))
            earning_id = int(cursor.lastrowid)
            record_event(connection,"technician_earning_adjustment_created",actor_user_id=session.user_id,
              details={"earning_id":earning_id,"technician_id":technician_id,"job_id":job_id,
              "related_earning_id":related_earning_id,"amount_cents":amount_cents,"reason":reason,
              "actor":session.user_id,"timestamp":now})
            return earning_id

    def void_technician_earning(self, session: Session, earning_id: int, reason: str) -> dict[str, Any]:
        self._write(session); self._id(earning_id,"earning_id"); reason=self._reason(reason)
        with self.auth.connection() as connection:
            row=connection.execute("SELECT * FROM TechnicianJobEarnings WHERE technician_earning_id=?",(earning_id,)).fetchone()
            if not row: raise LookupError("Technician earning not found")
            if row["earning_status"] not in {"Pending","Approved"}:
                raise ValueError("Only Pending or Approved earnings may be voided")
            now=utc_now_iso(); previous=row["earning_status"]
            connection.execute("UPDATE TechnicianJobEarnings SET earning_status='Voided',voided_at=?,voided_by=?,void_reason=? WHERE technician_earning_id=?",
                               (now,session.user_id,reason,earning_id))
            record_event(connection,"technician_earning_voided",actor_user_id=session.user_id,
              details={"earning_id":earning_id,"previous_status":previous,"new_status":"Voided",
                       "reason":reason,"actor":session.user_id,"timestamp":now})
            return self._get(connection,earning_id)

    @staticmethod
    def _get(connection, earning_id):
        row=connection.execute("""SELECT e.*,t.first_name,t.last_name,j.external_job_id,i.document_number
          FROM TechnicianJobEarnings e JOIN Techs t ON t.tech_id=e.tech_id
          LEFT JOIN Jobs j ON j.job_id=e.job_id LEFT JOIN MatterportPaymentItems i ON i.payment_item_id=e.payment_item_id
          WHERE e.technician_earning_id=?""",(earning_id,)).fetchone()
        if not row: raise LookupError("Technician earning not found")
        return dict(row)

    def get_technician_earning(self, earning_id: int):
        self._id(earning_id,"earning_id")
        with self.auth.connection() as c: return self._get(c,earning_id)

    def list_technician_earnings(self, technician_id=None, payment_batch_id=None, status=None):
        clauses=[]; params=[]
        for column,value in (("e.tech_id",technician_id),("e.payment_batch_id",payment_batch_id),("e.earning_status",status)):
            if value is not None: clauses.append(column+"=?"); params.append(value)
        where=" WHERE "+" AND ".join(clauses) if clauses else ""
        with self.auth.connection() as c:
            return [dict(r) for r in c.execute("""SELECT e.*,t.first_name,t.last_name,j.external_job_id,i.document_number
              FROM TechnicianJobEarnings e JOIN Techs t ON t.tech_id=e.tech_id LEFT JOIN Jobs j ON j.job_id=e.job_id
              LEFT JOIN MatterportPaymentItems i ON i.payment_item_id=e.payment_item_id"""+where+" ORDER BY e.technician_earning_id",params)]

    def get_payment_batch_earnings_summary(self, payment_batch_id: int):
        self._id(payment_batch_id,"payment_batch_id")
        with self.auth.connection() as c:
            row=c.execute("""SELECT COUNT(*) entry_count,COALESCE(SUM(net_earning_cents),0) net_earning_cents,
              COALESCE(SUM(CASE WHEN earning_status='Approved' THEN net_earning_cents ELSE 0 END),0) payable_cents
              FROM TechnicianJobEarnings WHERE payment_batch_id=? AND earning_status<>'Voided'""",(payment_batch_id,)).fetchone()
            return dict(row)

    def get_technician_pending_earnings_summary(self, technician_id: int):
        self._id(technician_id,"technician_id")
        with self.auth.connection() as c:
            row=c.execute("SELECT COUNT(*) entry_count,COALESCE(SUM(net_earning_cents),0) net_earning_cents FROM TechnicianJobEarnings WHERE tech_id=? AND earning_status='Pending'",(technician_id,)).fetchone()
            return dict(row)

    def update_technician_earning(self, session: Session, earning_id: int, changes: dict[str, Any]):
        """No general financial update surface exists; retained as an explicit guard."""
        self._write(session)
        if FINANCIAL_FIELDS.intersection(changes):
            raise ValueError("Ledger financial fields are immutable; void or adjust the earning")
        raise ValueError("Technician earnings cannot be edited through normal update methods")
