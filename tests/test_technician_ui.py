"""Non-pixel tests for the technician UI's controller and form mappings."""
import importlib
import unittest
from unittest.mock import MagicMock

from app.security.auth import Session
from app.ui.address_form import address_form_data
from app.ui.technician_form import changed_fields, technician_form_data
from app.ui.technician_manager import TechnicianController


class TechnicianUiHelpersTests(unittest.TestCase):
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
