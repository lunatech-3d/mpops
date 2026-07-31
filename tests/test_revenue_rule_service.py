import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.configure_revenue_rules import configure
from app.security.auth import AuthService
from app.security.user_manager import AuthorizationError, UserManager
from app.services.market_service import MarketService
from app.services.revenue_rule_service import (
    RevenueRuleService, RuleConfigurationError, RuleDataIntegrityError,
)
from app.services.technician_service import TechnicianService


class RevenueRuleTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.auth = AuthService(Settings(
            database_path=Path(self.tempdir.name) / "mpops.db",
            schema_path=PROJECT_ROOT / "database/schema/001_initial.sql",
            password_iterations=100_000))
        users = UserManager(self.auth)
        users.create_user("Admin", "correct-horse-123", "admin")
        self.admin = self.auth.authenticate("Admin", "correct-horse-123")
        users.create_user("Operator", "correct-horse-456", "operator", self.admin)
        users.create_user("Viewer", "correct-horse-789", "viewer", self.admin)
        self.operator = self.auth.authenticate("Operator", "correct-horse-456")
        self.viewer = self.auth.authenticate("Viewer", "correct-horse-789")
        self.techs = TechnicianService(self.auth)
        self.markets = MarketService(self.auth)
        with self.auth.connection() as connection:
            if "state" not in {row[1] for row in connection.execute("PRAGMA table_info(Markets)")}:
                connection.execute("ALTER TABLE Markets ADD COLUMN state TEXT")
        self.tech_id = self.techs.create_technician(self.admin, {
            "tech_code": "T-1", "first_name": "Ada", "last_name": "Lovelace"})
        self.market_id = self.markets.create_market(self.admin, "Detroit", "MI")
        with self.auth.connection() as connection:
            self.job_id = int(connection.execute(
                "INSERT INTO Jobs(external_job_id,created_by) VALUES('JOB-1',?)",
                (self.admin.user_id,)).lastrowid)
        self.service = RevenueRuleService(self.auth)

    def tearDown(self):
        self.tempdir.cleanup()

    def tech_rule(self, **extra):
        values = {"scope_type": "System", "scope_id": None, "rule_type": "Percentage",
                  "rule_value": 7000, "compensation_component": "Overall",
                  "effective_from": "2026-01-01", **extra}
        return values

    def market_rule(self, **extra):
        return {"market_id": self.market_id, "recipient_code": "LUNATECH_EAST",
                "share_basis_points": 0, "effective_from": "2026-01-01", **extra}


