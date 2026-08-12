"""Compensation calculation and append-only technician earnings ledger services."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError
from app.services.revenue_rule_service import (RevenueRuleService, RuleConfigurationError,
                                               RuleDataIntegrityError)

PREVIEW_BATCH_STATUSES = frozenset({"Draft", "Imported", "Needs Review",
                                    "Reconciled", "Approved", "Closed"})
POSTING_BATCH_STATUSES = frozenset({"Reconciled", "Approved", "Closed"})
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
    def _percentage_display(basis_points: int) -> str:
        return format((Decimal(basis_points) / Decimal(100)).normalize(), "f") + "%"

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

    def preview_job_payout(self, *, job_id: int, tech_id: int,
                           gross_revenue: Any) -> dict[str, Any]:
        """Calculate a non-persisted expected payout with the production rule resolver.

        This is intentionally available only for a persisted Job, so job overrides,
        its market, and its business date are considered exactly as they are by the
        earnings workflow. A Job without a market still resolves Job, Technician,
        then System rules; Market rules participate when a market is assigned.
        """
        job_id, tech_id = self._id(job_id, "job_id"), self._id(tech_id, "tech_id")
        gross_cents = self._financial_cents(gross_revenue)
        with self.auth.connection() as connection:
            job = connection.execute(
                "SELECT job_id, market_id, completed_at, actual_start_at, scheduled_start_at "
                "FROM Jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise LookupError("Job not found")
        effective_date = self.rule_effective_date(job, None)
        if effective_date is None:
            raise ValueError("The Job has no date available for compensation rule resolution.")
        rule = RevenueRuleService(self.auth).resolve_technician_rule(
            job_id=job_id, tech_id=tech_id, market_id=job["market_id"],
            effective_date=effective_date, compensation_component="Overall")
        amount = self.calculate_amount(gross_cents, rule["rule_type"], int(rule["rule_value"]))
        return {"amount_cents": amount, "rule_type": rule["rule_type"],
                "rule_value": int(rule["rule_value"]),
                "rule_source": self._rule_source(rule),
                "compensation_rule_id": int(rule["compensation_rule_id"]),
                "effective_date": effective_date}

    @staticmethod
    def reconcile_financial_components(gross_revenue_cents: int,
                                       components: dict[str, int]) -> dict[str, Any]:
        """Describe whether imported financial metadata is a safe gross breakdown."""
        component_sum = sum(components.values())
        if not any(components.values()):
            status, warning = "No component financial records", None
        elif component_sum == gross_revenue_cents:
            status, warning = "Reconciled", None
        elif component_sum > gross_revenue_cents:
            status = "Components exceed gross; likely overlapping metadata"
            warning = (
                f"Financial component fields total ${component_sum / 100:,.2f}, which exceeds "
                f"the matched gross payment of ${gross_revenue_cents / 100:,.2f}. The Overall "
                "technician percentage was applied to the matched gross payment to avoid "
                "double-counting."
            )
        else:
            status = "Components are less than gross"
            warning = (
                f"Financial component fields total ${component_sum / 100:,.2f}, which does not "
                f"reconcile to the matched gross payment of ${gross_revenue_cents / 100:,.2f}."
            )
        return {"component_sum_cents": component_sum,
                "component_reconciliation_status": status,
                "component_reconciliation_warning": warning,
                "components_reconciled": component_sum == gross_revenue_cents}

    def calculate_technician_compensation(self, connection: sqlite3.Connection, *,
            job_id: int, tech_id: int, market_id: int | None, effective_date: str,
            gross_revenue_cents: int, component_values: dict[str, int]) -> dict[str, Any]:
        """Apply the ledger's technician-rule algorithm without writing a ledger row.

        Both earnings generation and read-only operational previews call this method so
        component fallback, reconciliation, rounding, and rule precedence cannot drift.
        """
        resolver = RevenueRuleService(self.auth)
        overall_rule = resolver.resolve_technician_rule(job_id=job_id, tech_id=tech_id,
            market_id=market_id, effective_date=effective_date,
            compensation_component="Overall")
        resolved_component_rules = []
        for component, basis in component_values.items():
            if not basis:
                continue
            try:
                rule = resolver.resolve_technician_rule(job_id=job_id, tech_id=tech_id,
                    market_id=market_id, effective_date=effective_date,
                    compensation_component=component)
            except RuleConfigurationError:
                continue
            if rule["compensation_component"] != "Overall":
                resolved_component_rules.append((component, basis, rule))

        if resolved_component_rules:
            reconciliation = self.reconcile_financial_components(
                gross_revenue_cents, component_values)
            if not reconciliation["components_reconciled"]:
                raise ValueError("FINANCIAL_COMPONENTS_DO_NOT_RECONCILE: component-specific "
                                 "rules require financial components that reconcile to gross")
            calculation_parts = []
            for component, basis in component_values.items():
                if not basis:
                    continue
                rule = resolver.resolve_technician_rule(job_id=job_id, tech_id=tech_id,
                    market_id=market_id, effective_date=effective_date,
                    compensation_component=component)
                calculation_parts.append((component, basis, rule))
        else:
            reconciliation = {"component_sum_cents": sum(component_values.values()),
                "component_reconciliation_status": "Not applicable to Overall rule",
                "component_reconciliation_warning": None, "components_reconciled": None}
            calculation_parts = [("Overall", gross_revenue_cents, overall_rule)]

        components, amount = [], 0
        for component, basis, rule in calculation_parts:
            calculated = self.calculate_amount(basis, rule["rule_type"], int(rule["rule_value"]))
            amount += calculated
            components.append({"component": component, "revenue_basis_cents": basis,
                "compensation_rule_id": int(rule["compensation_rule_id"]),
                "rule_type": rule["rule_type"], "rule_value": int(rule["rule_value"]),
                "rule_scope_type": rule["scope_type"], "rule_scope_id": rule["scope_id"],
                "resolved_component": rule["compensation_component"],
                "rule_source": self._rule_source(rule),
                "calculated_amount_cents": calculated})
        return {"amount_cents": amount, "components": components,
                "calculation_parts": calculation_parts, **reconciliation}

    def preview_completed_job_compensation(self, connection: sqlite3.Connection, *,
            job: sqlite3.Row, tech_id: int) -> dict[str, Any]:
        """Calculate a completed JobFinancials-based display value, without writes."""
        effective_date = self.rule_effective_date(job, None)
        if effective_date is None:
            raise ValueError("No applicable job date")
        financial_rows = connection.execute("""SELECT ct_rate,ct_travel_payout,
          ct_off_hours_payout FROM JobFinancials WHERE job_id=?""", (job["job_id"],)).fetchall()
        if not financial_rows:
            raise ValueError("Missing job financial data")
        try:
            component_values = {"Base": sum(self._financial_cents(r[0]) for r in financial_rows),
                "Travel": sum(self._financial_cents(r[1]) for r in financial_rows),
                "Off Hours": sum(self._financial_cents(r[2]) for r in financial_rows)}
        except ValueError as exc:
            raise ValueError("Missing job financial data") from exc
        gross = sum(component_values.values())
        if gross <= 0:
            raise ValueError("Missing job financial data")
        result = self.calculate_technician_compensation(connection, job_id=job["job_id"],
            tech_id=tech_id, market_id=job["market_id"], effective_date=effective_date,
            gross_revenue_cents=gross, component_values=component_values)
        result.update(effective_date=effective_date, gross_revenue_cents=gross)
        return result

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
        if batch["batch_status"] not in PREVIEW_BATCH_STATUSES:
            exceptions.append({"payment_item_id": None, "job_id": None, "document_number": None,
              "reason_code": "BATCH_NOT_ELIGIBLE", "message": "This batch status cannot be calculated."})
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
                gross_revenue_cents = gross_value
                financial = connection.execute("""SELECT
                    COALESCE(SUM(ct_rate),0), COALESCE(SUM(ct_travel_payout),0),
                    COALESCE(SUM(ct_off_hours_payout),0)
                    FROM JobFinancials WHERE job_id=?""", (item["job_id"],)).fetchone()
                try:
                    component_values = {"Base": self._financial_cents(financial[0]),
                                        "Travel": self._financial_cents(financial[1]),
                                        "Off Hours": self._financial_cents(financial[2])}
                except ValueError as exc:
                    exceptions.append(self._exception(base, "INVALID_FINANCIAL_AMOUNT", str(exc))); continue
                try:
                    calculation = self.calculate_technician_compensation(connection,
                        job_id=item["job_id"], tech_id=tech["tech_id"],
                        market_id=item["market_id"], effective_date=effective_date,
                        gross_revenue_cents=gross_revenue_cents,
                        component_values=component_values)
                except RuleConfigurationError as exc:
                    exceptions.append(self._exception(base, "NO_TECHNICIAN_RULE", str(exc))); continue
                except RuleDataIntegrityError as exc:
                    exceptions.append(self._exception(base, "AMBIGUOUS_TECHNICIAN_RULE", str(exc))); continue
                except ValueError as exc:
                    if str(exc).startswith("FINANCIAL_COMPONENTS_DO_NOT_RECONCILE"):
                        exceptions.append(self._exception(base,
                            "FINANCIAL_COMPONENTS_DO_NOT_RECONCILE",
                            "Component-specific technician rules require financial components "
                            "that reconcile exactly to matched gross payment.")); continue
                    raise
                amount, components = calculation["amount_cents"], calculation["components"]
                calculation_parts = calculation["calculation_parts"]
                reconciliation = {key: calculation[key] for key in (
                    "component_sum_cents", "component_reconciliation_status",
                    "component_reconciliation_warning", "components_reconciled")}
                resolver = RevenueRuleService(self.auth)
                try:
                    east_rule = resolver.resolve_market_revenue_rule(market_id=item["market_id"],
                        effective_date=effective_date, recipient_code="LUNATECH_EAST")
                except RuleConfigurationError as exc:
                    exceptions.append(self._exception(base, "NO_MARKET_REVENUE_RULE", str(exc))); continue
                except RuleDataIntegrityError as exc:
                    exceptions.append(self._exception(base, "AMBIGUOUS_MARKET_REVENUE_RULE", str(exc))); continue
                east_bp = int(east_rule["share_basis_points"])
                east_amount = self.calculate_amount(gross_revenue_cents, "Percentage", east_bp)
                if amount + east_amount > gross_revenue_cents:
                    exceptions.append(self._exception(base, "TECHNICIAN_AMOUNT_EXCEEDS_GROSS",
                        "Technician compensation exceeds gross payment after the LunaTech-East allocation.")); continue
                contractual = (len(components) == 1 and components[0]["component"] == "Overall"
                               and components[0]["rule_type"] == "Percentage")
                tech_bp = (components[0]["rule_value"] if contractual else
                           (int((Decimal(amount) * 10000 / Decimal(gross_revenue_cents)).quantize(
                                Decimal("1"), rounding=ROUND_HALF_UP)) if gross_revenue_cents else 0))
                if tech_bp + east_bp > 10000:
                    exceptions.append(self._exception(base, "REVENUE_PERCENTAGES_EXCEED_100",
                        "Technician and LunaTech-East shares exceed 100%.")); continue
                lunatech_amount = gross_revenue_cents - amount - east_amount
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
                if existing and (existing["revenue_basis_cents"] != gross_revenue_cents or
                        existing["calculated_amount_cents"] != amount):
                    exceptions.append(self._exception(base, "EXISTING_CALCULATION_DIFFERS",
                        "Current technician earning differs from the resolved calculation.")); continue
                if allocation and (allocation["gross_revenue_cents"] != gross_revenue_cents or
                        allocation["technician_amount_cents"] != amount or
                        allocation["lunatech_east_amount_cents"] != east_amount or
                        allocation["lunatech_amount_cents"] != lunatech_amount or
                        allocation["market_revenue_share_rule_id"] != int(east_rule["market_revenue_share_rule_id"])):
                    exceptions.append(self._exception(base, "EXISTING_CALCULATION_DIFFERS",
                        "Current company allocation differs from the resolved calculation.")); continue
                entries.append({**base, "external_job_id": item["external_job_id"],
                    "job_date": (item["completed_at"] or item["actual_start_at"] or
                                 item["scheduled_start_at"]),
                    "technician_id": tech["tech_id"], "technician_name": " ".join(filter(None,
                    (tech["preferred_name"] or tech["first_name"], tech["last_name"]))),
                    "market_id": item["market_id"], "market_name": item["market_name"],
                    "effective_rule_date": effective_date,
                    "gross_revenue_cents": gross_revenue_cents,
                    "technician_calculation_basis_cents": sum(p[1] for p in calculation_parts),
                    "technician_rule_type": rule["rule_type"],
                    "technician_rule_value": int(rule["rule_value"]),
                    "technician_rule_source": source,
                    **reconciliation, "component_values_cents": component_values,
                    "revenue_basis_cents": gross_revenue_cents, "rule_type": rule["rule_type"],
                    "rule_value": int(rule["rule_value"]), "rule_source": source,
                    "components": components,
                    "effective_rate_display": (self._percentage_display(tech_bp) if contractual else
                        (f"{(Decimal(amount) * 100 / Decimal(gross_revenue_cents)):.2f}%"
                         if gross_revenue_cents else "—")),
                    "technician_amount_cents": amount,
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

    def generate_technician_earnings(self, session: Session, payment_batch_id: int,
                                     *, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        """Post Pending earnings, optionally within a caller-owned transaction."""
        self._write(session); self._id(payment_batch_id, "payment_batch_id")
        context = nullcontext(connection) if connection is not None else self.auth.connection()
        with context as connection:
            status = connection.execute(
                "SELECT batch_status FROM MatterportPaymentBatches WHERE payment_batch_id=?",
                (payment_batch_id,)).fetchone()
            if not status:
                raise LookupError("Payment batch not found")
            if status[0] not in POSTING_BATCH_STATUSES:
                raise ValueError("Technician earnings may only be posted by finalizing a reconciled batch")
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
                    "gross_revenue_basis_cents": entry["gross_revenue_cents"],
                    "gross_revenue_cents": entry["gross_revenue_cents"],
                    "technician_calculation_basis_cents": entry["technician_calculation_basis_cents"],
                    "component_values_cents": entry["component_values_cents"],
                    "component_sum_cents": entry["component_sum_cents"],
                    "component_reconciliation_status": entry["component_reconciliation_status"],
                    "component_reconciliation_warning": entry["component_reconciliation_warning"],
                    "components_reconciled": entry["components_reconciled"],
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
    def _approval_failure(connection: sqlite3.Connection, earning_id: int) -> str | None:
        row = connection.execute("""SELECT e.*,t.status technician_status,j.job_id valid_job,
          i.payment_item_id valid_item,a.company_revenue_allocation_id,a.allocation_status,
          a.gross_revenue_cents allocation_gross,a.technician_amount_cents,
          a.lunatech_east_amount_cents,a.lunatech_amount_cents
          FROM TechnicianJobEarnings e LEFT JOIN Techs t ON t.tech_id=e.tech_id
          LEFT JOIN Jobs j ON j.job_id=e.job_id
          LEFT JOIN MatterportPaymentItems i ON i.payment_item_id=e.payment_item_id
          LEFT JOIN CompanyRevenueAllocations a ON a.technician_earning_id=e.technician_earning_id
            AND a.allocation_status<>'Superseded'
          WHERE e.technician_earning_id=?""", (earning_id,)).fetchone()
        if not row: return "earning does not exist"
        if row["earning_status"] != "Pending": return f"earning is {row['earning_status']}, not Pending"
        if row["technician_status"] != "Active": return "technician is not active"
        linked = connection.execute("""SELECT p.payment_status FROM TechnicianPaymentEarnings pe
          JOIN TechnicianPayments p ON p.technician_payment_id=pe.technician_payment_id
          WHERE pe.technician_earning_id=?""", (earning_id,)).fetchone()
        if linked and linked[0] not in {"Cancelled", "Failed"}: return "earning is linked to a payment"
        if row["entry_type"] == "Manual Adjustment":
            if row["job_id"] is not None and row["valid_job"] is None: return "related job is invalid"
            return None
        if row["valid_job"] is None or row["valid_item"] is None: return "related job or payment item is invalid"
        if row["company_revenue_allocation_id"] is None: return "related company allocation is missing"
        if row["allocation_status"] != "Calculated": return "company allocation is not Calculated"
        if row["technician_amount_cents"] != row["net_earning_cents"]: return "allocation technician amount does not match earning"
        if (row["technician_amount_cents"] + row["lunatech_east_amount_cents"] +
                row["lunatech_amount_cents"] != row["allocation_gross"]):
            return "allocation amounts do not total gross revenue"
        return None

    def approve_technician_earnings(self, session: Session, earning_ids: list[int]) -> list[dict[str, Any]]:
        self._write(session)
        ids = list(dict.fromkeys(self._id(value, "earning_id") for value in earning_ids))
        if not ids: raise ValueError("At least one earning must be selected")
        with self.auth.connection() as connection:
            failures = [{"earning_id": value, "reason": reason} for value in ids
                        if (reason := self._approval_failure(connection, value))]
            if failures:
                message = "; ".join(f"{x['earning_id']}: {x['reason']}" for x in failures)
                raise ValueError(f"Earning approval validation failed: {message}")
            now = utc_now_iso()
            for value in ids:
                row = connection.execute("SELECT entry_type,tech_id,net_earning_cents FROM TechnicianJobEarnings WHERE technician_earning_id=?", (value,)).fetchone()
                changed = connection.execute("""UPDATE TechnicianJobEarnings SET earning_status='Approved',
                  approved_at=?,approved_by=? WHERE technician_earning_id=? AND earning_status='Pending'""",
                  (now,session.user_id,value)).rowcount
                if changed != 1: raise ValueError("Concurrent earning approval detected")
                if row["entry_type"] != "Manual Adjustment":
                    connection.execute("""UPDATE CompanyRevenueAllocations SET allocation_status='Approved',
                      approved_at=?,approved_by=? WHERE technician_earning_id=? AND allocation_status='Calculated'""",
                      (now,session.user_id,value))
                action = ("technician_earning_adjustment_approved" if row["entry_type"] == "Manual Adjustment"
                          else "technician_earning_approved")
                record_event(connection, action, actor_user_id=session.user_id, details={
                    "earning_id": value, "technician_id": row["tech_id"],
                    "amount_cents": row["net_earning_cents"], "previous_status": "Pending",
                    "new_status": "Approved", "timestamp": now})
            if len(ids) > 1:
                record_event(connection,"technician_earnings_bulk_approved",actor_user_id=session.user_id,
                  details={"earning_ids":ids,"count":len(ids),"actor":session.user_id,"timestamp":now})
            return [self._get(connection, value) for value in ids]

    def approve_technician_earning(self, session: Session, earning_id: int) -> dict[str, Any]:
        return self.approve_technician_earnings(session, [earning_id])[0]

    def list_earnings_for_review(self, *, status=None, technician_id=None, payment_batch_id=None,
            job_date_from=None, job_date_to=None, payment_date_from=None, payment_date_to=None,
            market_id=None, search_text=None, unpaid_only=False):
        clauses, params = [], []
        filters = (("e.earning_status",status),("e.tech_id",technician_id),
                   ("e.payment_batch_id",payment_batch_id),("j.market_id",market_id))
        for column, value in filters:
            if value not in (None, "", "All"): clauses.append(column+"=?"); params.append(value)
        for column, op, value in (("substr(COALESCE(j.completed_at,j.actual_start_at,j.scheduled_start_at),1,10)",">=",job_date_from),
                                  ("substr(COALESCE(j.completed_at,j.actual_start_at,j.scheduled_start_at),1,10)","<=",job_date_to),
                                  ("b.payment_date",">=",payment_date_from),("b.payment_date","<=",payment_date_to)):
            if value: clauses.append(f"{column}{op}?"); params.append(value)
        if search_text:
            clauses.append("(j.external_job_id LIKE ? OR j.capture_address_raw LIKE ? OR j.address_1 LIKE ?)")
            token=f"%{str(search_text).strip()}%"; params.extend((token,token,token))
        if unpaid_only:
            clauses.extend(("e.included_in_payment_run_id IS NULL", "e.paid_at IS NULL",
                            "e.voided_at IS NULL"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = """SELECT e.*,COALESCE(t.preferred_name,t.first_name)||' '||t.last_name technician_name,
          j.external_job_id,COALESCE(j.capture_address_raw,j.address_1,'') job_address,
          substr(COALESCE(j.completed_at,j.actual_start_at,j.scheduled_start_at),1,10) job_date,
          m.market_name,i.document_number,b.payment_date,b.payment_batch_id matterport_payment_batch_id,
          a.lunatech_east_amount_cents,a.lunatech_amount_cents,a.allocation_status,
          pe.technician_payment_id,(pe.technician_payment_id IS NOT NULL) linked_to_payment
          FROM TechnicianJobEarnings e JOIN Techs t ON t.tech_id=e.tech_id
          LEFT JOIN Jobs j ON j.job_id=e.job_id LEFT JOIN Markets m ON m.market_id=j.market_id
          LEFT JOIN MatterportPaymentItems i ON i.payment_item_id=e.payment_item_id
          LEFT JOIN MatterportPaymentBatches b ON b.payment_batch_id=e.payment_batch_id
          LEFT JOIN CompanyRevenueAllocations a ON a.technician_earning_id=e.technician_earning_id
            AND a.allocation_status<>'Superseded'
          LEFT JOIN TechnicianPaymentEarnings pe ON pe.technician_earning_id=e.technician_earning_id"""
        with self.auth.connection() as connection:
            return [dict(r) for r in connection.execute(sql+where+" ORDER BY e.technician_earning_id",params)]

    def get_earning_calculation_details(self, earning_id: int) -> dict[str, Any]:
        self._id(earning_id,"earning_id")
        with self.auth.connection() as connection:
            earning = self._get(connection,earning_id)
            earning["calculation_details"] = json.loads(earning.get("calculation_details_json") or "{}")
            allocation = connection.execute("SELECT * FROM CompanyRevenueAllocations WHERE technician_earning_id=? ORDER BY company_revenue_allocation_id DESC LIMIT 1",(earning_id,)).fetchone()
            earning["allocation"] = dict(allocation) if allocation else None
            earning["audit_history"] = [dict(r) for r in connection.execute(
                "SELECT * FROM AuditLog WHERE details_json LIKE ? ORDER BY id",(f'%\"earning_id\": {earning_id}%',))]
            return earning

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
