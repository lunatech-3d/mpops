"""Nonvisual Phase III revenue-rule formatting and controller tests."""
import unittest
from datetime import date
from unittest.mock import MagicMock

from app.security.auth import Session
from app.security.user_manager import AuthorizationError
from app.ui.revenue_rule_controllers import (MarketRevenueShareController,
    TechnicianCompensationController, classify_applicability)
from app.ui.revenue_rule_formatting import (amount_to_cents, format_basis_points,
                                             percentage_to_basis_points)


class FormattingTests(unittest.TestCase):
    def test_basis_point_display(self):
        for value, expected in ((7000,"70%"),(7250,"72.5%"),(7055,"70.55%"),(0,"0%"),(10000,"100%")):
            self.assertEqual(format_basis_points(value), expected)
    def test_percentage_entry(self):
        for value, expected in (("70",7000),("72.5",7250),("70.55",7055),("0",0),("100",10000)):
            self.assertEqual(percentage_to_basis_points(value), expected)
        for invalid in ("", "word", "-1", "100.01", "70.555", "NaN", "Infinity", "-Infinity"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): percentage_to_basis_points(invalid)
    def test_amount_entry(self):
        self.assertEqual(amount_to_cents("125"),12500);self.assertEqual(amount_to_cents("125.50"),12550);self.assertEqual(amount_to_cents("0"),0)
        for invalid in ("-1","1.001","nope","NaN","Infinity"):
            with self.assertRaises(ValueError):amount_to_cents(invalid)


class ControllerTests(unittest.TestCase):
    def test_applicability(self):
        today="2026-07-31"
        self.assertEqual(classify_applicability({"is_active":1,"effective_from":"2026-01-01"},today),"Current")
        self.assertEqual(classify_applicability({"is_active":1,"effective_from":"2026-08-01"},today),"Future")
        self.assertEqual(classify_applicability({"is_active":1,"effective_from":"2026-01-01","effective_to":"2026-07-30"},today),"Expired")
        self.assertEqual(classify_applicability({"is_active":0,"effective_from":"2026-01-01"},today),"Inactive")
    def test_technician_effective_source_zero_and_scoped_create(self):
        service=MagicMock();session=Session(1,"a","admin");controller=TechnicianCompensationController(service,session)
        service.resolve_technician_profile_rule.return_value={"scope_type":"Technician","rule_type":"Percentage","rule_value":0,"is_active":1,"effective_from":"2026-01-01"}
        result=controller.effective(3,date(2026,7,1));self.assertEqual((result["display_value"],result["source_label"]),("0%","Technician override"))
        controller.create(3,rule_type="Percentage",rule_value=7000,compensation_component="Overall",effective_from="2026-01-01",effective_to=None,is_active=True)
        self.assertEqual(service.create_technician_rule.call_args.kwargs["scope_id"],3)
    def test_system_source_and_history_scope(self):
        service=MagicMock();controller=TechnicianCompensationController(service,Session(1,"v","viewer"))
        service.resolve_technician_profile_rule.return_value={"scope_type":"System","rule_type":"Percentage","rule_value":7000,"is_active":1,"effective_from":"2026-01-01"}
        self.assertEqual(controller.effective(8,"2026-07-01")["source_label"],"System default")
        service.list_technician_rules_for.return_value=[];controller.history(8);service.list_technician_rules_for.assert_called_once_with(8,include_inactive=True)
        with self.assertRaises(AuthorizationError):controller.create(8)
    def test_market_summaries_keep_zero_missing_and_integrity(self):
        service=MagicMock();controller=MarketRevenueShareController(service,Session(1,"a","admin"))
        service.get_current_market_share_summary.return_value={1:{"status":"resolved","rule":{"share_basis_points":0}},2:{"status":"resolved","rule":{"share_basis_points":1000}},3:{"status":"missing","rule":None},4:{"status":"integrity_error","rule":None}}
        result=controller.summaries([1,2,3,4],"2026-07-01")
        self.assertEqual([result[i]["display_value"] for i in range(1,5)],["0%","10%","Not configured","Configuration error"])
    def test_non_admin_market_mutation_rejected(self):
        for role in ("operator","viewer"):
            with self.assertRaises(AuthorizationError):MarketRevenueShareController(MagicMock(),Session(1,"u",role)).create(1)


if __name__ == "__main__": unittest.main()
