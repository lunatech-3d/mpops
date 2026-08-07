"""Management and deterministic resolution of effective-dated revenue rules."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError


class RuleConfigurationError(LookupError):
    """Raised when no configured rule applies to a requested date."""


class RuleDataIntegrityError(RuntimeError):
    """Raised when stored rules make deterministic resolution impossible."""


class RevenueRuleService:
    """Validate, audit, and resolve technician and market revenue rules.

    Component requests use an ``Overall`` rule as a fallback at the same scope
    before resolution proceeds to the next-lower-precedence scope.
    """

    SCOPES = frozenset({"Job", "Technician", "Market", "System"})
    COMPONENTS = frozenset({"Overall", "Base", "Travel", "Off Hours"})
    RULE_TYPES = frozenset({"Percentage", "Flat Amount"})
    RECIPIENTS = frozenset({"LUNATECH_EAST"})
    TECH_FIELDS = frozenset({"scope_type", "scope_id", "rule_type", "rule_value",
                             "compensation_component", "effective_from", "effective_to",
                             "is_active"})
    MARKET_FIELDS = frozenset({"market_id", "recipient_code", "share_basis_points",
                               "effective_from", "effective_to", "is_active", "notes"})

    def __init__(self, auth: AuthService):
        self.auth = auth

    @staticmethod
    def _require_admin(session: Session | None) -> None:
        if session is None or session.role != "admin":
            raise AuthorizationError("Administrator role required")

    @staticmethod
    def _id(value: Any, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} must be a positive integer")
        return value

    @staticmethod
    def _integer(value: Any, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{label} must be an integer")
        return value

    @staticmethod
    def _date(value: Any, label: str, *, required: bool = False) -> str | None:
        if value is None:
            if required:
                raise ValueError(f"{label} is required")
            return None
        if not isinstance(value, str):
            raise ValueError(f"{label} must use ISO YYYY-MM-DD")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{label} must use ISO YYYY-MM-DD") from exc
        if parsed.isoformat() != value:
            raise ValueError(f"{label} must use ISO YYYY-MM-DD")
        return value

    @classmethod
    def _effective_date(cls, value: Any) -> str:
        if isinstance(value, date):
            return value.isoformat()
        parsed = cls._date(value, "effective_date", required=True)
        assert parsed is not None
        return parsed

    @staticmethod
    def _active(value: Any) -> int:
        if type(value) is not bool:
            raise ValueError("is_active must be a boolean")
        return int(value)

    @staticmethod
    def _range(start: str | None, end: str | None) -> None:
        if start is not None and end is not None and end < start:
            raise ValueError("effective_to cannot precede effective_from")

    @staticmethod
    def _exists(connection: sqlite3.Connection, table: str, column: str,
                identifier: int, label: str) -> None:
        if connection.execute(
                f'SELECT 1 FROM "{table}" WHERE "{column}"=?', (identifier,)).fetchone() is None:
            raise LookupError(f"{label} not found")

    def _clean_technician(self, connection: sqlite3.Connection,
                          values: dict[str, Any]) -> dict[str, Any]:
        scope = values["scope_type"]
        if scope not in self.SCOPES:
            raise ValueError("Unsupported technician rule scope_type")
        component = values.get("compensation_component", "Overall")
        if component not in self.COMPONENTS:
            raise ValueError("Unsupported compensation_component")
        rule_type = values["rule_type"]
        if rule_type not in self.RULE_TYPES:
            raise ValueError("Unsupported technician rule_type")
        rule_value = self._integer(values["rule_value"], "rule_value")
        if rule_type == "Percentage" and not 0 <= rule_value <= 10000:
            raise ValueError("Percentage rule_value must be between 0 and 10000 basis points")
        if rule_type == "Flat Amount" and rule_value < 0:
            raise ValueError("Flat Amount rule_value must be nonnegative integer cents")
        scope_id = values.get("scope_id")
        if scope == "System":
            if scope_id is not None:
                raise ValueError("System rules require a null scope_id")
        else:
            scope_id = self._id(scope_id, "scope_id")
            table, column = {"Job": ("Jobs", "job_id"), "Technician": ("Techs", "tech_id"),
                             "Market": ("Markets", "market_id")}[scope]
            self._exists(connection, table, column, scope_id, scope)
        start = self._date(values.get("effective_from"), "effective_from")
        end = self._date(values.get("effective_to"), "effective_to")
        self._range(start, end)
        return {"scope_type": scope, "scope_id": scope_id, "rule_type": rule_type,
                "rule_value": rule_value, "compensation_component": component,
                "effective_from": start, "effective_to": end,
                "is_active": self._active(values.get("is_active", True))}

    def _clean_market(self, connection: sqlite3.Connection,
                      values: dict[str, Any]) -> dict[str, Any]:
        market_id = self._id(values["market_id"], "market_id")
        self._exists(connection, "Markets", "market_id", market_id, "Market")
        recipient = values.get("recipient_code", "LUNATECH_EAST")
        if recipient not in self.RECIPIENTS:
            raise ValueError("Unsupported recipient_code")
        share = self._integer(values["share_basis_points"], "share_basis_points")
        if not 0 <= share <= 10000:
            raise ValueError("share_basis_points must be between 0 and 10000")
        start = self._date(values.get("effective_from"), "effective_from", required=True)
        end = self._date(values.get("effective_to"), "effective_to")
        self._range(start, end)
        notes = values.get("notes")
        if notes is not None:
            if not isinstance(notes, str):
                raise ValueError("notes must be text")
            notes = notes.strip() or None
            if notes and len(notes) > 1000:
                raise ValueError("notes may not exceed 1000 characters")
        return {"market_id": market_id, "recipient_code": recipient,
                "share_basis_points": share, "effective_from": start, "effective_to": end,
                "is_active": self._active(values.get("is_active", True)), "notes": notes}

    @staticmethod
    def _overlap(connection: sqlite3.Connection, table: str, key_sql: str,
                 key_values: tuple[Any, ...], start: str | None, end: str | None,
                 id_column: str, exclude_id: int | None = None) -> None:
        sql = (f"SELECT {id_column} FROM {table} WHERE is_active=1 AND {key_sql} "
               "AND (? IS NULL OR effective_from IS NULL OR effective_from<=?) "
               "AND (? IS NULL OR effective_to IS NULL OR effective_to>=?)")
        params: list[Any] = [*key_values, end, end, start, start]
        if exclude_id is not None:
            sql += f" AND {id_column}<>?"
            params.append(exclude_id)
        row = connection.execute(sql, params).fetchone()
        if row:
            raise ValueError(f"Active rule date range overlaps rule {row[0]}")

    def list_technician_rules(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        if type(include_inactive) is not bool:
            raise ValueError("include_inactive must be a boolean")
        where = "" if include_inactive else " WHERE is_active=1"
        with self.auth.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM TechnicianCompensationRules" + where +
                " ORDER BY scope_type,scope_id,compensation_component,effective_from,compensation_rule_id")]

    def get_technician_rule(self, rule_id: int) -> dict[str, Any] | None:
        self._id(rule_id, "rule_id")
        with self.auth.connection() as connection:
            row = connection.execute("SELECT * FROM TechnicianCompensationRules "
                                     "WHERE compensation_rule_id=?", (rule_id,)).fetchone()
            return dict(row) if row else None

    def list_technician_rules_for(self, tech_id: int, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        """Return only rules owned by one technician (never System fallbacks)."""
        tech_id = self._id(tech_id, "tech_id")
        if type(include_inactive) is not bool:
            raise ValueError("include_inactive must be a boolean")
        with self.auth.connection() as connection:
            self._exists(connection, "Techs", "tech_id", tech_id, "Technician")
            active = "" if include_inactive else " AND is_active=1"
            rows = connection.execute("SELECT * FROM TechnicianCompensationRules WHERE "
                "scope_type='Technician' AND scope_id=?" + active +
                " ORDER BY effective_from DESC,compensation_rule_id DESC", (tech_id,))
            return [dict(row) for row in rows]

    def _create_technician(self, connection: sqlite3.Connection, session: Session,
                           values: dict[str, Any]) -> int:
        clean = self._clean_technician(connection, values)
        if clean["is_active"]:
            self._overlap(connection, "TechnicianCompensationRules",
                "scope_type=? AND scope_id IS ? AND compensation_component=?",
                (clean["scope_type"], clean["scope_id"], clean["compensation_component"]),
                clean["effective_from"], clean["effective_to"], "compensation_rule_id")
        cursor = connection.execute("""INSERT INTO TechnicianCompensationRules
            (scope_type,scope_id,rule_type,rule_value,compensation_component,effective_from,
             effective_to,is_active,created_by) VALUES (?,?,?,?,?,?,?,?,?)""",
            (*clean.values(), session.user_id))
        rule_id = int(cursor.lastrowid)
        record_event(connection, "technician_compensation_rule_created",
            actor_user_id=session.user_id,
            details={"rule_id": rule_id, "scope": {"type": clean["scope_type"],
                     "id": clean["scope_id"]}, "new_values": clean, "acting_user": session.username})
        return rule_id

    def create_technician_rule(self, session: Session, **values: Any) -> int:
        self._require_admin(session)
        with self.auth.connection() as connection:
            return self._create_technician(connection, session, values)

    def update_technician_rule(self, session: Session, rule_id: int,
                               **changes: Any) -> dict[str, Any]:
        self._require_admin(session); self._id(rule_id, "rule_id")
        if not changes or set(changes) - self.TECH_FIELDS:
            raise ValueError("At least one supported technician rule field is required")
        with self.auth.connection() as connection:
            row = connection.execute("SELECT * FROM TechnicianCompensationRules WHERE "
                                     "compensation_rule_id=?", (rule_id,)).fetchone()
            if not row:
                raise LookupError("Technician compensation rule not found")
            before = dict(row)
            combined = {field: before[field] for field in self.TECH_FIELDS}
            combined["is_active"] = bool(combined["is_active"])
            combined.update(changes)
            clean = self._clean_technician(connection, combined)
            if clean["is_active"]:
                self._overlap(connection, "TechnicianCompensationRules",
                    "scope_type=? AND scope_id IS ? AND compensation_component=?",
                    (clean["scope_type"], clean["scope_id"], clean["compensation_component"]),
                    clean["effective_from"], clean["effective_to"], "compensation_rule_id", rule_id)
            connection.execute("""UPDATE TechnicianCompensationRules SET scope_type=?,scope_id=?,
                rule_type=?,rule_value=?,compensation_component=?,effective_from=?,effective_to=?,
                is_active=? WHERE compensation_rule_id=?""", (*clean.values(), rule_id))
            record_event(connection, "technician_compensation_rule_updated",
                actor_user_id=session.user_id, details={"rule_id": rule_id,
                "scope": {"type": clean["scope_type"], "id": clean["scope_id"]},
                "old_values": {k: before[k] for k in self.TECH_FIELDS}, "new_values": clean,
                "acting_user": session.username})
            return dict(connection.execute("SELECT * FROM TechnicianCompensationRules WHERE "
                                           "compensation_rule_id=?", (rule_id,)).fetchone())

    def deactivate_technician_rule(self, session: Session, rule_id: int) -> None:
        self._require_admin(session); self._id(rule_id, "rule_id")
        with self.auth.connection() as connection:
            row = connection.execute("SELECT * FROM TechnicianCompensationRules WHERE "
                                     "compensation_rule_id=?", (rule_id,)).fetchone()
            if not row:
                raise LookupError("Technician compensation rule not found")
            before = dict(row)
            connection.execute("UPDATE TechnicianCompensationRules SET is_active=0 WHERE "
                               "compensation_rule_id=?", (rule_id,))
            record_event(connection, "technician_compensation_rule_deactivated",
                actor_user_id=session.user_id, details={"rule_id": rule_id,
                "scope": {"type": before["scope_type"], "id": before["scope_id"]},
                "old_values": {k: before[k] for k in self.TECH_FIELDS},
                "new_values": {"is_active": 0}, "acting_user": session.username})

    def end_technician_rule(self, session: Session, rule_id: int, effective_to: str) -> dict[str, Any]:
        """End a rule without changing its historical value or start date."""
        return self.update_technician_rule(session, rule_id, effective_to=effective_to)

    def resolve_technician_profile_rule(self, tech_id: int, effective_date: date | str,
                                        compensation_component: str = "Overall") -> dict[str, Any]:
        """Resolve Technician then System compensation for a profile view."""
        tech_id = self._id(tech_id, "tech_id"); when = self._effective_date(effective_date)
        if compensation_component not in self.COMPONENTS:
            raise ValueError("Unsupported compensation_component")
        with self.auth.connection() as connection:
            self._exists(connection, "Techs", "tech_id", tech_id, "Technician")
            for scope, scope_id in (("Technician", tech_id), ("System", None)):
                rows = connection.execute("""SELECT * FROM TechnicianCompensationRules
                    WHERE scope_type=? AND scope_id IS ? AND compensation_component=? AND is_active=1
                      AND (effective_from IS NULL OR effective_from<=?)
                      AND (effective_to IS NULL OR effective_to>=?)
                    ORDER BY compensation_rule_id""",
                    (scope, scope_id, compensation_component, when, when)).fetchall()
                if len(rows) > 1:
                    raise RuleDataIntegrityError(
                        f"Multiple {scope} {compensation_component} technician rules apply on {when}")
                if rows:
                    return dict(rows[0])
        raise RuleConfigurationError(
            f"No {compensation_component} technician compensation rule applies on {when}")

    def resolve_technician_rule(self, *, job_id: int, tech_id: int, market_id: int | None,
                                effective_date: date,
                                compensation_component: str = "Overall") -> dict[str, Any]:
        ids = (self._id(job_id, "job_id"), self._id(tech_id, "tech_id"),
               self._id(market_id, "market_id") if market_id is not None else None)
        when = self._effective_date(effective_date)
        if compensation_component not in self.COMPONENTS:
            raise ValueError("Unsupported compensation_component")
        with self.auth.connection() as connection:
            required = [("Jobs", "job_id", ids[0], "Job"),
                        ("Techs", "tech_id", ids[1], "Technician")]
            if ids[2] is not None:
                required.append(("Markets", "market_id", ids[2], "Market"))
            for table, column, identifier, label in required:
                self._exists(connection, table, column, identifier, label)
            scopes = [("Job", ids[0]), ("Technician", ids[1])]
            if ids[2] is not None:
                scopes.append(("Market", ids[2]))
            scopes.append(("System", None))
            for scope, scope_id in scopes:
                components = (compensation_component,) if compensation_component == "Overall" else (
                    compensation_component, "Overall")
                for component in components:
                    rows = connection.execute("""SELECT * FROM TechnicianCompensationRules
                        WHERE scope_type=? AND scope_id IS ? AND compensation_component=? AND is_active=1
                          AND (effective_from IS NULL OR effective_from<=?)
                          AND (effective_to IS NULL OR effective_to>=?)
                        ORDER BY compensation_rule_id""",
                        (scope, scope_id, component, when, when)).fetchall()
                    if len(rows) > 1:
                        raise RuleDataIntegrityError(
                            f"Multiple {scope} {component} technician rules apply on {when}")
                    if rows:
                        return dict(rows[0])
        raise RuleConfigurationError(
            f"No {compensation_component} technician compensation rule applies on {when}")

    def list_market_revenue_rules(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        if type(include_inactive) is not bool:
            raise ValueError("include_inactive must be a boolean")
        where = "" if include_inactive else " WHERE is_active=1"
        with self.auth.connection() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM MarketRevenueShareRules"+
                where+" ORDER BY market_id,recipient_code,effective_from,market_revenue_share_rule_id")]

    def get_market_revenue_rule(self, rule_id: int) -> dict[str, Any] | None:
        self._id(rule_id, "rule_id")
        with self.auth.connection() as connection:
            row = connection.execute("SELECT * FROM MarketRevenueShareRules WHERE "
                "market_revenue_share_rule_id=?", (rule_id,)).fetchone()
            return dict(row) if row else None

    def list_market_revenue_rules_for(self, market_id: int, *, include_inactive: bool = True,
                                      recipient_code: str = "LUNATECH_EAST") -> list[dict[str, Any]]:
        market_id = self._id(market_id, "market_id")
        if type(include_inactive) is not bool:
            raise ValueError("include_inactive must be a boolean")
        if recipient_code not in self.RECIPIENTS:
            raise ValueError("Unsupported recipient_code")
        with self.auth.connection() as connection:
            self._exists(connection, "Markets", "market_id", market_id, "Market")
            active = "" if include_inactive else " AND is_active=1"
            rows = connection.execute("SELECT * FROM MarketRevenueShareRules WHERE market_id=? "
                "AND recipient_code=?" + active +
                " ORDER BY effective_from DESC,market_revenue_share_rule_id DESC",
                (market_id, recipient_code))
            return [dict(row) for row in rows]

    def _create_market(self, connection: sqlite3.Connection, session: Session,
                       values: dict[str, Any]) -> int:
        clean = self._clean_market(connection, values)
        if clean["is_active"]:
            self._overlap(connection, "MarketRevenueShareRules", "market_id=? AND recipient_code=?",
                (clean["market_id"], clean["recipient_code"]), clean["effective_from"],
                clean["effective_to"], "market_revenue_share_rule_id")
        cursor = connection.execute("""INSERT INTO MarketRevenueShareRules
            (market_id,recipient_code,share_basis_points,effective_from,effective_to,is_active,
             notes,created_by) VALUES (?,?,?,?,?,?,?,?)""", (*clean.values(), session.user_id))
        rule_id = int(cursor.lastrowid)
        record_event(connection, "market_revenue_share_rule_created", actor_user_id=session.user_id,
            details={"rule_id": rule_id, "market_id": clean["market_id"], "new_values": clean,
                     "acting_user": session.username})
        return rule_id

    def create_market_revenue_rule(self, session: Session, **values: Any) -> int:
        self._require_admin(session)
        with self.auth.connection() as connection:
            return self._create_market(connection, session, values)

    def update_market_revenue_rule(self, session: Session, rule_id: int,
                                   **changes: Any) -> dict[str, Any]:
        self._require_admin(session); self._id(rule_id, "rule_id")
        if not changes or set(changes) - self.MARKET_FIELDS:
            raise ValueError("At least one supported market rule field is required")
        with self.auth.connection() as connection:
            row = connection.execute("SELECT * FROM MarketRevenueShareRules WHERE "
                "market_revenue_share_rule_id=?", (rule_id,)).fetchone()
            if not row:
                raise LookupError("Market revenue-share rule not found")
            before = dict(row)
            combined = {field: before[field] for field in self.MARKET_FIELDS}
            combined["is_active"] = bool(combined["is_active"])
            combined.update(changes)
            clean = self._clean_market(connection, combined)
            if clean["is_active"]:
                self._overlap(connection, "MarketRevenueShareRules", "market_id=? AND recipient_code=?",
                    (clean["market_id"], clean["recipient_code"]), clean["effective_from"],
                    clean["effective_to"], "market_revenue_share_rule_id", rule_id)
            now = utc_now_iso()
            connection.execute("""UPDATE MarketRevenueShareRules SET market_id=?,recipient_code=?,
                share_basis_points=?,effective_from=?,effective_to=?,is_active=?,notes=?,updated_at=?,
                updated_by=? WHERE market_revenue_share_rule_id=?""",
                (*clean.values(), now, session.user_id, rule_id))
            record_event(connection, "market_revenue_share_rule_updated", actor_user_id=session.user_id,
                details={"rule_id": rule_id, "market_id": clean["market_id"],
                "old_values": {k: before[k] for k in self.MARKET_FIELDS}, "new_values": clean,
                "acting_user": session.username})
            return dict(connection.execute("SELECT * FROM MarketRevenueShareRules WHERE "
                "market_revenue_share_rule_id=?", (rule_id,)).fetchone())

    def deactivate_market_revenue_rule(self, session: Session, rule_id: int) -> None:
        self._require_admin(session); self._id(rule_id, "rule_id")
        with self.auth.connection() as connection:
            row = connection.execute("SELECT * FROM MarketRevenueShareRules WHERE "
                "market_revenue_share_rule_id=?", (rule_id,)).fetchone()
            if not row:
                raise LookupError("Market revenue-share rule not found")
            before = dict(row); now = utc_now_iso()
            connection.execute("UPDATE MarketRevenueShareRules SET is_active=0,updated_at=?,updated_by=? "
                "WHERE market_revenue_share_rule_id=?", (now, session.user_id, rule_id))
            record_event(connection, "market_revenue_share_rule_deactivated",
                actor_user_id=session.user_id, details={"rule_id": rule_id,
                "market_id": before["market_id"],
                "old_values": {k: before[k] for k in self.MARKET_FIELDS},
                "new_values": {"is_active": 0}, "acting_user": session.username})

    def end_market_revenue_rule(self, session: Session, rule_id: int,
                                effective_to: str) -> dict[str, Any]:
        return self.update_market_revenue_rule(session, rule_id, effective_to=effective_to)

    def resolve_market_revenue_rule(self, *, market_id: int, effective_date: date,
                                    recipient_code: str = "LUNATECH_EAST") -> dict[str, Any]:
        market_id = self._id(market_id, "market_id"); when = self._effective_date(effective_date)
        if recipient_code not in self.RECIPIENTS:
            raise ValueError("Unsupported recipient_code")
        with self.auth.connection() as connection:
            self._exists(connection, "Markets", "market_id", market_id, "Market")
            rows = connection.execute("""SELECT * FROM MarketRevenueShareRules
                WHERE market_id=? AND recipient_code=? AND is_active=1
                  AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)
                ORDER BY market_revenue_share_rule_id""",
                (market_id, recipient_code, when, when)).fetchall()
        if len(rows) > 1:
            raise RuleDataIntegrityError(
                f"Multiple {recipient_code} market revenue-share rules apply on {when}")
        if not rows:
            raise RuleConfigurationError(
                f"No {recipient_code} market revenue-share rule applies on {when}")
        return dict(rows[0])

    def get_current_market_share_summary(self, market_ids: list[int],
                                         effective_date: date | str) -> dict[int, dict[str, Any]]:
        """Bulk resolve shares, explicitly distinguishing missing and ambiguous rules."""
        if not isinstance(market_ids, list):
            raise ValueError("market_ids must be a list")
        ids = [self._id(value, "market_id") for value in market_ids]
        if len(set(ids)) != len(ids):
            ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        when = self._effective_date(effective_date)
        placeholders = ",".join("?" for _ in ids)
        with self.auth.connection() as connection:
            existing = {row[0] for row in connection.execute(
                f"SELECT market_id FROM Markets WHERE market_id IN ({placeholders})", ids)}
            missing_ids = set(ids) - existing
            if missing_ids:
                raise LookupError(f"Market not found: {min(missing_ids)}")
            rows = connection.execute(f"""SELECT * FROM MarketRevenueShareRules
                WHERE market_id IN ({placeholders}) AND recipient_code='LUNATECH_EAST'
                  AND is_active=1 AND effective_from<=?
                  AND (effective_to IS NULL OR effective_to>=?)
                ORDER BY market_revenue_share_rule_id""", (*ids, when, when)).fetchall()
        grouped: dict[int, list[dict[str, Any]]] = {identifier: [] for identifier in ids}
        for row in rows:
            grouped[int(row["market_id"])].append(dict(row))
        result = {}
        for identifier, matches in grouped.items():
            if not matches:
                result[identifier] = {"status": "missing", "rule": None}
            elif len(matches) > 1:
                result[identifier] = {"status": "integrity_error", "rule": None}
            else:
                result[identifier] = {"status": "resolved", "rule": matches[0]}
        return result