class TechnicianRevenueRuleTests(RevenueRuleTestCase):
    def test_admin_crud_and_audit_details(self):
        rule_id = self.service.create_technician_rule(self.admin, **self.tech_rule())
        self.assertEqual(self.service.get_technician_rule(rule_id)["rule_value"], 7000)
        updated = self.service.update_technician_rule(self.admin, rule_id, rule_value=6500)
        self.assertEqual(updated["rule_value"], 6500)
        self.service.deactivate_technician_rule(self.admin, rule_id)
        self.assertEqual(self.service.get_technician_rule(rule_id)["is_active"], 0)
        with self.auth.connection() as connection:
            events = connection.execute("SELECT action,details_json FROM AuditLog WHERE action LIKE "
                "'technician_compensation_rule_%' ORDER BY id").fetchall()
        self.assertEqual([row[0] for row in events], ["technician_compensation_rule_created",
            "technician_compensation_rule_updated", "technician_compensation_rule_deactivated"])
        details = json.loads(events[1][1])
        self.assertEqual(details["rule_id"], rule_id)
        self.assertEqual(details["acting_user"], "Admin")
        self.assertIn("old_values", details); self.assertIn("new_values", details)

    def test_operator_and_viewer_cannot_mutate(self):
        for session in (self.operator, self.viewer):
            with self.subTest(role=session.role), self.assertRaises(AuthorizationError):
                self.service.create_technician_rule(session, **self.tech_rule())

    def test_value_scope_reference_and_date_validation(self):
        invalid = [
            self.tech_rule(rule_value=-1), self.tech_rule(rule_value=10001),
            self.tech_rule(rule_type="Flat Amount", rule_value=-1),
            self.tech_rule(scope_id=1),
            self.tech_rule(scope_type="Technician", scope_id=None),
            self.tech_rule(scope_type="Technician", scope_id=999),
            self.tech_rule(scope_type="Market", scope_id=999),
            self.tech_rule(scope_type="Job", scope_id=999),
            self.tech_rule(effective_from="01/01/2026"),
            self.tech_rule(effective_to="2025-12-31"),
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises((ValueError, LookupError)):
                self.service.create_technician_rule(self.admin, **values)
        flat = self.service.create_technician_rule(self.admin, **self.tech_rule(
            rule_type="Flat Amount", rule_value=0))
        self.assertGreater(flat, 0)

    def test_overlap_adjacent_and_inactive_rules(self):
        first = self.service.create_technician_rule(self.admin, **self.tech_rule(
            effective_to="2026-06-30"))
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.service.create_technician_rule(self.admin, **self.tech_rule(
                effective_from="2026-06-30", rule_value=6000))
        second = self.service.create_technician_rule(self.admin, **self.tech_rule(
            effective_from="2026-07-01", rule_value=6000))
        self.assertNotEqual(first, second)
        self.service.deactivate_technician_rule(self.admin, second)
        third = self.service.create_technician_rule(self.admin, **self.tech_rule(
            effective_from="2026-07-01", rule_value=5000))
        self.assertGreater(third, second)

    def test_resolution_precedence_dates_ids_and_overall_fallback(self):
        system = self.service.create_technician_rule(self.admin, **self.tech_rule(rule_value=1000))
        market = self.service.create_technician_rule(self.admin, **self.tech_rule(
            scope_type="Market", scope_id=self.market_id, rule_value=2000))
        technician = self.service.create_technician_rule(self.admin, **self.tech_rule(
            scope_type="Technician", scope_id=self.tech_id, rule_value=3000))
        job = self.service.create_technician_rule(self.admin, **self.tech_rule(
            scope_type="Job", scope_id=self.job_id, rule_value=4000,
            effective_from="2026-06-01", effective_to="2026-06-30"))
        resolved = self.service.resolve_technician_rule(job_id=self.job_id, tech_id=self.tech_id,
            market_id=self.market_id, effective_date=date(2026, 6, 15),
            compensation_component="Travel")
        self.assertEqual(resolved["compensation_rule_id"], job)
        self.assertNotIn("Douglas", RevenueRuleService.resolve_technician_rule.__code__.co_consts)
        self.service.deactivate_technician_rule(self.admin, job)
        self.assertEqual(self.service.resolve_technician_rule(job_id=self.job_id,
            tech_id=self.tech_id, market_id=self.market_id, effective_date=date(2026, 6, 15))[
                "compensation_rule_id"], technician)
        self.service.deactivate_technician_rule(self.admin, technician)
        self.assertEqual(self.service.resolve_technician_rule(job_id=self.job_id,
            tech_id=self.tech_id, market_id=self.market_id, effective_date=date(2026, 6, 15))[
                "compensation_rule_id"], market)
        self.service.deactivate_technician_rule(self.admin, market)
        self.assertEqual(self.service.resolve_technician_rule(job_id=self.job_id,
            tech_id=self.tech_id, market_id=self.market_id, effective_date=date(2026, 6, 15))[
                "compensation_rule_id"], system)

    def test_missing_and_ambiguous_resolution_errors(self):
        with self.assertRaises(RuleConfigurationError):
            self.service.resolve_technician_rule(job_id=self.job_id, tech_id=self.tech_id,
                market_id=self.market_id, effective_date=date(2026, 1, 1))
        with self.auth.connection() as connection:
            for value in (6000, 7000):
                connection.execute("""INSERT INTO TechnicianCompensationRules
                    (scope_type,scope_id,rule_type,rule_value,compensation_component,effective_from)
                    VALUES('Technician',?,'Percentage',?,'Overall','2026-01-01')""",
                    (self.tech_id, value))
        with self.assertRaises(RuleDataIntegrityError):
            self.service.resolve_technician_rule(job_id=self.job_id, tech_id=self.tech_id,
                market_id=self.market_id, effective_date=date(2026, 1, 1))


class MarketRevenueRuleTests(RevenueRuleTestCase):
    def test_zero_and_ten_percent_resolution_and_auditing(self):
        mi = self.service.create_market_revenue_rule(self.admin, **self.market_rule())
        nc_market = self.markets.create_market(self.admin, "Charlotte", "NC")
        nc = self.service.create_market_revenue_rule(self.admin, market_id=nc_market,
            share_basis_points=1000, effective_from="2026-01-01")
        self.assertEqual(self.service.resolve_market_revenue_rule(market_id=self.market_id,
            effective_date=date(2026, 2, 1))["market_revenue_share_rule_id"], mi)
        self.assertEqual(self.service.get_market_revenue_rule(nc)["share_basis_points"], 1000)
        self.service.update_market_revenue_rule(self.admin, nc, notes="approved")
        self.service.deactivate_market_revenue_rule(self.admin, nc)
        with self.auth.connection() as connection:
            actions = [r[0] for r in connection.execute("SELECT action FROM AuditLog WHERE action "
                "LIKE 'market_revenue_share_rule_%'")]
        self.assertEqual(len(actions), 4)

    def test_validation_overlap_adjacent_and_missing(self):
        for changes in ({"share_basis_points": -1}, {"share_basis_points": 10001},
                        {"recipient_code": "OTHER"}, {"market_id": 999},
                        {"effective_from": "2026/01/01"},
                        {"effective_to": "2025-01-01"}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, LookupError)):
                self.service.create_market_revenue_rule(self.admin,
                    **self.market_rule(**changes))
        first = self.service.create_market_revenue_rule(self.admin, **self.market_rule(
            effective_to="2026-03-31"))
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.service.create_market_revenue_rule(self.admin, **self.market_rule(
                effective_from="2026-03-31", share_basis_points=1000))
        future = self.service.create_market_revenue_rule(self.admin, **self.market_rule(
            effective_from="2026-04-01", share_basis_points=1000))
        self.assertEqual(self.service.resolve_market_revenue_rule(market_id=self.market_id,
            effective_date=date(2026, 3, 31))["market_revenue_share_rule_id"], first)
        self.assertEqual(self.service.resolve_market_revenue_rule(market_id=self.market_id,
            effective_date=date(2026, 4, 1))["market_revenue_share_rule_id"], future)
        with self.assertRaises(RuleConfigurationError):
            self.service.resolve_market_revenue_rule(market_id=self.market_id,
                effective_date=date(2025, 1, 1))

    def test_mutations_admin_only_and_ambiguous_data(self):
        for session in (self.operator, self.viewer):
            with self.assertRaises(AuthorizationError):
                self.service.create_market_revenue_rule(session, **self.market_rule())
        with self.auth.connection() as connection:
            for share in (0, 1000):
                connection.execute("""INSERT INTO MarketRevenueShareRules
                    (market_id,recipient_code,share_basis_points,effective_from)
                    VALUES(?,'LUNATECH_EAST',?,'2026-01-01')""", (self.market_id, share))
        with self.assertRaises(RuleDataIntegrityError):
            self.service.resolve_market_revenue_rule(market_id=self.market_id,
                effective_date=date(2026, 1, 1))


