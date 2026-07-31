import importlib
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import PROJECT_ROOT, Settings
from app.security.auth import AuthService, Session
from app.security.passwords import hash_password, verify_password
from app.security.user_manager import AuthorizationError, UserManager
from app.services.jobs_service import JobsService
from app.main import requires_initial_admin
from app.resources import resource_path
from app.ui.dialog_utils import close_modal, prepare_modal_dialog, validate_confirmation, validate_identity
from app.ui.main_window import MainWindow


class ApplicationServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.tmp.name) / "mpops.db", password_iterations=100_000)
        self.auth = AuthService(self.settings)
        self.users = UserManager(self.auth)
        self.admin_id = self.users.create_user("Admin", "admin-password-123", "admin")
        self.admin = self.auth.authenticate("admin", "admin-password-123")

    def tearDown(self):
        Session.clear_environment()
        self.tmp.cleanup()

    def test_packaged_logo_resource_exists(self):
        self.assertTrue(resource_path("lunatech_logo.png").is_file())

    def test_application_modules_import(self):
        for name in ("app.main", "app.ui.styles", "app.ui.dashboard", "app.ui.main_window", "app.ui.user_manager_window",
                     "app.ui.initial_admin", "app.ui.user_form", "app.ui.password_reset",
                     "app.ui.technician_manager", "app.ui.technician_form", "app.ui.address_form"):
            self.assertIsNotNone(importlib.import_module(name))

    def test_job_activity_uses_scheduled_local_dates_and_distinct_jobs(self):
        jobs = JobsService(self.auth)
        rows = (
            ("TODAY-COMPLETE", "Completed", "2026-07-31T09:00:00"),
            ("TODAY-SCHEDULED", "Scheduled", "2026-07-31T17:00:00"),
            ("TODAY-CANCELLED", "Cancelled", "2026-07-31T12:00:00"),
            ("MONDAY", "Assigned", "2026-07-27T08:00:00"),
            ("SUNDAY", "Scheduled", "2026-08-02T23:59:00"),
            ("MONTH-ONLY", "On Hold", "2026-07-05T10:00:00"),
            ("OUTSIDE", "Completed", "2026-06-30T10:00:00"),
        )
        with self.auth.connection() as connection:
            ids = {}
            for external_id, status, scheduled in rows:
                ids[external_id] = connection.execute(
                    "INSERT INTO Jobs(external_job_id, job_status, scheduled_start_at, created_by) "
                    "VALUES (?, ?, ?, ?)",
                    (external_id, status, scheduled, self.admin_id),
                ).lastrowid
            connection.executemany(
                "INSERT INTO JobFinancials(job_id, ct_rate) VALUES (?, ?)",
                ((ids["TODAY-COMPLETE"], 10),
                 (ids["TODAY-COMPLETE"], 20)),
            )

        self.assertEqual(
            jobs.get_job_activity_counts(date(2026, 7, 31)),
            {"today": 2, "week": 4, "month": 4},
        )

    def test_logout_clears_environment(self):
        self.admin.apply_to_environment()
        Session.clear_environment()
        self.assertTrue(all(key not in os.environ for key in ("MPOPS_USER_ID", "MPOPS_USERNAME", "MPOPS_ROLE")))

    def test_modal_lifecycle_skips_hidden_parent_and_waits_before_grab(self):
        calls = []
        parent = MagicMock()
        parent.winfo_viewable.return_value = False
        dialog = MagicMock()
        dialog.winfo_reqwidth.return_value = 300
        dialog.winfo_reqheight.return_value = 200
        dialog.winfo_screenwidth.return_value = 1200
        dialog.winfo_screenheight.return_value = 800
        dialog.wait_visibility.side_effect = lambda: calls.append("visible")
        dialog.grab_set.side_effect = lambda: calls.append("grab")

        prepare_modal_dialog(dialog, parent)

        dialog.transient.assert_not_called()
        self.assertLess(calls.index("visible"), calls.index("grab"))
        dialog.deiconify.assert_called_once_with()
        dialog.lift.assert_called_once_with()
        dialog.focus_force.assert_called_once_with()
        dialog.attributes.assert_any_call("-topmost", True)
        clear_topmost = dialog.after_idle.call_args.args[0]
        clear_topmost()
        dialog.attributes.assert_any_call("-topmost", False)

    def test_close_modal_releases_grab_before_destroying(self):
        calls = []
        dialog = MagicMock()
        dialog.grab_release.side_effect = lambda: calls.append("release")
        dialog.destroy.side_effect = lambda: calls.append("destroy")
        close_modal(dialog)
        self.assertEqual(calls, ["release", "destroy"])

    @patch("app.ui.initial_admin.prepare_modal_dialog")
    @patch("app.ui.initial_admin.tk.StringVar", side_effect=lambda: MagicMock())
    @patch("app.ui.initial_admin.tk.Toplevel")
    def test_cancelling_first_run_setup_returns_false(self, toplevel, _string_var, _prepare):
        dialog = toplevel.return_value
        dialog.winfo_exists.return_value = False
        root = MagicMock()
        self.assertFalse(__import__("app.ui.initial_admin", fromlist=["show_initial_admin_dialog"])
                         .show_initial_admin_dialog(root, MagicMock()))

    @patch("app.security.login.prepare_modal_dialog")
    @patch("app.security.login.tk.StringVar", side_effect=lambda: MagicMock())
    @patch("app.security.login.tk.Toplevel")
    def test_cancelling_login_returns_none(self, toplevel, _string_var, _prepare):
        dialog = toplevel.return_value
        dialog.winfo_exists.return_value = False
        root = MagicMock()
        self.assertIsNone(__import__("app.security.login", fromlist=["show_login"])
                          .show_login(root, MagicMock()))

    def test_logout_clears_session_root_and_invokes_login(self):
        window = MainWindow.__new__(MainWindow)
        child, secondary = MagicMock(), MagicMock()
        secondary.winfo_exists.return_value = True
        window.root = MagicMock()
        window.root.winfo_children.return_value = [child]
        window.secondary_windows = [secondary]
        window.on_logout = MagicMock()
        with patch.object(Session, "clear_environment") as clear:
            window.logout()
        clear.assert_called_once_with()
        secondary.destroy.assert_called_once_with()
        child.destroy.assert_called_once_with()
        window.root.withdraw.assert_called_once_with()
        window.on_logout.assert_called_once_with()

    def test_admin_crud_password_and_activation(self):
        uid = self.users.create_user("NewUser", "original-pass-123", "operator", self.admin, "New User")
        with self.auth.connection() as connection:
            self.assertEqual(connection.execute("SELECT created_by FROM Users WHERE id=?", (uid,)).fetchone()[0],
                             self.admin.user_id)
        self.assertEqual(len(self.users.list_users(self.admin, "new user")), 1)
        self.users.update_user(uid, display_name="Changed Name", role="viewer", actor=self.admin)
        self.assertEqual(self.users.get_user(uid, self.admin)["role"], "viewer")
        with self.auth.connection() as connection:
            updated = connection.execute("SELECT updated_at,updated_by FROM Users WHERE id=?", (uid,)).fetchone()
            self.assertRegex(updated[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
            self.assertEqual(updated[1], self.admin.user_id)
        self.users.reset_password(uid, "replacement-123", self.admin)
        self.assertEqual(self.auth.authenticate("newuser", "replacement-123").user_id, uid)
        self.users.set_active(uid, False, self.admin)
        self.assertFalse(self.users.get_user(uid, self.admin)["is_active"])
        self.users.set_active(uid, True, self.admin)
        self.assertTrue(self.users.get_user(uid, self.admin)["is_active"])

    def test_startup_decision_and_initial_administrator(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = AuthService(Settings(Path(directory) / "fresh.db", password_iterations=100_000))
            users = UserManager(auth)
            self.assertTrue(requires_initial_admin(users))
            uid = users.create_user(" FirstAdmin ", "initial-admin-123", "admin", None, " First Admin ")
            self.assertFalse(requires_initial_admin(users))
            row = users.get_user(uid, auth.authenticate("firstadmin", "initial-admin-123"))
            self.assertEqual((row["role"], row["is_active"], row["display_name"]), ("admin", 1, "First Admin"))
            with self.assertRaises(AuthorizationError):
                users.create_user("Second", "second-admin-123", "admin")

    def test_user_form_validation_helpers(self):
        self.assertEqual(validate_identity(" user ", " User Name "), ("user", "User Name"))
        self.assertEqual(validate_confirmation("same", "same"), "same")
        with self.assertRaises(ValueError): validate_identity("", "Name")
        with self.assertRaises(ValueError): validate_confirmation("one", "two")

    def test_created_password_is_not_plaintext_and_inactive_creation(self):
        uid = self.users.create_user("Pending", "pending-pass-123", "viewer", self.admin, "Pending User", False)
        with self.auth.connection() as connection:
            row = connection.execute("SELECT password_hash,is_active FROM Users WHERE id=?", (uid,)).fetchone()
        self.assertNotEqual(row[0], "pending-pass-123")
        self.assertEqual(row[1], 0)

    def test_non_admin_roles_cannot_administer(self):
        for role in ("operator", "viewer"):
            uid = self.users.create_user(role, f"{role}-pass-123", role, self.admin)
            actor = Session(uid, role, role)
            with self.assertRaises(AuthorizationError): self.users.list_users(actor)
            with self.assertRaises(AuthorizationError): self.users.update_user(self.admin_id, display_name="X", role="admin", actor=actor)
            with self.assertRaises(AuthorizationError): self.users.reset_password(self.admin_id, "not-allowed-123", actor)
            with self.assertRaises(AuthorizationError): self.users.set_active(self.admin_id, False, actor)

    def test_cannot_deactivate_self_and_duplicate_is_case_insensitive(self):
        with self.assertRaises(ValueError): self.users.set_active(self.admin_id, False, self.admin)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.users.create_user("aDmIn", "duplicate-pass-123", "viewer", self.admin)

    def test_existing_hash_still_verifies(self):
        encoded = hash_password("existing-pass-123", 100_000)
        self.assertTrue(verify_password("existing-pass-123", encoded))

    def test_only_expected_tables_are_initialized(self):
        with self.auth.connection() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables - {"sqlite_sequence"},
                         {"Users", "AuditLog", "Techs", "TechAddresses", "SchemaMigrations",
                          "Projects", "Jobs", "JobSourceRecords", "JobAssignments", "Markets",
                          "MatterportPaymentBatches", "MatterportPaymentItems", "TechnicianJobEarnings",
                          "TechnicianPaymentRuns", "TechnicianPayments", "TechnicianPaymentEarnings",
                          "TechnicianCompensationRules", "JobFinancials"})
        with self.auth.connection() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(Users)")}
            self.assertEqual(columns, {"id", "username", "password_hash", "display_name", "role",
                "is_active", "last_login_at", "created_at", "created_by", "updated_at", "updated_by"})
            self.assertNotIn("username_key", columns)
            self.assertFalse(connection.execute("PRAGMA foreign_key_check").fetchall())


class MigrationTests(unittest.TestCase):
    def test_migration_preserves_existing_user_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            encoded = hash_password("preserved-pass-123", 100_000)
            connection = sqlite3.connect(path)
            try:
                connection.executescript("""
                    CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, username_key TEXT UNIQUE,
                      password_hash TEXT, role TEXT, is_active INTEGER, created_at TEXT, last_login_at TEXT);
                    CREATE TABLE audit_log (id INTEGER PRIMARY KEY, occurred_at TEXT, actor_user_id INTEGER,
                      subject_user_id INTEGER, action TEXT, details_json TEXT);
                    CREATE TABLE Techs (TechID INTEGER PRIMARY KEY, TechCode TEXT, FirstName TEXT, LastName TEXT,
                      Status TEXT, CreatedAt TEXT, CreatedBy INTEGER,
                      FOREIGN KEY(CreatedBy) REFERENCES Users(UserID));
                    CREATE TABLE TechAddresses (AddressID INTEGER PRIMARY KEY, TechID INTEGER, Address1 TEXT,
                      City TEXT, State TEXT, ZipCode TEXT, IsPrimary INTEGER, CreatedAt TEXT, CreatedBy INTEGER,
                      FOREIGN KEY(TechID) REFERENCES Techs(TechID),
                      FOREIGN KEY(CreatedBy) REFERENCES Users(UserID));
                """)
                connection.execute("INSERT INTO users (username,username_key,password_hash,role,is_active,created_at) VALUES (?,?,?,?,1,?)",
                                   ("Legacy", "legacy", encoded, "admin", "2020-01-01T00:00:00Z"))
                connection.execute("INSERT INTO audit_log VALUES (7,?,?,?,?,?)",
                    ("2020-01-01", 1, 1, "legacy_event", "{}"))
                connection.execute("INSERT INTO Techs VALUES (10,'T-10','Ada','Lovelace','Active','2020',1)")
                connection.execute("INSERT INTO TechAddresses VALUES (20,10,'1 Main','Town','ST','12345',1,'2020',1)")
                connection.commit()
            finally:
                connection.close()
            auth = AuthService(Settings(path, password_iterations=100_000))
            self.assertEqual(auth.authenticate("Legacy", "preserved-pass-123").username, "Legacy")
            with auth.connection() as connection:
                row = connection.execute("SELECT id,password_hash FROM Users").fetchone()
                self.assertEqual((row[0], row[1]), (1, encoded))
                self.assertEqual(connection.execute("SELECT tech_id FROM Techs").fetchone()[0], 10)
                self.assertEqual(connection.execute("SELECT address_id FROM TechAddresses").fetchone()[0], 20)
                self.assertEqual(connection.execute("SELECT action FROM AuditLog WHERE id=7").fetchone()[0], "legacy_event")
                self.assertFalse(connection.execute("PRAGMA foreign_key_check").fetchall())
                applied = connection.execute("SELECT count(*) FROM SchemaMigrations WHERE name=?",
                    ("002_reconcile_legacy.py",)).fetchone()[0]
                self.assertEqual(applied, 1)
            AuthService(Settings(path, password_iterations=100_000))
            with auth.connection() as connection:
                applied = {row[0] for row in connection.execute("SELECT name FROM SchemaMigrations")}
                self.assertEqual(applied, {"002_reconcile_legacy.py", "003_expand_technicians.py",
                                           "004_add_jobs.py", "010_create_markets.sql",
                                           "011_add_payment_payout_schema.py",
                                           "012_payment_amount_resolution.py",
                                           "013_payment_batch_reconciliation.py",
                                           "014_compensation_ledger.py",
                                           "015_add_job_financials.py"})

    def test_job_financial_migration_has_pre_drop_column_fallback(self):
        import importlib.util

        migration_path = PROJECT_ROOT / "database" / "migrations" / "015_add_job_financials.py"
        spec = importlib.util.spec_from_file_location("migration_015_test", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript("""
                CREATE TABLE Sample (
                    id INTEGER PRIMARY KEY,
                    retained TEXT NOT NULL,
                    obsolete TEXT
                );
                INSERT INTO Sample VALUES (7, 'keep me', 'remove me');
            """)
            migration._drop_columns_compat(connection, "Sample", ("obsolete",))
            self.assertEqual(
                [row[1] for row in connection.execute("PRAGMA table_info(Sample)")],
                ["id", "retained"],
            )
            self.assertEqual(connection.execute("SELECT * FROM Sample").fetchone(), (7, "keep me"))
        finally:
            connection.close()

    def test_job_financial_migration_resumes_partially_applied_upgrade(self):
        """An old executescript could commit DDL before failing and being recorded."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.db"
            auth = AuthService(Settings(path, password_iterations=100_000))
            with auth.connection() as connection:
                connection.execute(
                    "INSERT INTO Users(username,password_hash,is_active) VALUES('owner','hash',1)"
                )
                connection.execute(
                    "INSERT INTO Jobs(external_job_id,created_by) VALUES('legacy-job',1)"
                )
                connection.execute(
                    "INSERT INTO JobFinancials(job_id,ap_invoice_number) VALUES(1,'AP-existing')"
                )
                connection.execute("DELETE FROM SchemaMigrations WHERE name='015_add_job_financials.py'")
                connection.execute("ALTER TABLE Jobs ADD COLUMN ap_invoice_number TEXT")
                connection.execute("UPDATE Jobs SET ap_invoice_number='AP-from-job' WHERE job_id=1")

            # JobSourceRecords financial columns are already absent, matching the
            # partial production state. Re-running must preserve both invoice values.
            AuthService(Settings(path, password_iterations=100_000))
            with auth.connection() as connection:
                invoices = {row[0] for row in connection.execute(
                    "SELECT ap_invoice_number FROM JobFinancials WHERE job_id=1"
                )}
                self.assertEqual(invoices, {"AP-existing", "AP-from-job"})
                self.assertNotIn("ap_invoice_number", {
                    row[1] for row in connection.execute("PRAGMA table_info(Jobs)")
                })
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM SchemaMigrations WHERE name='015_add_job_financials.py'"
                ).fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_failed_migration_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "db.sqlite"
            migrations = root / "migrations"
            migrations.mkdir()
            (migrations / "999_fail.sql").write_text("CREATE TABLE TemporaryThing(id); INVALID SQL;")
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE Existing(id)")
                connection.commit()
            finally:
                connection.close()
            settings = Settings(path, migrations_path=migrations, password_iterations=100_000)
            with self.assertRaises(sqlite3.OperationalError):
                AuthService(settings)
            connection = sqlite3.connect(path)
            try:
                self.assertFalse(connection.execute("SELECT 1 FROM SchemaMigrations WHERE name='999_fail.sql'").fetchone())
                self.assertFalse(connection.execute("SELECT 1 FROM sqlite_master WHERE name='TemporaryThing'").fetchone())
            finally:
                connection.close()

    def test_primary_address_is_unique(self):
        with self.settings_database() as (auth, admin_id):
            with auth.connection() as connection:
                connection.execute("INSERT INTO Techs(tech_code,first_name,last_name,created_by) VALUES('T1','A','B',?)", (admin_id,))
                connection.execute("INSERT INTO TechAddresses(tech_id,address_1,city,state,zip_code,created_by) VALUES(1,'A','C','S','Z',?)", (admin_id,))
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO TechAddresses(tech_id,address_1,city,state,zip_code,created_by) VALUES(1,'B','C','S','Z',?)", (admin_id,))

    from contextlib import contextmanager
    @contextmanager
    def settings_database(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = AuthService(Settings(Path(directory) / "db", password_iterations=100_000))
            admin_id = UserManager(auth).create_user("Admin", "admin-password-123", "admin")
            yield auth, admin_id
