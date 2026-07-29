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
        for status in ("Needs Review",):
            updated = self.service.update_payment_batch(
                self.session, batch_id, {"batch_status": status}
            )
            self.assertEqual(updated["batch_status"], status)
        with self.assertRaisesRegex(ValueError, "empty"):
            self.service.update_payment_batch(
                self.session, batch_id, {"batch_status": "Reconciled"}
            )
        # Cancellation remains permitted from any status except Closed.
        self.assertEqual(self.service.update_payment_batch(
            self.session, batch_id, {"batch_status": "Cancelled"}
        )["batch_status"], "Cancelled")
        with self.assertRaisesRegex(ValueError, "do not allow"):
            self.service.update_payment_batch(
                self.session, batch_id, {"batch_status": "Approved"}
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
            "missing_job_count": 1,
            "ambiguous_count": 0,
            "amount_review_count": 0,
            "excluded_count": 0,
            "excluded_total_cents": 0,
            "item_count": 2,
            "resolved_count": 1,
            "exception_count": 1,
        })

    def test_batch_totals_distinguish_resolved_and_exception_statuses(self):
        amounts = {
            "Matched": 101,
            "Excluded": 202,
            "Missing Job": 303,
            "Ambiguous": 404,
            "Amount Review": 505,
            "Unmatched": 606,
        }
        batch_id = self.create_batch(sum(amounts.values()))
        for index, (status, amount) in enumerate(amounts.items()):
            item_id = self.add_item(batch_id, f"TOTAL-{index}", amount)
            self.service.update_payment_item(
                self.session, item_id, {"match_status": status})

        self.assertEqual(self.service.calculate_batch_totals(batch_id), {
            "payment_amount_cents": 2121,
            "imported_total_cents": 2121,
            "difference_cents": 0,
            "matched_total_cents": 101,
            "unmatched_total_cents": 1818,
            "matched_count": 1,
            "unmatched_count": 4,
            "missing_job_count": 1,
            "ambiguous_count": 1,
            "amount_review_count": 1,
            "excluded_count": 1,
            "excluded_total_cents": 202,
            "item_count": 6,
            "resolved_count": 2,
            "exception_count": 4,
        })

    def test_matched_and_excluded_batch_has_no_exceptions_and_reconciles(self):
        batch_id = self.create_batch(100)
        for document, amount, status in (
            ("RESOLVED-MATCH", 60, "Matched"),
            ("RESOLVED-EXCLUDED", 40, "Excluded"),
        ):
            item_id = self.add_item(batch_id, document, amount)
            self.service.update_payment_item(
                self.session, item_id, {"match_status": status})
        for status in ("Imported", "Needs Review"):
            self.service.update_payment_batch(
                self.session, batch_id, {"batch_status": status})

        totals = self.service.calculate_batch_totals(batch_id)
        self.assertEqual(totals["exception_count"], 0)
        self.assertEqual(totals["unmatched_count"], 0)
        self.assertEqual(totals["unmatched_total_cents"], 0)
        self.assertEqual(totals["excluded_total_cents"], 40)
        self.assertEqual(totals["imported_total_cents"], 100)
        self.assertEqual(self.service.update_payment_batch(
            self.session, batch_id, {"batch_status": "Reconciled"})["batch_status"],
            "Reconciled")

    def test_matching_success_and_failure(self):
        batch_id = self.create_batch()
        job_id = self.create_job("JOB-MATCH")
        self.add_item(batch_id, "job-match", 100)
        self.add_item(batch_id, "NOT-FOUND", 200)
        self.assertEqual(self.service.match_payment_items(self.session, batch_id), {
            "matched_count": 1, "missing_job_count": 1,
            "ambiguous_count": 0, "unmatched_count": 1,
        })
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

    def test_primary_technician_result_distinguishes_all_assignment_cases(self):
        job_id = self.create_job("TECH-CASES")
        self.assertEqual(self.service.get_primary_technician_result(job_id), {
            "status": "Missing", "technician": None, "candidate_count": 0,
        })
        with self.auth.connection() as connection:
            # Exercise the service's defensive ambiguity handling even though the
            # current schema normally prevents two active primary rows.
            connection.execute("DROP INDEX ux_JobAssignments_active_primary")
            tech_ids = []
            for code, first, status in (("TC1", "One", "Active"),
                                        ("TC2", "Two", "Active"),
                                        ("TC3", "Inactive", "Inactive"),
                                        ("TC4", "Historical", "Active")):
                tech_ids.append(int(connection.execute(
                    "INSERT INTO Techs (tech_code, first_name, last_name, status, created_by) "
                    "VALUES (?, ?, 'Tech', ?, ?)",
                    (code, first, status, self.session.user_id),
                ).lastrowid))
            connection.execute(
                "INSERT INTO JobAssignments (job_id, tech_id, assignment_role, "
                "assignment_status, assigned_by) VALUES (?, ?, 'Primary', 'Assigned', ?)",
                (job_id, tech_ids[0], self.session.user_id),
            )
            connection.execute(
                "INSERT INTO JobAssignments (job_id, tech_id, assignment_role, "
                "assignment_status, assigned_by) VALUES (?, ?, 'Primary', 'Assigned', ?)",
                (job_id, tech_ids[2], self.session.user_id),
            )
            connection.execute(
                "INSERT INTO JobAssignments (job_id, tech_id, assignment_role, "
                "assignment_status, unassigned_at, assigned_by) "
                "VALUES (?, ?, 'Primary', 'Unassigned', '2026-01-01', ?)",
                (job_id, tech_ids[3], self.session.user_id),
            )
        result = self.service.get_primary_technician_result(job_id)
        self.assertEqual((result["status"], result["candidate_count"]), ("Found", 1))
        self.assertEqual(result["technician"]["tech_id"], tech_ids[0])
        with self.auth.connection() as connection:
            connection.execute(
                "INSERT INTO JobAssignments (job_id, tech_id, assignment_role, "
                "assignment_status, assigned_by) VALUES (?, ?, 'Primary', 'Assigned', ?)",
                (job_id, tech_ids[1], self.session.user_id),
            )
        result = self.service.get_primary_technician_result(job_id)
        self.assertEqual(result, {
            "status": "Ambiguous", "technician": None, "candidate_count": 2,
        })
        self.assertIsNone(self.service.get_primary_technician(job_id))

    def test_matching_detects_ambiguous_external_job_ids(self):
        batch_id = self.create_batch(100)
        first = self.create_job("DUP-JOB")
        # Rebuild this isolated test's Jobs table without its UNIQUE constraint
        # to represent legacy/corrupt data the matching layer must not hide.
        with self.auth.connection() as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("ALTER TABLE Jobs RENAME TO Jobs_unique")
            connection.execute("CREATE TABLE Jobs AS SELECT * FROM Jobs_unique")
            second = int(connection.execute(
                "INSERT INTO Jobs (job_id, external_job_id, created_by) VALUES (?, ?, ?)",
                (first + 1, "dup-job", self.session.user_id),
            ).lastrowid)
        self.add_item(batch_id, "Dup-Job", 100)
        summary = self.service.match_payment_items(self.session, batch_id)
        self.assertEqual(summary, {"matched_count": 0, "missing_job_count": 0,
                                   "ambiguous_count": 1, "unmatched_count": 1})
        item = self.service.list_payment_items(batch_id)[0]
        self.assertIsNone(item["job_id"])
        self.assertNotIn(item["job_id"], (first, second))
        self.assertEqual((item["match_status"], item["match_method"]),
                         ("Ambiguous", "External Job ID"))
        self.assertIn("Multiple Jobs", item["match_notes"])
        with self.auth.connection() as connection:
            actions = [row[0] for row in connection.execute(
                "SELECT action FROM AuditLog WHERE action = 'payment_item_match_ambiguous'")]
        self.assertEqual(actions, ["payment_item_match_ambiguous"])

    def test_matching_allowed_statuses_and_rejected_statuses(self):
        for status in ("Draft", "Imported", "Needs Review"):
            with self.subTest(status=status):
                batch_id = self.create_batch(1)
                self.add_item(batch_id, f"ALLOW-{status}", 1)
                if status != "Draft":
                    self.service.update_payment_batch(
                        self.session, batch_id, {"batch_status": "Imported"})
                if status == "Needs Review":
                    self.service.update_payment_batch(
                        self.session, batch_id, {"batch_status": "Needs Review"})
                self.service.match_payment_items(self.session, batch_id)

        for status in ("Reconciled", "Approved", "Closed", "Cancelled"):
            with self.subTest(status=status):
                batch_id = self.create_batch(1)
                document = f"DENY-{status}"
                self.add_item(batch_id, document, 1)
                self.create_job(document)
                self.service.update_payment_batch(
                    self.session, batch_id, {"batch_status": "Imported"})
                self.service.update_payment_batch(
                    self.session, batch_id, {"batch_status": "Needs Review"})
                if status == "Cancelled":
                    self.service.update_payment_batch(
                        self.session, batch_id, {"batch_status": "Cancelled"})
                else:
                    self.service.match_payment_items(self.session, batch_id)
                    self.service.update_payment_batch(
                        self.session, batch_id, {"batch_status": "Reconciled"})
                    if status in {"Approved", "Closed"}:
                        self.service.update_payment_batch(
                            self.session, batch_id, {"batch_status": "Approved"})
                    if status == "Closed":
                        self.service.update_payment_batch(
                            self.session, batch_id, {"batch_status": "Closed"})
                with self.assertRaisesRegex(ValueError, "cannot be matched"):
                    self.service.match_payment_items(self.session, batch_id)

    def test_imported_items_cannot_supply_matching_fields(self):
        for field, value in (("job_id", 1), ("match_status", "Matched"),
                             ("match_method", "Manual"), ("match_notes", "bypass")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "matching fields"):
                self.service.add_payment_item(
                    self.session, self.create_batch(),
                    {"document_number": f"BLOCK-{field}", "amount_received_cents": 1,
                     field: value},
                )
        item_id = self.add_item(self.create_batch(), "CLEAN-IMPORT", 1)
        with self.auth.connection() as connection:
            item = connection.execute(
                "SELECT job_id, match_status, match_method, match_notes "
                "FROM MatterportPaymentItems WHERE payment_item_id = ?", (item_id,)
            ).fetchone()
        self.assertEqual(tuple(item), (None, "Unmatched", None, None))

    def test_reconciliation_prerequisites_and_excluded_totals(self):
        def needs_review(amount=100):
            batch = self.create_batch(amount)
            self.service.update_payment_batch(
                self.session, batch, {"batch_status": "Imported"})
            self.service.update_payment_batch(
                self.session, batch, {"batch_status": "Needs Review"})
            return batch

        with self.assertRaisesRegex(ValueError, "empty"):
            self.service.update_payment_batch(
                self.session, needs_review(), {"batch_status": "Reconciled"})

        difference = self.create_batch(100)
        difference_item = self.add_item(difference, "DIFFERENCE", 99)
        self.service.update_payment_item(
            self.session, difference_item, {"match_status": "Matched"})
        for status in ("Imported", "Needs Review"):
            self.service.update_payment_batch(
                self.session, difference, {"batch_status": status})
        with self.assertRaisesRegex(ValueError, "imported total"):
            self.service.update_payment_batch(
                self.session, difference, {"batch_status": "Reconciled"})

        for match_status, message in (("Missing Job", "Missing Job"),
                                      ("Ambiguous", "Ambiguous"),
                                      ("Unmatched", "non-excluded")):
            with self.subTest(match_status=match_status):
                batch = self.create_batch(100)
                item = self.add_item(batch, f"STATUS-{match_status}", 100)
                self.service.update_payment_item(
                    self.session, item, {"match_status": match_status})
                for status in ("Imported", "Needs Review"):
                    self.service.update_payment_batch(
                        self.session, batch, {"batch_status": status})
                with self.assertRaisesRegex(ValueError, message):
                    self.service.update_payment_batch(
                        self.session, batch, {"batch_status": "Reconciled"})

        valid = self.create_batch(100)
        self.create_job("VALID-RECON")
        self.add_item(valid, "VALID-RECON", 100)
        for status in ("Imported", "Needs Review"):
            self.service.update_payment_batch(self.session, valid, {"batch_status": status})
        self.service.match_payment_items(self.session, valid)
        self.assertEqual(self.service.update_payment_batch(
            self.session, valid, {"batch_status": "Reconciled"})["batch_status"],
            "Reconciled")

        excluded = self.create_batch(100)
        matched_item = self.add_item(excluded, "MATCH-PART", 60)
        excluded_item = self.add_item(excluded, "EXCLUDED-PART", 40)
        self.service.update_payment_item(
            self.session, matched_item, {"match_status": "Matched"})
        self.service.update_payment_item(
            self.session, excluded_item, {"match_status": "Excluded"})
        for status in ("Imported", "Needs Review", "Reconciled"):
            updated = self.service.update_payment_batch(
                self.session, excluded, {"batch_status": status})
        self.assertEqual(updated["batch_status"], "Reconciled")
        self.assertEqual(self.service.calculate_batch_totals(excluded)["imported_total_cents"], 100)

    def test_status_mutability_policy_covers_batch_and_item_paths(self):
        batch = self.create_batch(100)
        item = self.add_item(batch, "MUTATE", 100)
        self.service.update_payment_item(
            self.session, item, {"description_raw": "draft edit"})
        self.service.update_payment_batch(
            self.session, batch, {"payer_name": "payer", "batch_status": "Imported"})
        self.service.update_payment_batch(self.session, batch, {"notes": "metadata ok"})
        for field, value in (("payment_date", "2026-08-01"),
                             ("payment_amount_cents", 200)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                self.service.update_payment_batch(self.session, batch, {field: value})
        for operation in (
            lambda: self.service.add_payment_item(
                self.session, batch, {"document_number": "NO-ADD", "amount_received_cents": 1}),
            lambda: self.service.update_payment_item(
                self.session, item, {"description_raw": "no edit"}),
            lambda: self.service.delete_payment_item(self.session, item),
            lambda: self.service.delete_payment_batch(self.session, batch),
        ):
            with self.assertRaises(ValueError):
                operation()

        self.service.update_payment_batch(
            self.session, batch, {"batch_status": "Needs Review"})
        self.service.update_payment_batch(self.session, batch, {"notes": "review note"})
        with self.assertRaisesRegex(ValueError, "payer_name"):
            self.service.update_payment_batch(self.session, batch, {"payer_name": "new"})

        # Reconcile, approve, and verify their notes-only behavior.
        with self.auth.connection() as connection:
            connection.execute(
                "UPDATE MatterportPaymentItems SET match_status = 'Matched' "
                "WHERE payment_item_id = ?", (item,))
        self.service.update_payment_batch(
            self.session, batch, {"batch_status": "Reconciled"})
        self.service.update_payment_batch(self.session, batch, {"notes": "reconciled note"})
        self.service.update_payment_batch(
            self.session, batch, {"batch_status": "Approved"})
        self.service.update_payment_batch(self.session, batch, {"notes": "approved note"})
        self.service.update_payment_batch(
            self.session, batch, {"batch_status": "Closed"})
        with self.assertRaisesRegex(ValueError, "do not allow"):
            self.service.update_payment_batch(self.session, batch, {"notes": "closed"})

        cancelled = self.create_batch()
        self.service.update_payment_batch(
            self.session, cancelled, {"batch_status": "Cancelled"})
        with self.assertRaisesRegex(ValueError, "do not allow"):
            self.service.update_payment_batch(self.session, cancelled, {"notes": "cancelled"})

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
        self.assertEqual(self.service.get_payment_batch(batch_id)["batch_status"], "Draft")
        with self.auth.connection() as connection:
            matching_audits = connection.execute(
                "SELECT COUNT(*) FROM AuditLog WHERE action IN "
                "('payment_item_matched', 'payment_item_unmatched', "
                "'payment_item_match_ambiguous')"
            ).fetchone()[0]
        self.assertEqual(matching_audits, 0)


if __name__ == "__main__":
    unittest.main()
