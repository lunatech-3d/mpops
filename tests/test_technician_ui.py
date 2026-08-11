"""Non-pixel tests for the technician UI's controller and form mappings."""
import importlib
import unittest
from unittest.mock import MagicMock

from app.security.auth import Session
from app.ui.address_form import address_form_data
from app.ui.technician_form import TECHNICIAN_FIELDS, changed_fields, technician_form_data
from app.ui.technician_manager import (TechnicianController, TechnicianDetails,
                                       TechnicianManager, display_name)


class TechnicianUiHelpersTests(unittest.TestCase):
    def test_complete_fields_and_operational_columns_exclude_internal_and_sensitive_data(self):
        expected = {"tech_code", "first_name", "middle_name", "last_name", "suffix",
                    "preferred_name", "company_name", "contractor_type", "inactive_reason",
                    "date_of_birth", "ssn_last4", "drivers_license_number",
                    "drivers_license_state", "email", "alternate_email", "mobile_phone",
                    "home_phone", "work_phone", "emergency_contact_name",
                    "emergency_contact_relationship", "emergency_contact_phone", "hire_date",
                    "termination_date", "notes", "notes_private"}
        self.assertEqual(set(TECHNICIAN_FIELDS), expected)
        self.assertTrue({"tech_id", "created_by", "updated_by"}.isdisjoint(TECHNICIAN_FIELDS))
        self.assertEqual(TechnicianManager.COLUMNS[0], "first_name")
        self.assertNotIn("tech_id", TechnicianManager.COLUMNS)
        self.assertTrue(all("ID" not in heading.upper().split() for heading in TechnicianManager.HEADINGS))
        self.assertTrue({"ssn_last4", "date_of_birth", "notes_private"}.isdisjoint(TechnicianManager.COLUMNS))

    def test_tree_defaults_to_first_name_in_ascending_order(self):
        self.assertEqual(TechnicianManager.DEFAULT_SORT_COLUMN, "first_name")
        self.assertIs(TechnicianManager.DEFAULT_SORT_DESCENDING, False)

    def test_display_name_uses_all_nonblank_components(self):
        self.assertEqual(display_name({"first_name": "Ada", "middle_name": "M",
                                       "last_name": "Lovelace", "suffix": "III"}),
                         "Ada M Lovelace III")

    def test_ui_modules_import(self):
        for name in ("app.ui.technician_form", "app.ui.address_form", "app.ui.technician_manager"):
            self.assertIsNotNone(importlib.import_module(name))

    def test_technician_payload_and_required_values(self):
        data = technician_form_data({"tech_code": " T1 ", "first_name": " Ada ", "last_name": " Lovelace "})
        self.assertEqual(data["tech_code"], "T1")
        self.assertIsNone(data["email"])
        self.assertIsNone(data["termination_date"])
        with self.assertRaisesRegex(ValueError, "First Name"):
            technician_form_data({"tech_code": "T1", "last_name": "Lovelace"})

    def test_changed_fields_only_returns_editable_differences(self):
        submitted = technician_form_data({"tech_code": "T1", "first_name": "Ada", "last_name": "Byron"})
        original = {**submitted, "last_name": "Lovelace", "tech_id": 91, "status": "Active"}
        self.assertEqual(changed_fields(original, submitted), {"last_name": "Byron"})

    def test_address_payload_uses_boolean_and_normalizes_blanks(self):
        data = address_form_data({"address_1": "1 Main", "city": "Town", "state": "CA",
                                  "zip_code": "12345", "is_primary": 1})
        self.assertIs(data["is_primary"], True)
        self.assertIsNone(data["address_2"])
        with self.assertRaisesRegex(ValueError, "City"):
            address_form_data({"address_1": "1 Main", "state": "CA", "zip_code": "12345"})

    def test_roles_control_mutation_permission(self):
        for role, expected in (("admin", True), ("operator", False), ("viewer", False)):
            self.assertEqual(TechnicianController(MagicMock(), Session(1, "u", role)).can_modify, expected)

    def test_details_tabs_put_jobs_first_profile_last_and_omit_addresses(self):
        self.assertEqual(TechnicianDetails.TAB_ORDER[0], "Jobs")
        self.assertEqual(TechnicianDetails.TAB_ORDER[-1], "Profile")
        self.assertNotIn("Addresses", TechnicianDetails.TAB_ORDER)

    def test_profile_formats_current_address_and_empty_state(self):
        self.assertEqual(TechnicianDetails.format_current_address(None),
                         "No address on file")
        text = TechnicianDetails.format_current_address({
            "address_1": "12 Main", "address_2": "Suite 3", "city": "Austin",
            "state": "TX", "zip_code": "78701", "effective_date": "2026-08-01",
        })
        self.assertIn("Address Line 1: 12 Main", text)
        self.assertIn("Address Line 2: Suite 3", text)
        self.assertIn("City: Austin", text)
        self.assertIn("Effective Date: 08/01/2026", text)

    def test_empty_load_and_service_routing(self):
        service = MagicMock(); service.list_technicians.return_value = []
        controller = TechnicianController(service, Session(1, "admin", "admin"))
        self.assertEqual(controller.load(), [])
        service.list_technicians.assert_called_once_with(False)
        controller.load("Ada", True)
        service.search_technicians.assert_called_once_with("Ada", True)

    def test_create_edit_deactivate_and_address_operations_use_service(self):
        service = MagicMock(); session = Session(7, "admin", "admin")
        controller = TechnicianController(service, session)
        tech = technician_form_data({"tech_code": "T1", "first_name": "Ada", "last_name": "Lovelace"})
        controller.create(tech); service.create_technician.assert_called_once_with(session, tech)
        edited = dict(tech, preferred_name="A")
        controller.update(11, tech, edited)
        service.update_technician.assert_called_once_with(session, 11, {"preferred_name": "A"})
        controller.set_active(11, False)
        service.set_technician_active.assert_called_once_with(session, 11, False)
        address = address_form_data({"address_1": "1 Main", "city": "Town", "state": "CA", "zip_code": "1"})
        controller.add_address(11, address); service.add_address.assert_called_once_with(session, 11, address)
        changed = dict(address, city="Elsewhere")
        controller.update_address(11, 4, address, changed)
        service.update_address.assert_called_once_with(session, 11, 4, {"city": "Elsewhere"})
        controller.delete_address(11, 4); service.delete_address.assert_called_once_with(session, 11, 4)


if __name__ == "__main__":
    unittest.main()
