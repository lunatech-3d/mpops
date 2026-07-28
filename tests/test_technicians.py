import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import PROJECT_ROOT, Settings
from app.security.auth import AuthService
from app.security.user_manager import AuthorizationError, UserManager
from app.services.technician_service import TechnicianService


class TechnicianServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "mpops.db"
        self.auth = AuthService(Settings(database_path=self.db_path,
                                         schema_path=PROJECT_ROOT / "database/schema/001_initial.sql",
                                         password_iterations=100_000))
        users = UserManager(self.auth)
        users.create_user("Admin", "correct-horse-123", "admin")
        self.admin = self.auth.authenticate("Admin", "correct-horse-123")
        self.operator_id = users.create_user("Operator", "correct-horse-456", "operator", self.admin)
        self.operator = self.auth.authenticate("Operator", "correct-horse-456")
        self.service = TechnicianService(self.auth)

    def tearDown(self):
        self.tempdir.cleanup()

    def create_tech(self, code="T001", first="Ada", last="Lovelace", **extra):
        data = {"tech_code": code, "first_name": first, "last_name": last, **extra}
        return self.service.create_technician(self.admin, data)

    @staticmethod
    def address(**extra):
        return {"address_1": " 12 Main St ", "city": " London ", "state": "ny",
                "zip_code": " 00123 ", **extra}

    def actions(self):
        with self.auth.connection() as connection:
            return [row[0] for row in connection.execute("SELECT action FROM AuditLog ORDER BY id")]

    def test_admin_creates_normalized_persisted_and_audited_technician(self):
        tech_id = self.service.create_technician(self.admin, {
            "tech_code": " T-9 ", "first_name": " Ada ", "last_name": " Lovelace ",
            "preferred_name": " ", "email": " ada@example.test ", "mobile_phone": " +44 20 1234 "})
        tech = self.service.get_technician(tech_id)
        self.assertEqual((tech["tech_code"], tech["first_name"], tech["preferred_name"]),
                         ("T-9", "Ada", None))
        self.assertEqual(tech["mobile_phone"], "+44 20 1234")
        self.assertEqual(tech["created_by"], self.admin.user_id)
        self.assertIn("technician_created", self.actions())

    def test_create_validation_and_conflicts(self):
        for data in ({}, {"tech_code": "T", "first_name": " ", "last_name": "Last"},
                     {"tech_code": "T", "first_name": "First", "last_name": "Last",
                      "email": "not-email"},
                     {"tech_code": "T", "first_name": "First", "last_name": "Last",
                      "bogus": "x"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.service.create_technician(self.admin, data)
        self.create_tech()
        with self.assertRaises(ValueError):
            self.create_tech(code="t001")

    def test_expanded_fields_validation_and_normalization(self):
        tech_id = self.create_tech(
            middle_name="M", suffix="III", company_name="Analytical Engines",
            contractor_type="Independent", date_of_birth="1815-12-10", ssn_last4="1234",
            drivers_license_number="DL-9", drivers_license_state="ny",
            email="ada@example.test", alternate_email="other@example.test", work_phone="555-1",
            emergency_contact_name="Charles", emergency_contact_relationship="Colleague",
            emergency_contact_phone="555-2", notes_private="restricted")
        tech = self.service.get_technician(tech_id)
        self.assertEqual(tech["drivers_license_state"], "NY")
        self.assertEqual(tech["ssn_last4"], "1234")
        for field, value in (("email", "bad"), ("alternate_email", "bad"),
                             ("date_of_birth", "2023-02-29"), ("hire_date", "01/01/2020"),
                             ("termination_date", "2020-13-01"), ("ssn_last4", "123-45-6789"),
                             ("drivers_license_state", "New York")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.service.update_technician(self.admin, tech_id, {field: value})

    def test_sensitive_update_audit_contains_names_not_values(self):
        tech_id = self.create_tech()
        secrets = {"ssn_last4": "9876", "drivers_license_number": "SECRET-DL",
                   "notes_private": "SECRET NOTES", "date_of_birth": "2000-01-01"}
        self.service.update_technician(self.admin, tech_id, secrets)
        with self.auth.connection() as connection:
            raw = connection.execute("SELECT details_json FROM AuditLog "
                                     "WHERE action='technician_updated' ORDER BY id DESC").fetchone()[0]
        self.assertTrue(set(secrets).issubset(json.loads(raw)["fields_changed"]))
        for value in secrets.values(): self.assertNotIn(value, raw)

    def test_atomic_deactivation_records_details_and_rolls_back_on_audit_failure(self):
        tech_id = self.create_tech()
        with patch("app.services.technician_service.record_event",
                   side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.service.deactivate_technician(self.admin, tech_id, "2026-07-28", "Paused")
        self.assertEqual(self.service.get_technician(tech_id)["status"], "Active")
        self.service.deactivate_technician(self.admin, tech_id, "2026-07-28", "Paused")
        tech = self.service.get_technician(tech_id)
        self.assertEqual((tech["status"], tech["termination_date"], tech["inactive_reason"]),
                         ("Inactive", "2026-07-28", "Paused"))
        self.service.set_technician_active(self.admin, tech_id, True)
        tech = self.service.get_technician(tech_id)
        self.assertEqual((tech["status"], tech["termination_date"], tech["inactive_reason"]),
                         ("Active", "2026-07-28", "Paused"))

    def test_mutations_require_admin(self):
        with self.assertRaises(AuthorizationError):
            self.service.create_technician(self.operator,
                                           {"tech_code": "T", "first_name": "A", "last_name": "B"})
        tech_id = self.create_tech()
        operations = [
            lambda: self.service.update_technician(self.operator, tech_id, {"first_name": "B"}),
            lambda: self.service.set_technician_active(self.operator, tech_id, False),
            lambda: self.service.add_address(self.operator, tech_id, self.address()),
        ]
        for operation in operations:
            with self.assertRaises(AuthorizationError):
                operation()

    def test_get_missing_and_positive_identifier_validation(self):
        self.assertIsNone(self.service.get_technician(999))
        for identifier in (0, -1, True, "1"):
            with self.assertRaises(ValueError):
                self.service.get_technician(identifier)

    def test_listing_active_filter_and_deterministic_order(self):
        zed = self.create_tech("T3", "Zed", "Alpha")
        amy = self.create_tech("T2", "Amy", "Alpha")
        inactive = self.create_tech("T1", "Bob", "Beta")
        self.service.set_technician_active(self.admin, inactive, False)
        self.assertEqual([r["tech_id"] for r in self.service.list_technicians()], [amy, zed])
        self.assertEqual([r["tech_id"] for r in self.service.list_technicians(True)],
                         [amy, zed, inactive])

    def test_search_names_contacts_partial_case_and_inactive(self):
        ada = self.create_tech("ADA", "Ada", "Lovelace", email="math@example.test")
        grace = self.create_tech("GH", "Grace", "Hopper", preferred_name="Amazing Grace",
                                 mobile_phone="555-0199")
        self.service.set_technician_active(self.admin, grace, False)
        self.assertEqual([r["tech_id"] for r in self.service.search_technicians("LOVe")], [ada])
        self.assertEqual([r["tech_id"] for r in self.service.search_technicians("019", True)], [grace])
        self.assertEqual(self.service.search_technicians("Grace"), [])
        self.assertEqual(self.service.search_technicians("  "), self.service.list_technicians())

    def test_update_allowlist_validation_missing_and_audit(self):
        tech_id = self.create_tech()
        updated = self.service.update_technician(self.admin, tech_id,
                                                 {"first_name": " Grace ", "email": "g@example.test"})
        self.assertEqual(updated["first_name"], "Grace")
        self.assertIsNotNone(updated["updated_at"])
        for changes in ({"tech_id": 20}, {"status": "Inactive"}, {}):
            with self.assertRaises(ValueError):
                self.service.update_technician(self.admin, tech_id, changes)
        with self.assertRaises(LookupError):
            self.service.update_technician(self.admin, 999, {"first_name": "X"})
        self.assertIn("technician_updated", self.actions())

    def test_activation_is_idempotent_preserves_record_and_audits(self):
        tech_id = self.create_tech()
        self.service.set_technician_active(self.admin, tech_id, False)
        self.service.set_technician_active(self.admin, tech_id, False)
        self.assertEqual(self.service.get_technician(tech_id)["status"], "Inactive")
        self.assertEqual(self.service.list_technicians(), [])
        self.service.set_technician_active(self.admin, tech_id, True)
        self.assertEqual(len(self.service.list_technicians()), 1)
        self.assertIn("technician_deactivated", self.actions())
        self.assertIn("technician_activated", self.actions())

    def test_add_addresses_primary_rules_normalization_and_order(self):
        tech_id = self.create_tech()
        first = self.service.add_address(self.admin, tech_id, self.address())
        second = self.service.add_address(self.admin, tech_id,
                                          self.address(address_1="Other", is_primary=False))
        third = self.service.add_address(self.admin, tech_id,
                                         self.address(address_1="Newest", is_primary=True))
        rows = self.service.list_addresses(tech_id)
        self.assertEqual([r["address_id"] for r in rows], [third, first, second])
        self.assertEqual(sum(r["is_primary"] for r in rows), 1)
        self.assertEqual(rows[0]["state"], "NY")
        self.assertEqual(rows[0]["zip_code"], "00123")
        self.assertIn("technician_address_added", self.actions())

    def test_address_validation_and_invalid_parent(self):
        tech_id = self.create_tech()
        with self.assertRaises(LookupError):
            self.service.add_address(self.admin, 999, self.address())
        with self.assertRaises(LookupError):
            self.service.list_addresses(999)
        with self.assertRaises(ValueError):
            self.service.add_address(self.admin, tech_id, {"address_1": "x"})
        with self.assertRaises(ValueError):
            self.service.add_address(self.admin, tech_id, self.address(is_primary=1))

    def test_update_address_and_primary_uniqueness(self):
        tech_id = self.create_tech()
        first = self.service.add_address(self.admin, tech_id, self.address())
        second = self.service.add_address(self.admin, tech_id,
                                          self.address(address_1="Second", is_primary=False))
        result = self.service.update_address(self.admin, tech_id, second,
                                             {"city": " Paris ", "is_primary": True})
        self.assertEqual(result["city"], "Paris")
        rows = self.service.list_addresses(tech_id)
        self.assertEqual([r["address_id"] for r in rows if r["is_primary"]], [second])
        self.service.update_address(self.admin, tech_id, second, {"is_primary": False})
        self.assertFalse(any(r["is_primary"] for r in self.service.list_addresses(tech_id)))
        with self.assertRaises(ValueError):
            self.service.update_address(self.admin, tech_id, first, {"address_id": 3})
        self.assertIn("technician_address_updated", self.actions())

    def test_address_ownership_is_enforced(self):
        first_tech = self.create_tech()
        second_tech = self.create_tech("T002", "Grace", "Hopper")
        address_id = self.service.add_address(self.admin, first_tech, self.address())
        for operation in (
            lambda: self.service.update_address(self.admin, second_tech, address_id, {"city": "X"}),
            lambda: self.service.set_primary_address(self.admin, second_tech, address_id),
            lambda: self.service.delete_address(self.admin, second_tech, address_id),
        ):
            with self.assertRaisesRegex(LookupError, "does not belong"):
                operation()

    def test_each_technician_can_have_a_primary_and_explicit_change_is_audited(self):
        one = self.create_tech()
        two = self.create_tech("T002", "Grace", "Hopper")
        old = self.service.add_address(self.admin, one, self.address())
        new = self.service.add_address(self.admin, one, self.address(address_1="New", is_primary=False))
        other = self.service.add_address(self.admin, two, self.address())
        self.service.set_primary_address(self.admin, one, new)
        self.assertEqual([r["address_id"] for r in self.service.list_addresses(one) if r["is_primary"]], [new])
        self.assertEqual([r["address_id"] for r in self.service.list_addresses(two) if r["is_primary"]], [other])
        with self.auth.connection() as connection:
            with self.assertRaises(Exception):
                connection.execute("UPDATE TechAddresses SET is_primary=1 WHERE address_id=?", (old,))
        self.assertIn("technician_primary_address_changed", self.actions())

    def test_delete_non_primary_and_primary_selects_no_replacement(self):
        tech_id = self.create_tech()
        primary = self.service.add_address(self.admin, tech_id, self.address())
        secondary = self.service.add_address(self.admin, tech_id,
                                             self.address(address_1="Second", is_primary=False))
        self.service.delete_address(self.admin, tech_id, secondary)
        self.service.delete_address(self.admin, tech_id, primary)
        self.assertEqual(self.service.list_addresses(tech_id), [])
        self.assertEqual(self.actions().count("technician_address_deleted"), 2)

    def test_technician_change_rolls_back_when_audit_fails(self):
        def fail(*args, **kwargs):
            raise RuntimeError("audit unavailable")
        with patch("app.services.technician_service.record_event", side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.create_tech()
        self.assertEqual(self.service.list_technicians(), [])

    def test_address_change_rolls_back_when_audit_fails(self):
        tech_id = self.create_tech()
        with patch("app.services.technician_service.record_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.service.add_address(self.admin, tech_id, self.address())
        self.assertEqual(self.service.list_addresses(tech_id), [])

    def test_audit_details_identify_objects_without_contact_data(self):
        tech_id = self.create_tech()
        address_id = self.service.add_address(self.admin, tech_id, self.address())
        with self.auth.connection() as connection:
            row = connection.execute("SELECT actor_user_id, details_json FROM AuditLog "
                                     "WHERE action='technician_address_added'").fetchone()
        details = json.loads(row["details_json"])
        self.assertEqual((row["actor_user_id"], details["tech_id"], details["address_id"]),
                         (self.admin.user_id, tech_id, address_id))
        self.assertNotIn("address_1", details)

    def test_temporary_database_can_be_removed_after_connections_close(self):
        self.create_tech()
        path = self.db_path
        self.tempdir.cleanup()
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
