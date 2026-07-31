"""Integration tests for the revenue-allocation schema migration."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.security.auth import AuthService


MIGRATION = "018_revenue_allocation_schema.py"
TABLES = {"MarketRevenueShareRules", "CompanyRevenueAllocations"}
INDEXES = {
    "idx_market_revenue_rules_market_dates", "idx_company_allocations_payment_item",
    "idx_company_allocations_job", "idx_company_allocations_market",
    "idx_company_allocations_status", "ux_current_company_allocation",
}


class RevenueAllocationSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "mpops.db"
        self.settings = Settings(self.path, password_iterations=100_000)
        self.auth = AuthService(self.settings)
        with self.auth.connection() as connection:
            self.user_id = connection.execute(
                "INSERT INTO Users(username,password_hash,is_active) VALUES('owner','hash',1)"
            ).lastrowid
            self.market_id = connection.execute(
                "INSERT INTO Markets(market_name,created_by) VALUES('Test Market',?)",
                (self.user_id,),
            ).lastrowid
            self.job_id = connection.execute(
                "INSERT INTO Jobs(external_job_id,created_by) VALUES('JOB-1',?)", (self.user_id,)
            ).lastrowid
            batch_id = connection.execute(
                "INSERT INTO MatterportPaymentBatches(payment_date,payment_amount_cents,created_by) "
                "VALUES('2026-07-31',10000,?)", (self.user_id,)
            ).lastrowid
            self.payment_item_id = connection.execute(
                "INSERT INTO MatterportPaymentItems(payment_batch_id,document_number,"
                "amount_received_cents,job_id) VALUES(?,?,10000,?)",
                (batch_id, "INV-1", self.job_id),
            ).lastrowid

    def tearDown(self):
        self.tempdir.cleanup()

    def allocation(self, **changes):
        values = {
            "payment_item_id": self.payment_item_id, "job_id": self.job_id,
            "market_id": self.market_id, "gross_revenue_cents": 10000,
            "technician_share_basis_points": 0, "technician_amount_cents": 0,
            "lunatech_east_share_basis_points": 0, "lunatech_east_amount_cents": 0,
            "lunatech_share_basis_points": 10000, "lunatech_amount_cents": 10000,
            "allocation_status": "Calculated",
        }
        values.update(changes)
        columns = ",".join(values)
        placeholders = ",".join("?" for _ in values)
        return f"INSERT INTO CompanyRevenueAllocations({columns}) VALUES({placeholders})", tuple(values.values())

    def test_fresh_schema_foreign_keys_indexes_and_repeatability(self):
        with self.auth.connection() as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            indexes = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
            columns = {row[1] for row in connection.execute(
                "PRAGMA table_info(TechnicianCompensationRules)")}
            self.assertTrue(TABLES <= tables)
            self.assertTrue(INDEXES <= indexes)
            self.assertTrue({"effective_from", "effective_to"} <= columns)
            market_parents = {row[2] for row in connection.execute(
                "PRAGMA foreign_key_list(MarketRevenueShareRules)")}
            allocation_parents = {row[2] for row in connection.execute(
                "PRAGMA foreign_key_list(CompanyRevenueAllocations)")}
            self.assertEqual(market_parents, {"Markets", "Users"})
            self.assertEqual(allocation_parents, {
                "MatterportPaymentItems", "Jobs", "Markets", "TechnicianJobEarnings",
                "MarketRevenueShareRules", "Users",
            })
        AuthService(self.settings)
        with self.auth.connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM SchemaMigrations WHERE name=?", (MIGRATION,)
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_constraints_and_supported_allocations(self):
        with self.auth.connection() as connection:
            for share in (-1, 10001):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO MarketRevenueShareRules(market_id,recipient_code,"
                        "share_basis_points,effective_from) VALUES(?,'LUNATECH_EAST',?,'2026-01-01')",
                        (self.market_id, share),
                    )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO MarketRevenueShareRules(market_id,recipient_code,share_basis_points,"
                    "effective_from,effective_to) VALUES(?,'LUNATECH_EAST',0,'2026-02-01','2026-01-01')",
                    (self.market_id,),
                )
            connection.execute(
                "INSERT INTO TechnicianCompensationRules(scope_type,rule_type,rule_value,"
                "effective_from,effective_to) VALUES('System','Percentage',7000,'2026-02-01',NULL)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO TechnicianCompensationRules(scope_type,rule_type,rule_value,"
                    "effective_from,effective_to) VALUES('Market','Percentage',1000,'2026-02-01','2026-01-01')"
                )

            invalid = (
                {"lunatech_share_basis_points": 9999},
                {"lunatech_amount_cents": 9999},
                {"technician_amount_cents": -1, "lunatech_amount_cents": 10001},
                {"allocation_status": "Exception"},
            )
            for changes in invalid:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(*self.allocation(**changes))

            connection.execute(*self.allocation())  # zero-dollar tech, zero East, 100% LunaTech
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(*self.allocation(allocation_status="Approved"))
            connection.execute(*self.allocation(allocation_status="Superseded"))
            connection.execute(*self.allocation(allocation_status="Superseded"))

            second_batch = connection.execute(
                "INSERT INTO MatterportPaymentBatches(payment_date,payment_amount_cents,created_by) "
                "VALUES('2026-08-01',20000,?)", (self.user_id,)
            ).lastrowid
            for number, shares in enumerate(((0, 1000, 9000), (7000, 0, 3000)), start=2):
                payment_item = connection.execute(
                    "INSERT INTO MatterportPaymentItems(payment_batch_id,document_number,"
                    "amount_received_cents,job_id) VALUES(?,?,10000,?)",
                    (second_batch, f"INV-{number}", self.job_id),
                ).lastrowid
                tech, east, luna = shares
                connection.execute(*self.allocation(
                    payment_item_id=payment_item,
                    technician_share_basis_points=tech, technician_amount_cents=tech,
                    lunatech_east_share_basis_points=east, lunatech_east_amount_cents=east,
                    lunatech_share_basis_points=luna, lunatech_amount_cents=luna,
                ))

    def test_populated_prototype_tables_are_rebuilt_without_data_loss(self):
        with self.auth.connection() as connection:
            connection.execute("DELETE FROM SchemaMigrations WHERE name=?", (MIGRATION,))
            connection.execute("DROP TABLE CompanyRevenueAllocations")
            connection.execute("DROP TABLE MarketRevenueShareRules")
            connection.execute("""CREATE TABLE MarketRevenueShareRules (
                market_revenue_share_rule_id INTEGER PRIMARY KEY, market_id INTEGER,
                recipient_code TEXT, share_basis_points INTEGER, effective_from TEXT,
                effective_to TEXT, is_active INTEGER, notes TEXT, created_at TEXT,
                created_by INTEGER, updated_at TEXT, updated_by INTEGER)""")
            connection.execute("""CREATE TABLE CompanyRevenueAllocations (
                company_revenue_allocation_id INTEGER PRIMARY KEY, payment_item_id INTEGER,
                job_id INTEGER, market_id INTEGER, gross_revenue_cents INTEGER,
                technician_earning_id INTEGER, technician_share_basis_points INTEGER,
                technician_amount_cents INTEGER, lunatech_east_share_basis_points INTEGER,
                lunatech_east_amount_cents INTEGER, lunatech_share_basis_points INTEGER,
                lunatech_amount_cents INTEGER, market_revenue_share_rule_id INTEGER,
                allocation_status TEXT, calculation_details_json TEXT, created_at TEXT,
                created_by INTEGER, approved_at TEXT, approved_by INTEGER,
                superseded_at TEXT, superseded_by INTEGER, superseded_reason TEXT)""")
            connection.execute(
                "INSERT INTO MarketRevenueShareRules VALUES(41,?,'LUNATECH_EAST',1000,"
                "'2026-01-01',NULL,1,'preserve rule','2026-01-01',?,NULL,NULL)",
                (self.market_id, self.user_id),
            )
            connection.execute(
                "INSERT INTO CompanyRevenueAllocations VALUES(51,?,?,?,10000,NULL,0,0,1000,1000,"
                "9000,9000,41,'Calculated','{\"prototype\":true}','2026-01-02',?,NULL,NULL,NULL,NULL,NULL)",
                (self.payment_item_id, self.job_id, self.market_id, self.user_id),
            )
        AuthService(self.settings)
        with self.auth.connection() as connection:
            self.assertEqual(tuple(connection.execute(
                "SELECT market_revenue_share_rule_id,notes FROM MarketRevenueShareRules"
            ).fetchone()), (41, "preserve rule"))
            self.assertEqual(tuple(connection.execute(
                "SELECT company_revenue_allocation_id,calculation_details_json "
                "FROM CompanyRevenueAllocations"
            ).fetchone()), (51, '{"prototype":true}'))
            self.assertTrue(INDEXES <= {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")})
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_incompatible_populated_prototype_fails_and_rolls_back(self):
        with self.auth.connection() as connection:
            connection.execute("DELETE FROM SchemaMigrations WHERE name=?", (MIGRATION,))
            connection.execute("DROP TABLE CompanyRevenueAllocations")
            connection.execute("CREATE TABLE CompanyRevenueAllocations(id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO CompanyRevenueAllocations VALUES(1)")
        with self.assertRaisesRegex(RuntimeError, "missing required column"):
            AuthService(self.settings)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT id FROM CompanyRevenueAllocations"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM SchemaMigrations WHERE name=?", (MIGRATION,)
            ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