class RevenueConfigurationTests(RevenueRuleTestCase):
    def setUp(self):
        super().setUp()
        self.techs.create_technician(self.admin, {"tech_code": "DW",
            "first_name": "Douglas", "last_name": "Willett"})
        self.nc = self.markets.create_market(self.admin, "Charlotte", "NC")
        self.other = self.markets.create_market(self.admin, "Atlanta", "GA")

    def run_config(self, dry_run=False):
        output = io.StringIO()
        result = configure(self.auth, username="admin", effective_from="2026-01-01",
                           dry_run=dry_run, output=output)
        return result, output.getvalue()

    def counts(self):
        with self.auth.connection() as connection:
            return (connection.execute("SELECT count(*) FROM TechnicianCompensationRules").fetchone()[0],
                    connection.execute("SELECT count(*) FROM MarketRevenueShareRules").fetchone()[0])

    def test_dry_run_apply_idempotence_and_state_configuration(self):
        result, report = self.run_config(True)
        self.assertEqual(self.counts(), (0, 0)); self.assertIn("tech_id=", report)
        self.assertIn("UNCONFIGURED", report)
        result, report = self.run_config()
        self.assertEqual(result["created"], 4)
        self.assertEqual((result["michigan"], result["north_carolina"], result["unconfigured"]),
                         (1, 1, 1))
        with self.auth.connection() as connection:
            shares = dict(connection.execute("SELECT market_id,share_basis_points FROM "
                                             "MarketRevenueShareRules"))
        self.assertEqual(shares, {self.market_id: 0, self.nc: 1000})
        self.assertNotIn(self.other, shares)
        again, report = self.run_config()
        self.assertEqual(again["created"], 0); self.assertEqual(again["already_correct"], 4)
        self.assertEqual(self.counts(), (2, 2))

    def test_missing_and_duplicate_douglas_fail_safely(self):
        with self.auth.connection() as connection:
            connection.execute("DELETE FROM Techs WHERE tech_code='DW'")
        with self.assertRaisesRegex(LookupError, "No exact"):
            self.run_config()
        self.assertEqual(self.counts(), (0, 0))
        self.techs.create_technician(self.admin, {"tech_code": "DW1",
            "first_name": "Douglas", "last_name": "Willett"})
        self.techs.create_technician(self.admin, {"tech_code": "DW2",
            "first_name": "DOUGLAS", "last_name": "WILLETT"})
        with self.assertRaisesRegex(ValueError, "Multiple exact"):
            self.run_config()
        self.assertEqual(self.counts(), (0, 0))

    def test_conflict_rolls_back_all_planned_rules(self):
        self.service.create_market_revenue_rule(self.admin, market_id=self.market_id,
            share_basis_points=500, effective_from="2026-01-01")
        before = self.counts()
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.run_config()
        self.assertEqual(self.counts(), before)


if __name__ == "__main__":
    unittest.main()
