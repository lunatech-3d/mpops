"""Integration coverage for the idempotent payment and payout migration."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.security.auth import AuthService


MIGRATION = "011_add_payment_payout_schema.py"
RESOLUTION_MIGRATION = "012_payment_amount_resolution.py"
TABLES = {
    "MatterportPaymentBatches", "MatterportPaymentItems", "TechnicianJobEarnings",
    "TechnicianPaymentRuns", "TechnicianPayments", "TechnicianPaymentEarnings",
}
INDEXES = {
    "idx_MatterportPaymentItems_batch", "idx_MatterportPaymentItems_document",
    "idx_MatterportPaymentItems_job", "ux_MatterportPaymentItems_document",
    "idx_TechnicianJobEarnings_payment_item", "idx_TechnicianJobEarnings_job",
    "idx_TechnicianJobEarnings_tech", "idx_TechnicianPayments_run",
    "idx_TechnicianPayments_tech", "idx_TechnicianPaymentEarnings_payment",
    "idx_TechnicianPaymentEarnings_earning",
}


class PaymentSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "mpops.db"
        self.settings = Settings(self.path, password_iterations=100_000)

    def tearDown(self):
        self.tempdir.cleanup()

    def assert_complete(self):
        with sqlite3.connect(self.path) as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            indexes = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
            columns = {row[1] for row in connection.execute("PRAGMA table_info(Techs)")}
            self.assertTrue(TABLES <= tables)
            self.assertTrue(INDEXES <= indexes)
            self.assertIn("default_pay_percentage", columns)
            payment_columns = {row[1] for row in connection.execute(
                "PRAGMA table_info(MatterportPaymentItems)")}
            self.assertTrue({"expected_job_amount_cents", "resolved_amount_cents",
                             "amount_resolution", "amount_resolution_notes",
                             "amount_resolved_at", "amount_resolved_by"} <= payment_columns)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM SchemaMigrations WHERE name=?", (MIGRATION,)
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM SchemaMigrations WHERE name=?", (RESOLUTION_MIGRATION,)
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_fresh_database_and_repeated_initialization(self):
        AuthService(self.settings)
        self.assert_complete()
        AuthService(self.settings)
        self.assert_complete()

    def test_preexisting_manual_schema_and_data_are_preserved(self):
        AuthService(self.settings)
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM SchemaMigrations WHERE name=?", (MIGRATION,))
            connection.execute(
                "INSERT INTO MatterportPaymentBatches "
                "(payment_date,payment_amount_cents,notes) VALUES ('2026-07-01',12345,'preserve me')"
            )
        AuthService(self.settings)
        AuthService(self.settings)
        self.assert_complete()
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT payment_amount_cents,notes FROM MatterportPaymentBatches"
            ).fetchone(), (12345, "preserve me"))

    def test_partial_schema_is_completed_without_replacing_existing_table(self):
        AuthService(self.settings)
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("DELETE FROM SchemaMigrations WHERE name=?", (MIGRATION,))
            connection.execute(
                "INSERT INTO MatterportPaymentBatches "
                "(payment_date,payment_amount_cents,notes) VALUES ('2026-07-02',1,'partial sentinel')"
            )
            for table in reversed(tuple(TABLES - {"MatterportPaymentBatches"})):
                connection.execute(f"DROP TABLE {table}")
            connection.execute("DROP INDEX IF EXISTS idx_MatterportPaymentItems_batch")
        AuthService(self.settings)
        self.assert_complete()
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT notes FROM MatterportPaymentBatches"
            ).fetchone()[0], "partial sentinel")


if __name__ == "__main__":
    unittest.main()
