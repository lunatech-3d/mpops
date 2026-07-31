"""Compensation calculation and append-only technician earnings ledger services."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError
from app.services.revenue_rule_service import (RevenueRuleService, RuleConfigurationError,
                                               RuleDataIntegrityError)

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
    def rule_effective_date(job: sqlite3.Row, payment_date: Any) -> str | None:
        """Return the financial-rule business date, never the current system date.

        Job completion, actual start, and scheduled start take precedence over the
        payment date. Timestamp values contribute only their calendar date.
        """
        for value in (job["completed_at"], job["actual_start_at"],
                      job["scheduled_start_at"], payment_date):
            if value is None or isinstance(value, bool):
                continue
            try:
                if isinstance(value, datetime):
                    return value.date().isoformat()
                if isinstance(value, date):
                    return value.isoformat()
                text = str(value).strip()
                return date.fromisoformat(text[:10]).isoformat()
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _financial_cents(value: Any) -> int:
        try:
            cents = (Decimal(str(value)) * Decimal(100)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP)
        except Exception as exc:
            raise ValueError("Financial component is not a valid decimal amount") from exc
        if not cents.is_finite() or cents < 0:
            raise ValueError("Financial component must be nonnegative")
        return int(cents)

    @staticmethod
    def _rule_source(rule: dict[str, Any]) -> str:
        return {"Job": "Job Override", "Technician": "Technician Default",
                "Market": "Market Default", "System": "System Default"}[rule["scope_type"]]

    @staticmethod
    def _exception(base: dict[str, Any], code: str, message: str) -> dict[str, Any]:
        return {**base, "reason_code": code, "message": message}

    def _preview(self, connection: sqlite3.Connection, batch_id: int) -> dict[str, Any]:
        batch = connection.execute("SELECT * FROM MatterportPaymentBatches WHERE payment_batch_id=?",
                                   (batch_id,)).fetchone()
        if not batch:
            raise LookupError("Payment batch not found")
        entries, exceptions = [], []
        has_market = any(r[1] == "market_id" for r in connection.execute("PRAGMA table_info(Jobs)"))
        market_select = "j.market_id" if has_market else "NULL AS market_id"
        items = connection.execute(f"""
          SELECT i.*,j.external_job_id,j.completed_at,j.actual_start_at,j.scheduled_start_at,
                 {market_select},m.market_name
          FROM MatterportPaymentItems i LEFT JOIN Jobs j ON j.job_id=i.job_id
          LEFT JOIN Markets m ON m.market_id=j.market_id
          WHERE i.payment_batch_id=? AND i.match_status<>'Excluded' ORDER BY i.payment_item_id
        """, (batch_id,)).fetchall()
        if batch["batch_status"] not in ELIGIBLE_BATCH_STATUSES:
            exceptions.append({"payment_item_id": None, "job_id": None, "document_number": None,
              "reason_code": "BATCH_NOT_ELIGIBLE", "message": "Batch must be Reconciled, Approved, or Closed."})
        else:
            for item in items:
                base = {"payment_item_id": item["payment_item_id"], "job_id": item["job_id"],
                        "document_number": item["document_number"]}
                if item["match_status"] != "Matched":
                    exceptions.append(self._exception(base, "ITEM_NOT_MATCHED", "Payment item is not matched.")); continue
                if item["job_id"] is None or item["external_job_id"] is None:
                    exceptions.append(self._exception(base, "MISSING_JOB", "Payment item has no valid matched job.")); continue
                if item["market_id"] is None:
                    exceptions.append(self._exception(base, "MISSING_MARKET", "Matched job has no market.")); continue
                effective_date = self.rule_effective_date(item, batch["payment_date"])
                if effective_date is None:
                    exceptions.append(self._exception(base, "NO_RULE_EFFECTIVE_DATE",
                        "No job or payment date is available for financial rule resolution.")); continue
                techs = self._technician(connection, item)
                if len(techs) != 1:
                    code = "MISSING_PRIMARY_TECHNICIAN" if not techs else "MULTIPLE_PRIMARY_TECHNICIANS"
                    message = ("No eligible primary technician assignment found." if not techs else
                               "Multiple eligible primary technician assignments found.")
                    exceptions.append({**base, "reason_code": code, "message": message}); continue
                tech = techs[0]
                gross_value = (item["resolved_amount_cents"] if item["resolved_amount_cents"] is not None
                               else item["amount_received_cents"])
                if isinstance(gross_value, bool) or not isinstance(gross_value, int) or gross_value < 0:
                    exceptions.append(self._exception(base, "INVALID_FINANCIAL_AMOUNT",
                        "Gross revenue must be a nonnegative integer number of cents.")); continue
                revenue = gross_value
                financial = connection.execute("""SELECT
                    COALESCE(SUM(ct_rate),0), COALESCE(SUM(ct_travel_payout),0),
                    COALESCE(SUM(ct_off_hours_payout),0)
                    FROM JobFinancials WHERE job_id=?""", (item["job_id"],)).fetchone()
                try:
                    component_revenue = [("Base", self._financial_cents(financial[0])),
                                         ("Travel", self._financial_cents(financial[1])),
                                         ("Off Hours", self._financial_cents(financial[2]))]
                except ValueError as exc:
                    exceptions.append(self._exception(base, "INVALID_FINANCIAL_AMOUNT", str(exc))); continue
                # Imported receipts remain the safe basis for older jobs which do not
                # yet have component financial records.
                if not any(value for _, value in component_revenue):
                    component_revenue = [("Overall", revenue)]
                components, amount = [], 0
                resolver = RevenueRuleService(self.auth)
                for component, basis in component_revenue:
                    if not basis:
                        continue
                    try:
                        rule = resolver.resolve_technician_rule(job_id=item["job_id"],
                            tech_id=tech["tech_id"], market_id=item["market_id"],
                            effective_date=effective_date, compensation_component=component)
                    except RuleConfigurationError as exc:
                        exceptions.append(self._exception(base, "NO_TECHNICIAN_RULE", str(exc))); break
                    except RuleDataIntegrityError as exc:
                        exceptions.append(self._exception(base, "AMBIGUOUS_TECHNICIAN_RULE", str(exc))); break
                    source = self._rule_source(rule)
                    calculated = self.calculate_amount(basis, rule["rule_type"], int(rule["rule_value"]))
                    amount += calculated
                    components.append({"component": component, "revenue_basis_cents": basis,
                        "compensation_rule_id": int(rule["compensation_rule_id"]),
                        "rule_type": rule["rule_type"], "rule_value": int(rule["rule_value"]),
                        "rule_scope_type": rule["scope_type"], "rule_scope_id": rule["scope_id"],
                        "resolved_component": rule["compensation_component"],
                        "rule_source": source, "calculated_amount_cents": calculated})
                if len(components) != len([x for x in component_revenue if x[1]]): continue
                if amount > revenue:
                    exceptions.append(self._exception(base, "TECHNICIAN_AMOUNT_EXCEEDS_GROSS",
                        "Technician compensation exceeds gross payment revenue.")); continue
                try:
                    east_rule = resolver.resolve_market_revenue_rule(market_id=item["market_id"],
                        effective_date=effective_date, recipient_code="LUNATECH_EAST")
                except RuleConfigurationError as exc:
                    exceptions.append(self._exception(base, "NO_MARKET_REVENUE_RULE", str(exc))); continue
                except RuleDataIntegrityError as exc:
                    exceptions.append(self._exception(base, "AMBIGUOUS_MARKET_REVENUE_RULE", str(exc))); continue
                east_bp = int(east_rule["share_basis_points"])
                contractual = (len(components) == 1 and components[0]["component"] == "Overall"
                               and components[0]["rule_type"] == "Percentage")
                tech_bp = (components[0]["rule_value"] if contractual else
                           (int((Decimal(amount) * 10000 / Decimal(revenue)).quantize(
                                Decimal("1"), rounding=ROUND_HALF_UP)) if revenue else 0))
                if tech_bp + east_bp > 10000:
                    exceptions.append(self._exception(base, "REVENUE_PERCENTAGES_EXCEED_100",
                        "Technician and LunaTech-East shares exceed 100%.")); continue
                east_amount = int((Decimal(revenue) * Decimal(east_bp) / Decimal(10000)).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP))
                lunatech_amount = revenue - amount - east_amount
                if lunatech_amount < 0:
                    exceptions.append(self._exception(base, "REVENUE_PERCENTAGES_EXCEED_100",
                        "Calculated company shares exceed gross revenue.")); continue
                lunatech_bp = 10000 - tech_bp - east_bp
                rule = components[0]
                sources = {part["rule_source"] for part in components}
                source = next(iter(sources)) if len(sources) == 1 else "Mixed Component Rules"
                existing = connection.execute("SELECT * FROM TechnicianJobEarnings "
                    "WHERE payment_item_id=? AND tech_id=? AND entry_type='Calculated' "
                    "AND earning_status<>'Voided'", (item["payment_item_id"], tech["tech_id"])).fetchone()
                allocation = connection.execute("SELECT * FROM CompanyRevenueAllocations "
                    "WHERE payment_item_id=? AND allocation_status<>'Superseded'",
                    (item["payment_item_id"],)).fetchone()
                if existing and (existing["revenue_basis_cents"] != revenue or
                        existing["calculated_amount_cents"] != amount):
                    exceptions.append(self._exception(base, "EXISTING_CALCULATION_DIFFERS",
                        "Current technician earning differs from the resolved calculation.")); continue
                if allocation and (allocation["gross_revenue_cents"] != revenue or
                        allocation["technician_amount_cents"] != amount or
                        allocation["lunatech_east_amount_cents"] != east_amount or
                        allocation["lunatech_amount_cents"] != lunatech_amount or
                        allocation["market_revenue_share_rule_id"] != int(east_rule["market_revenue_share_rule_id"])):
                    exceptions.append(self._exception(base, "EXISTING_CALCULATION_DIFFERS",
                        "Current company allocation differs from the resolved calculation.")); continue
                entries.append({**base, "external_job_id": item["external_job_id"],
                    "technician_id": tech["tech_id"], "technician_name": " ".join(filter(None,
                    (tech["preferred_name"] or tech["first_name"], tech["last_name"]))),
                    "market_id": item["market_id"], "market_name": item["market_name"],
                    "effective_rule_date": effective_date,
                    "revenue_basis_cents": revenue, "rule_type": rule["rule_type"],
                    "rule_value": int(rule["rule_value"]), "rule_source": source,
                    "components": components,
                    "effective_rate_display": (f"{(Decimal(amount) * 100 / Decimal(revenue)):.2f}%"
                                               if revenue else "—"),
                    "calculated_amount_cents": amount,
                    "technician_rule_ids": [c["compensation_rule_id"] for c in components],
                    "technician_percentage_is_contractual": contractual,
                    "technician_share_basis_points": tech_bp,
                    "market_revenue_share_rule_id": int(east_rule["market_revenue_share_rule_id"]),
                    "lunatech_east_share_basis_points": east_bp,
                    "lunatech_east_amount_cents": east_amount,
                    "lunatech_share_basis_points": lunatech_bp,
                    "lunatech_amount_cents": lunatech_amount,
                    "existing_earning_id": int(existing["technician_earning_id"]) if existing else None,
                    "existing_allocation_id": int(allocation["company_revenue_allocation_id"]) if allocation else None})
        proposed = [e for e in entries if e["existing_allocation_id"] is None]
        return {"payment_batch_id": batch_id, "batch_status": batch["batch_status"],
                "ready": not exceptions, "summary": {"eligible_item_count": len(entries),
                "proposed_earning_count": len(proposed), "exception_count": len(exceptions),
                "revenue_basis_total_cents": sum(e["revenue_basis_cents"] for e in proposed),
                "proposed_earnings_total_cents": sum(e["calculated_amount_cents"] for e in proposed),
                "gross_revenue_total_cents": sum(e["revenue_basis_cents"] for e in entries),
                "technician_total_cents": sum(e["calculated_amount_cents"] for e in entries),
                "lunatech_east_total_cents": sum(e["lunatech_east_amount_cents"] for e in entries),
                "lunatech_total_cents": sum(e["lunatech_amount_cents"] for e in entries),
                "unallocated_total_cents": sum((i["resolved_amount_cents"] if i["resolved_amount_cents"] is not None else i["amount_received_cents"] or 0) for i in items if any(x["payment_item_id"] == i["payment_item_id"] for x in exceptions))},
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
            new_entries = [e for e in all_entries if e["existing_allocation_id"] is None]
            if not new_entries:  # documented idempotent no-op
                ids = [e["existing_earning_id"] for e in all_entries]
                return self._generation_result(payment_batch_id, all_entries, ids, True)
            now, ids, allocation_ids = utc_now_iso(), [], []
            for entry in new_entries:
                details = {"effective_rule_date": entry["effective_rule_date"],
                    "gross_revenue_basis_cents": entry["revenue_basis_cents"],
                    "technician_id": entry["technician_id"], "market_id": entry["market_id"],
                    "rule_type": entry["rule_type"], "rule_source": entry["rule_source"],
                    "rounding_method": "ROUND_HALF_UP", "technician_components": entry.get("components", []),
                    "technician_compensation_rule_ids": entry["technician_rule_ids"],
                    "market_revenue_share_rule_id": entry["market_revenue_share_rule_id"],
                    "east_basis_points": entry["lunatech_east_share_basis_points"],
                    "technician_percentage_kind": ("contractual" if entry["technician_percentage_is_contractual"] else "derived"),
                    "final_amounts_cents": {"technician": entry["calculated_amount_cents"],
                        "lunatech_east": entry["lunatech_east_amount_cents"],
                        "lunatech": entry["lunatech_amount_cents"]}}
                details["rule_value_basis_points" if entry["rule_type"] == "Percentage" else
                        "rule_value_cents"] = entry["rule_value"]
                earning_id = entry["existing_earning_id"]
                if earning_id is not None:
                    stored = connection.execute("SELECT * FROM TechnicianJobEarnings WHERE technician_earning_id=?",
                                                (earning_id,)).fetchone()
                    if (stored["payment_item_id"] != entry["payment_item_id"] or
                            stored["tech_id"] != entry["technician_id"] or
                            stored["revenue_basis_cents"] != entry["revenue_basis_cents"] or
                            stored["calculated_amount_cents"] != entry["calculated_amount_cents"]):
                        raise ValueError("EXISTING_CALCULATION_DIFFERS: existing earning requires review")
                else:
                    cursor = connection.execute("""INSERT INTO TechnicianJobEarnings
                      (tech_id,job_id,payment_batch_id,payment_item_id,entry_type,revenue_basis_cents,
                       compensation_rule_type,compensation_rule_value,calculated_amount_cents,
                       adjustment_amount_cents,net_earning_cents,earning_status,calculation_details_json,
                       created_at,created_by) VALUES (?,?,?,?, 'Calculated',?,?,?,?,0,?,'Pending',?,?,?)""",
                      (entry["technician_id"],entry["job_id"],payment_batch_id,entry["payment_item_id"],
                       entry["revenue_basis_cents"],entry["rule_type"],entry["rule_value"],
                       entry["calculated_amount_cents"],entry["calculated_amount_cents"],
                       json.dumps(details, sort_keys=True),now,session.user_id))
                    earning_id = int(cursor.lastrowid)
                ids.append(int(earning_id))
                cursor = connection.execute("""INSERT INTO CompanyRevenueAllocations
                  (payment_item_id,job_id,market_id,gross_revenue_cents,technician_earning_id,
                   technician_share_basis_points,technician_amount_cents,
                   lunatech_east_share_basis_points,lunatech_east_amount_cents,
                   lunatech_share_basis_points,lunatech_amount_cents,market_revenue_share_rule_id,
                   allocation_status,calculation_details_json,created_at,created_by)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'Calculated',?,?,?)""",
                  (entry["payment_item_id"],entry["job_id"],entry["market_id"],entry["revenue_basis_cents"],
                   earning_id,entry["technician_share_basis_points"],entry["calculated_amount_cents"],
                   entry["lunatech_east_share_basis_points"],entry["lunatech_east_amount_cents"],
                   entry["lunatech_share_basis_points"],entry["lunatech_amount_cents"],
                   entry["market_revenue_share_rule_id"],json.dumps(details, sort_keys=True),now,session.user_id))
                allocation_ids.append(int(cursor.lastrowid))
            result = self._generation_result(payment_batch_id, new_entries, ids, False)
            result["allocation_ids"] = allocation_ids
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
