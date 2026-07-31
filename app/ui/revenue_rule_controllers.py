"""Nonvisual controllers for compensation and market revenue-share screens."""

from datetime import date

from app.security.user_manager import AuthorizationError
from app.services.revenue_rule_service import RuleConfigurationError
from app.ui.revenue_rule_formatting import format_basis_points, format_cents


def classify_applicability(rule: dict, today: date | str | None = None) -> str:
    when = today.isoformat() if isinstance(today, date) else (today or date.today().isoformat())
    if not rule.get("is_active"):
        return "Inactive"
    if rule.get("effective_from") and rule["effective_from"] > when:
        return "Future"
    if rule.get("effective_to") and rule["effective_to"] < when:
        return "Expired"
    return "Current"


def _sorted_history(rows: list[dict], value_field: str, *, today=None) -> list[dict]:
    order = {"Current": 0, "Future": 1, "Expired": 2, "Inactive": 3}
    result = []
    for source in rows:
        row = dict(source); row["applicability"] = classify_applicability(row, today)
        row["display_value"] = format_basis_points(row[value_field])
        result.append(row)
    return sorted(result, key=lambda r: (order[r["applicability"]],
        -(date.fromisoformat(r.get("effective_from") or "0001-01-01").toordinal())))


class _BaseController:
    def __init__(self, service, session): self.service, self.session = service, session
    @property
    def can_modify(self): return self.session.role == "admin"
    def _admin(self):
        if not self.can_modify: raise AuthorizationError("Administrator role required")


class TechnicianCompensationController(_BaseController):
    def history(self, tech_id, *, today=None):
        rows = self.service.list_technician_rules_for(tech_id, include_inactive=True)
        result = _sorted_history(rows, "rule_value", today=today)
        for row in result:
            if row["rule_type"] == "Flat Amount": row["display_value"] = format_cents(row["rule_value"])
        return result
    def effective(self, tech_id, effective_date):
        rule = self.service.resolve_technician_profile_rule(tech_id, effective_date)
        row = dict(rule)
        row["display_value"] = (format_basis_points(row["rule_value"])
                                if row["rule_type"] == "Percentage" else format_cents(row["rule_value"]))
        row["source_label"] = "Technician override" if row["scope_type"] == "Technician" else "System default"
        row["applicability"] = classify_applicability(row, effective_date)
        return row
    def create(self, tech_id, **values):
        self._admin(); return self.service.create_technician_rule(
            self.session, scope_type="Technician", scope_id=tech_id, **values)
    def update_future(self, tech_id, rule_id, **changes):
        self._admin(); rule = self.service.get_technician_rule(rule_id)
        if not rule or rule["scope_type"] != "Technician" or rule["scope_id"] != tech_id:
            raise LookupError("Technician compensation rule not found")
        if classify_applicability(rule) != "Future": raise ValueError("Only future rules may be edited.")
        return self.service.update_technician_rule(self.session, rule_id, **changes)
    def end_current(self, tech_id, rule_id, effective_to):
        self._admin(); rule = self.service.get_technician_rule(rule_id)
        if not rule or rule["scope_id"] != tech_id or classify_applicability(rule) != "Current":
            raise ValueError("Only the current technician rule may be ended.")
        if effective_to < date.today().isoformat():
            raise ValueError("The effective end date cannot be before today.")
        return self.service.end_technician_rule(self.session, rule_id, effective_to)
    def set_active(self, tech_id, rule_id, active):
        self._admin(); rule = self.service.get_technician_rule(rule_id)
        if not rule or rule["scope_id"] != tech_id: raise LookupError("Technician compensation rule not found")
        return (self.service.update_technician_rule(self.session, rule_id, is_active=True) if active
                else self.service.deactivate_technician_rule(self.session, rule_id))


class MarketRevenueShareController(_BaseController):
    def history(self, market_id, *, today=None):
        return _sorted_history(self.service.list_market_revenue_rules_for(
            market_id, include_inactive=True), "share_basis_points", today=today)
    def effective(self, market_id, effective_date):
        rule = self.service.resolve_market_revenue_rule(market_id=market_id, effective_date=effective_date)
        return {**rule, "display_value": format_basis_points(rule["share_basis_points"]),
                "applicability": classify_applicability(rule, effective_date)}
    def summaries(self, market_ids, effective_date):
        raw = self.service.get_current_market_share_summary(market_ids, effective_date); result = {}
        for market_id, item in raw.items():
            label = {"missing": "Not configured", "integrity_error": "Configuration error"}.get(item["status"])
            result[market_id] = {**item, "display_value": label or format_basis_points(
                item["rule"]["share_basis_points"])}
        return result
    def create(self, market_id, **values):
        self._admin(); return self.service.create_market_revenue_rule(
            self.session, market_id=market_id, recipient_code="LUNATECH_EAST", **values)
    def update_future(self, market_id, rule_id, **changes):
        self._admin(); rule = self.service.get_market_revenue_rule(rule_id)
        if not rule or rule["market_id"] != market_id: raise LookupError("Market revenue-share rule not found")
        if classify_applicability(rule) != "Future": raise ValueError("Only future rules may be edited.")
        return self.service.update_market_revenue_rule(self.session, rule_id, **changes)
    def end_current(self, market_id, rule_id, effective_to):
        self._admin(); rule = self.service.get_market_revenue_rule(rule_id)
        if not rule or rule["market_id"] != market_id or classify_applicability(rule) != "Current":
            raise ValueError("Only the current market rule may be ended.")
        if effective_to < date.today().isoformat():
            raise ValueError("The effective end date cannot be before today.")
        return self.service.end_market_revenue_rule(self.session, rule_id, effective_to)
    def set_active(self, market_id, rule_id, active):
        self._admin(); rule = self.service.get_market_revenue_rule(rule_id)
        if not rule or rule["market_id"] != market_id: raise LookupError("Market revenue-share rule not found")
        return (self.service.update_market_revenue_rule(self.session, rule_id, is_active=True) if active
                else self.service.deactivate_market_revenue_rule(self.session, rule_id))
