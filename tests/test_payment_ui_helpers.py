"""Nonvisual tests for payment UI presentation policy."""

import unittest

from app.ui.payment_helpers import (format_cents, next_batch_status, parse_currency,
                                    status_permissions, totals_to_display, import_preview_summary,
                                    visible_exception_tabs, workflow_summary)


class PaymentUiHelperTests(unittest.TestCase):
    def test_format_cents(self):
        self.assertEqual(format_cents(364010), "$3,640.10")
        self.assertEqual(format_cents(-25), "-$0.25")
        self.assertEqual(format_cents(None), "$0.00")

    def test_parse_currency(self):
        self.assertEqual(parse_currency("3640.10"), 364010)
        self.assertEqual(parse_currency("$3,640.10"), 364010)
        self.assertEqual(parse_currency("0"), 0)
        for invalid in ("", "money", "1.001", "-0.01", "NaN"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_currency(invalid)

    def test_next_status(self):
        self.assertEqual(next_batch_status("Draft"), "Imported")
        self.assertEqual(next_batch_status("Approved"), "Closed")
        self.assertIsNone(next_batch_status("Closed"))
        self.assertIsNone(next_batch_status("Cancelled"))

    def test_status_permissions(self):
        draft = status_permissions("Draft")
        self.assertIn("payment_date", draft["editable_fields"])
        self.assertTrue(draft["can_match"]); self.assertTrue(draft["can_delete"])
        review = status_permissions("Needs Review")
        self.assertEqual(review["editable_fields"], frozenset({"notes"}))
        self.assertTrue(review["can_match"])
        self.assertFalse(status_permissions("Closed")["can_save"])
        self.assertFalse(status_permissions("Draft", can_modify=False)["can_match"])

    def test_totals_to_display(self):
        display = totals_to_display({"payment_amount_cents": 123456, "difference_cents": -5,
                                     "item_count": 3, "exception_count": 1})
        self.assertEqual(display["payment_amount_cents"], "$1,234.56")
        self.assertEqual(display["difference_cents"], "-$0.05")
        self.assertEqual(display["item_count"], "3")
        self.assertEqual(display["matched_count"], "0")


    def test_import_preview_and_workflow_summary(self):
        preview = import_preview_summary(1000, 200, 700)
        self.assertEqual(preview["difference_after_import"], "$1.00")
        self.assertFalse(preview["balances"])
        lines = workflow_summary("Draft", {"item_count": 2, "missing_job_count": 1,
            "ambiguous_count": 0, "difference_cents": 0, "exception_count": 1})
        self.assertIn("⚠ 1 Missing Jobs", lines)
        self.assertIn("✓ Totals Balanced", lines)
        self.assertEqual(lines[-1], "□ Ready for Reconciliation")

    def test_exception_tab_visibility_preserves_operational_order(self):
        groups = {"Excluded": [{"id": 2}], "Missing Jobs": [{"id": 1}],
                  "Duplicates": [], "Amount Review": []}
        self.assertEqual(visible_exception_tabs(groups), ("Missing Jobs", "Excluded"))


if __name__ == "__main__":
    unittest.main()
