"""Focused coverage for typed Matterport remittance adjustments."""

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.security.auth import AuthService
from app.security.user_manager import UserManager
from app.services.matterport_email_parser import (parse_matterport_payment_email,
                                                   parse_signed_usd_amount)
from app.services.payment_service import PaymentService
from app.ui.matterport_email_import_dialog import confirmation_message


class MatterportRemittanceAdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.auth = AuthService(Settings(Path(self.temp.name) / "test.db", password_iterations=100_000))
        UserManager(self.auth).create_user("operator", "operator-password-123", "admin")
        self.session = self.auth.authenticate("operator", "operator-password-123")
        self.service = PaymentService(self.auth)

    def tearDown(self):
        self.temp.cleanup()

    def test_signed_amount_variations_and_invalid_input(self):
        for source in ("(USD 87.66)", "-USD 87.66", "USD -87.66", "(87.66)"):
            with self.subTest(source=source):
                self.assertEqual(parse_signed_usd_amount(source), -8766)
        with self.assertRaises(ValueError):
            parse_signed_usd_amount("USD bananas")

    def test_parser_preserves_invoice_and_vendor_credit(self):
        result = parse_matterport_payment_email(
            "A USD 3,744.81 payment was sent to you today by ACH and covers\n"
            "Amount Type Document number Document date\n"
            "USD 3,832.47 | Invoice | AP-invoices | 06/07/2026\n"
            "(USD 87.66) | Vendor credit | AP-recnDcfXhziZEKJzc | 06/08/2026\n")
        self.assertEqual([r["document_type"] for r in result["rows"]],
                         ["Invoice", "Vendor Credit"])
        self.assertEqual(result["rows"][1]["signed_effect_cents"], -8766)
        self.assertEqual(result["rows"][1]["allocation_status"],
                         "Account Allocation Required")

    def _example_batch(self):
        batch = self.service.create_payment_batch(self.session, {
            "payment_date": "2026-08-06", "payment_amount_cents": 374481})
        amounts = [22500] * 16 + [23247]
        items = [{"document_number": f"INV-{n}", "document_type": "Invoice",
                  "amount_received_cents": amount} for n, amount in enumerate(amounts)]
        items.append({"document_number": "AP-recnDcfXhziZEKJzc",
                      "document_type": "Vendor Credit", "amount_received_cents": 8766,
                      "signed_effect_cents": -8766})
        imported = self.service.import_payment_items(self.session, batch, items)
        with self.auth.connection() as connection:
            connection.execute("UPDATE MatterportPaymentItems SET match_status='Matched' "
                               "WHERE payment_batch_id=?", (batch,))
        self.service.update_payment_batch(self.session, batch, {"batch_status": "Imported"})
        return batch, imported["payment_item_ids"][-1]

    def test_example_reconciles_without_rewriting_invoice_or_earnings(self):
        batch, credit = self._example_batch()
        totals = self.service.calculate_batch_totals(batch)
        self.assertEqual(totals["gross_invoice_total_cents"], 383247)
        self.assertEqual(totals["vendor_credits_cents"], 8766)
        self.assertEqual(totals["expected_net_payment_cents"], 374481)
        self.assertEqual(totals["difference_cents"], 0)
        reconciliation = self.service.validate_batch_reconciliation(batch)
        self.assertTrue(reconciliation["ready"])
        self.assertTrue(reconciliation["summary"]["allocation_required"])
        with self.auth.connection() as connection:
            invoice_total = connection.execute(
                "SELECT SUM(amount_received_cents) FROM MatterportPaymentItems "
                "WHERE payment_batch_id=? AND document_type='Invoice'", (batch,)).fetchone()[0]
            earnings = connection.execute("SELECT COUNT(*) FROM TechnicianJobEarnings").fetchone()[0]
        self.assertEqual(invoice_total, 383247)
        self.assertEqual(earnings, 0)
        self.assertEqual(self.service.get_payment_batch(batch)["payment_amount_cents"], 374481)
        rows = self.service.list_payment_items(batch)
        self.assertEqual(len(rows), 18)
        self.assertEqual(rows[-1]["payment_item_id"], credit)
        self.assertEqual(rows[-1]["signed_effect_cents"], -8766)
        self.assertEqual(totals["imported_total_cents"], 374481)
        self.assertNotEqual(totals["imported_total_cents"], 392013)

    def test_full_import_preview_and_confirmation_show_gross_credit_and_net(self):
        invoice_lines = "\n".join(
            f"USD {amount / 100:.2f} | Invoice | INV-{number} | 06/07/2026"
            for number, amount in enumerate([22500] * 16 + [23247]))
        parsed = parse_matterport_payment_email(
            "A USD 3,744.81 payment was sent to you today by ACH and covers\n"
            "Amount Type Document number Document date\n" + invoice_lines +
            "\n(USD 87.66) | Vendor credit | CREDIT-1 | 06/08/2026\n")
        summary = parsed["summary"]
        self.assertEqual(summary["gross_invoice_total_cents"], 383247)
        self.assertEqual(summary["vendor_credit_total_cents"], -8766)
        self.assertEqual(summary["valid_count"], 18)
        self.assertEqual(summary["importable_total_cents"], 374481)
        message = confirmation_message(summary)
        self.assertIn("17 invoices: $3,832.47", message)
        self.assertIn("1 vendor credit: ($87.66)", message)
        self.assertIn("Net payment: $3,744.81", message)
        self.assertNotIn("$3,920.13", message)

    def test_credit_can_split_and_cannot_be_overallocated(self):
        batch, credit = self._example_batch()
        first = self.service.allocate_adjustment(self.session, credit, 4000,
                                                 account_name="Matterport Account A")
        self.assertEqual(first["allocation_status"], "Partially Allocated")
        second = self.service.allocate_adjustment(self.session, credit, 4766,
                                                  account_name="Matterport Account B")
        self.assertEqual(second["allocation_status"], "Allocated")
        self.assertEqual(len(self.service.list_adjustment_allocations(credit)), 2)
        with self.assertRaisesRegex(ValueError, "exceed"):
            self.service.allocate_adjustment(self.session, credit, 1, account_name="Account C")

    def test_direction_conflict_requires_review_and_duplicate_is_atomic(self):
        batch = self.service.create_payment_batch(self.session, {
            "payment_date": "2026-08-06", "payment_amount_cents": 1})
        item = {"document_number": "BAD-CREDIT", "document_type": "Vendor Credit",
                "amount_received_cents": 1, "signed_effect_cents": 1}
        result = self.service.import_payment_items(self.session, batch, [item])
        self.assertEqual(self.service.list_payment_items(batch)[0]["direction_status"], "Invalid")
        with self.assertRaisesRegex(ValueError, "already been imported"):
            self.service.import_payment_items(self.session, batch, [
                {"document_number": "NEW", "amount_received_cents": 1},
                {"document_number": "BAD-CREDIT", "amount_received_cents": 1}])
        self.assertEqual(len(self.service.list_payment_items(batch)), result["imported_count"])

    def test_positive_adjustment_and_fee_net_effect(self):
        batch = self.service.create_payment_batch(self.session, {
            "payment_date": "2026-08-06", "payment_amount_cents": 10500})
        self.service.import_payment_items(self.session, batch, [
            {"document_number": "I", "document_type": "Invoice", "amount_received_cents": 10000},
            {"document_number": "A", "document_type": "Positive Adjustment", "amount_received_cents": 1000},
            {"document_number": "F", "document_type": "Fee or Deduction", "amount_received_cents": 500},
        ])
        totals = self.service.calculate_batch_totals(batch)
        self.assertEqual(totals["expected_net_payment_cents"], 10500)


if __name__ == "__main__":
    unittest.main()
