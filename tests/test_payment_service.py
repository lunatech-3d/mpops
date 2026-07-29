"""Unit and transaction coverage for the Matterport payment service API."""

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.security.auth import AuthService
from app.security.user_manager import UserManager
from app.services.payment_service import PaymentService


class PaymentServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.auth = AuthService(
            Settings(Path(self.tempdir.name) / "mpops.db", password_iterations=100_000)
        )
        users = UserManager(self.auth)
        users.create_user("admin", "admin-password-123", "admin")
        self.session = self.auth.authenticate("admin", "admin-password-123")
        self.service = PaymentService(self.auth)

    def tearDown(self):
        self.tempdir.cleanup()

    def create_batch(self, amount=10000):
        return self.service.create_payment_batch(
            self.session,
            {"payment_date": "2026-07-29", "payment_amount_cents": amount},
        )

    def add_item(self, batch_id, document, amount):
        return self.service.add_payment_item(
            self.session,
            batch_id,
            {"document_number": document, "amount_received_cents": amount},
        )

    def create_job(self, external_id):
        with self.auth.connection() as connection:
            return int(connection.execute(
                "INSERT INTO Jobs (external_job_id, created_by) VALUES (?, ?)",
                (external_id, self.session.user_id),
            ).lastrowid)

    def test_fresh_batch_creation_retrieval_and_listing(self):
        batch_id = self.create_batch()
        batch = self.service.get_payment_batch(batch_id)
        self.assertEqual(batch["payment_date"], "2026-07-29")
        self.assertEqual(batch["payment_amount_cents"], 10000)
        self.assertEqual(batch["batch_status"], "Draft")
        self.assertEqual([row["payment_batch_id"] for row in self.service.list_payment_batches()],
                         [batch_id])

    def test_batch_update_and_status_transitions(self):
        batch_id = self.create_batch()
        updated = self.service.update_payment_batch(
            self.session, batch_id, {"notes": "Tipalti receipt", "batch_status": "Imported"}
        )
        self.assertEqual(updated["notes"], "Tipalti receipt")
        for status in ("Needs Review", "Reconciled", "Approved", "Closed"):
            updated = self.service.update_payment_batch(
                self.session, batch_id, {"batch_status": status}
            )
            self.assertEqual(updated["batch_status"], status)
        with self.assertRaisesRegex(ValueError, "transition"):
            self.service.update_payment_batch(
                self.session, batch_id, {"batch_status": "Cancelled"}
            )
        cancellable = self.create_batch()
        self.assertEqual(self.service.update_payment_batch(
            self.session, cancellable, {"batch_status": "Cancelled"}
        )["batch_status"], "Cancelled")
        with self.assertRaisesRegex(ValueError, "Invalid batch status"):
            self.service.update_payment_batch(
                self.session, self.create_batch(), {"batch_status": "Paid"}
            )

    def test_draft_deletion_removes_items_and_rejects_non_draft(self):
        batch_id = self.create_batch()
        self.add_item(batch_id, "DELETE-1", 100)
        self.assertTrue(self.service.delete_payment_batch(self.session, batch_id))
        self.assertIsNone(self.service.get_payment_batch(batch_id))
        imported = self.create_batch()
        self.service.update_payment_batch(
            self.session, imported, {"batch_status": "Imported"}
        )
        with self.assertRaisesRegex(ValueError, "Draft"):
            self.service.delete_payment_batch(self.session, imported)

    def test_duplicate_documents_are_global_and_case_insensitive(self):
        first, second = self.create_batch(), self.create_batch()
        item_id = self.add_item(first, "MP-100", 250)
        duplicate = self.service.find_duplicate_document("mp-100")
        self.assertEqual(duplicate["payment_item_id"], item_id)
        with self.assertRaisesRegex(ValueError, "already been imported"):
            self.add_item(second, "mp-100", 250)
        self.assertEqual(self.service.list_payment_items(second), [])

    def test_import_validation_and_invalid_job_reference(self):
        batch_id = self.create_batch()
        invalid_items = (
            {"document_number": "", "amount_received_cents": 1},
            {"document_number": "A", "amount_received_cents": -1},
            {"document_number": "B", "amount_received_cents": 1.5},
            {"document_number": "C", "amount_received_cents": 1, "job_id": 99999},
            {"document_number": "D", "amount_received_cents": 1, "match_status": "Paid"},
        )
        for item in invalid_items:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.service.add_payment_item(self.session, batch_id, item)
        with self.assertRaisesRegex(ValueError, "payment_date"):
            self.service.create_payment_batch(
                self.session, {"payment_amount_cents": 100}
            )

    def test_batch_totals_use_integer_cents(self):
        batch_id = self.create_batch(1000)
        self.add_item(batch_id, "MATCHED", 625)
        self.add_item(batch_id, "MISSING", 300)
        self.create_job("MATCHED")
        self.service.match_payment_items(self.session, batch_id)
        self.assertEqual(self.service.calculate_batch_totals(batch_id), {
            "payment_amount_cents": 1000,
            "imported_total_cents": 925,
            "difference_cents": 75,
            "matched_total_cents": 625,
            "unmatched_total_cents": 300,
            "matched_count": 1,
            "unmatched_count": 1,
        })

    def test_matching_success_and_failure(self):
        batch_id = self.create_batch()
        job_id = self.create_job("JOB-MATCH")
        self.add_item(batch_id, "job-match", 100)
        self.add_item(batch_id, "NOT-FOUND", 200)
        self.assertEqual(self.service.match_payment_items(self.session, batch_id),
                         {"matched_count": 1, "unmatched_count": 1})
        matched, missing = self.service.list_payment_items(batch_id)
        self.assertEqual((matched["job_id"], matched["match_status"], matched["match_method"]),
                         (job_id, "Matched", "External Job ID"))
        self.assertEqual((missing["job_id"], missing["match_status"]),
                         (None, "Missing Job"))

    def test_active_primary_technician_lookup(self):
        job_id = self.create_job("TECH-JOB")
        with self.auth.connection() as connection:
            tech_id = int(connection.execute(
                "INSERT INTO Techs (tech_code, first_name, last_name, status, created_by) "
                "VALUES ('PAY1', 'Pat', 'Primary', 'Active', ?)",
                (self.session.user_id,),
            ).lastrowid)
            connection.execute(
                "INSERT INTO JobAssignments (job_id, tech_id, assignment_role, "
                "assignment_status, assigned_by) VALUES (?, ?, 'Primary', 'Assigned', ?)",
                (job_id, tech_id, self.session.user_id),
            )
        self.assertEqual(self.service.get_primary_technician(job_id), {
            "tech_id": tech_id, "first_name": "Pat", "last_name": "Primary"
        })
        with self.auth.connection() as connection:
            connection.execute("UPDATE Techs SET status = 'Inactive' WHERE tech_id = ?", (tech_id,))
        self.assertIsNone(self.service.get_primary_technician(job_id))

    def test_matching_transaction_rolls_back_all_rows_on_failure(self):
        batch_id = self.create_batch()
        self.create_job("ROLLBACK-1")
        self.add_item(batch_id, "ROLLBACK-1", 100)
        self.add_item(batch_id, "ROLLBACK-2", 200)
        with self.auth.connection() as connection:
            connection.execute(
                "CREATE TRIGGER fail_match_audit BEFORE INSERT ON AuditLog "
                "WHEN NEW.action = 'payment_item_unmatched' "
                "BEGIN SELECT RAISE(ABORT, 'forced audit failure'); END"
            )
        with self.assertRaisesRegex(Exception, "forced audit failure"):
            self.service.match_payment_items(self.session, batch_id)
        items = self.service.list_payment_items(batch_id)
        self.assertEqual([(row["job_id"], row["match_status"]) for row in items],
                         [(None, "Unmatched"), (None, "Unmatched")])


if __name__ == "__main__":
    unittest.main()
