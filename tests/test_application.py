import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.security.auth import AuthService, Session
from app.security.passwords import hash_password, verify_password
from app.security.user_manager import AuthorizationError, UserManager


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

    def test_application_modules_import(self):
        for name in ("app.main", "app.ui.styles", "app.ui.dashboard", "app.ui.main_window", "app.ui.user_manager_window"):
            self.assertIsNotNone(importlib.import_module(name))

    def test_logout_clears_environment(self):
        self.admin.apply_to_environment()
        Session.clear_environment()
        self.assertTrue(all(key not in os.environ for key in ("MPOPS_USER_ID", "MPOPS_USERNAME", "MPOPS_ROLE")))

    def test_admin_crud_password_and_activation(self):
        uid = self.users.create_user("NewUser", "original-pass-123", "operator", self.admin, "New User")
        self.assertEqual(len(self.users.list_users(self.admin, "new user")), 1)
        self.users.update_user(uid, display_name="Changed Name", role="viewer", actor=self.admin)
        self.assertEqual(self.users.get_user(uid, self.admin)["role"], "viewer")
        self.users.reset_password(uid, "replacement-123", self.admin)
        self.assertEqual(self.auth.authenticate("newuser", "replacement-123").user_id, uid)
        self.users.set_active(uid, False, self.admin)
        self.assertFalse(self.users.get_user(uid, self.admin)["is_active"])
        self.users.set_active(uid, True, self.admin)
        self.assertTrue(self.users.get_user(uid, self.admin)["is_active"])

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
        with self.auth.connect() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {"users", "audit_log", "schema_migrations"})


class MigrationTests(unittest.TestCase):
    def test_migration_preserves_existing_user_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            schema = (PROJECT_ROOT / "database/schema/001_initial.sql").read_text()
            encoded = hash_password("preserved-pass-123", 100_000)
            with sqlite3.connect(path) as connection:
                connection.executescript(schema)
                connection.execute("INSERT INTO users (username,username_key,password_hash,role,is_active,created_at) VALUES (?,?,?,?,1,?)",
                                   ("Legacy", "legacy", encoded, "admin", "2020-01-01T00:00:00Z"))
            auth = AuthService(Settings(path, password_iterations=100_000))
            self.assertEqual(auth.authenticate("Legacy", "preserved-pass-123").username, "Legacy")
            with auth.connect() as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            self.assertTrue({"display_name", "created_by", "updated_at", "updated_by"}.issubset(columns))
