"""Financial reconciliation workflow coverage."""

import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.security.auth import AuthService
from app.security.user_manager import UserManager
from app.services.payment_service import PaymentService


class PaymentReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.auth = AuthService(Settings(Path(self.directory.name) / "test.db",
                                         password_iterations=100_000))
        UserManager(self.auth).create_user("operator", "operator-password-123", "admin")
        self.session = self.auth.authenticate("operator", "operator-password-123")
        self.service = PaymentService(self.auth)

    def tearDown(self):
        self.directory.cleanup()

    def batch(self, amount=100):
        return self.service.create_payment_batch(
            self.session, {"payment_date": "2026-07-29", "payment_amount_cents": amount})

    def ready_batch(self):
        batch_id = self.batch()
        item_id = self.service.add_payment_item(
            self.session, batch_id,
            {"document_number": "READY-1", "amount_received_cents": 100})
        with self.auth.connection() as connection:
            connection.execute("UPDATE MatterportPaymentItems SET match_status='Matched' "
                               "WHERE payment_item_id=?", (item_id,))
        self.service.update_payment_batch(self.session, batch_id,
                                          {"batch_status": "Imported"})
        return batch_id, item_id

    def test_validation_is_structured_and_reports_every_blocker(self):
        batch_id = self.batch(1825)
        item_id = self.service.add_payment_item(
            self.session, batch_id,
            {"document_number": "MISSING", "amount_received_cents": 0})
        with self.auth.connection() as connection:
            connection.execute("UPDATE MatterportPaymentItems SET match_status='Missing Job' "
                               "WHERE payment_item_id=?", (item_id,))
        self.service.update_payment_batch(self.session, batch_id,
                                          {"batch_status": "Imported"})
        result = self.service.validate_batch_reconciliation(batch_id)
        self.assertEqual(set(result), {"ready", "errors", "warnings", "summary"})
        self.assertFalse(result["ready"])
        self.assertIn("Missing Job items remain.", result["errors"])
        self.assertIn("Effective payment total differs by $18.25.", result["errors"])

    def test_empty_and_amount_review_batches_are_blocked(self):
        empty = self.batch()
        self.service.update_payment_batch(self.session, empty, {"batch_status": "Imported"})
        self.assertIn("Payment batch contains no imported items.",
                      self.service.validate_batch_reconciliation(empty)["errors"])
        review = self.batch()
        item = self.service.add_payment_item(
            self.session, review, {"document_number": "REVIEW", "amount_received_cents": 100})
        with self.auth.connection() as connection:
            connection.execute("UPDATE MatterportPaymentItems SET match_status='Amount Review' "
                               "WHERE payment_item_id=?", (item,))
        self.service.update_payment_batch(self.session, review, {"batch_status": "Imported"})
        self.assertIn("Amount Review items remain.",
                      self.service.validate_batch_reconciliation(review)["errors"])

    def test_reconcile_stores_snapshot_audit_history_and_freezes_mutations(self):
        batch_id, item_id = self.ready_batch()
        self.assertTrue(self.service.validate_batch_reconciliation(batch_id)["ready"])
        reconciled = self.service.reconcile_batch(self.session, batch_id)
        self.assertEqual(reconciled["batch_status"], "Reconciled")
        self.assertEqual(reconciled["reconciled_imported_total_cents"], 100)
        self.assertEqual(reconciled["reconciled_effective_total_cents"], 100)
        self.assertEqual(reconciled["reconciled_payment_amount_cents"], 100)
        self.assertEqual(reconciled["reconciled_matched_count"], 1)
        self.assertEqual(reconciled["reconciled_excluded_count"], 0)
        self.assertEqual(reconciled["reconciled_difference_cents"], 0)
        with self.auth.connection() as connection:
            audit = connection.execute(
                "SELECT details_json FROM AuditLog WHERE action='payment_batch_reconciled'"
            ).fetchone()
        self.assertEqual(json.loads(audit[0])["batch_id"], batch_id)
        self.assertIn("payment_batch_reconciled",
                      [entry["event"] for entry in self.service.get_batch_history(batch_id)])
        mutations = (
            lambda: self.service.assign_payment_item_job(self.session, item_id, 1),
            lambda: self.service.exclude_payment_item(self.session, item_id),
            lambda: self.service.update_payment_item_resolution_notes(self.session, item_id, "x"),
            lambda: self.service.delete_payment_item(self.session, item_id),
            lambda: self.service.update_payment_batch(self.session, batch_id, {"notes": "x"}),
            lambda: self.service.import_payment_items(self.session, batch_id, [
                {"document_number": "NEW", "amount_received_cents": 1}]),
        )
        for mutation in mutations:
            with self.assertRaises(ValueError):
                mutation()

    def test_reconcile_revalidates_and_rolls_back(self):
        batch_id, _ = self.ready_batch()
        with self.auth.connection() as connection:
            connection.execute("UPDATE MatterportPaymentBatches SET payment_amount_cents=101 "
                               "WHERE payment_batch_id=?", (batch_id,))
        with self.assertRaisesRegex(ValueError, "differs"):
            self.service.reconcile_batch(self.session, batch_id)
        batch = self.service.get_payment_batch(batch_id)
        self.assertEqual(batch["batch_status"], "Imported")
        self.assertIsNone(batch["reconciled_at"])


if __name__ == "__main__":
    unittest.main()
