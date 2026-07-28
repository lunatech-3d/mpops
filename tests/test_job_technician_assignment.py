import tempfile
import unittest
import importlib.util
from pathlib import Path

from app.config import Settings
from app.security.auth import AuthService
from app.security.user_manager import UserManager
from app.services.jobs_service import JobsService


class JobTechnicianAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = AuthService(
            Settings(Path(self.tmp.name) / "mpops.db", password_iterations=100_000)
        )
        # Fresh-schema initialization intentionally predates the operational tables.
        migration_path = Path(__file__).parents[1] / "database/migrations/004_add_jobs.py"
        spec = importlib.util.spec_from_file_location("add_jobs_migration", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.auth.connection() as connection:
            migration.migrate(connection)
            connection.execute("ALTER TABLE Jobs ADD COLUMN market_id INTEGER")
            connection.execute("ALTER TABLE Markets ADD COLUMN state TEXT")
        users = UserManager(self.auth)
        users.create_user("admin", "admin-password-123", "admin")
        self.session = self.auth.authenticate("admin", "admin-password-123")
        self.service = JobsService(self.auth)
        with self.auth.connection() as connection:
            connection.executemany(
                "INSERT INTO Techs (tech_code, first_name, last_name, status, "
                "created_by) VALUES (?, ?, ?, ?, ?)",
                [
                    ("T2", "Inactive", "Person", "Inactive", self.session.user_id),
                    ("T1", "Active", "Able", "Active", self.session.user_id),
                    ("T3", "Second", "Baker", "Active", self.session.user_id),
                ],
            )
        self.active_id = self._tech_id("T1")
        self.second_id = self._tech_id("T3")
        self.inactive_id = self._tech_id("T2")

    def tearDown(self):
        self.tmp.cleanup()

    def _tech_id(self, code):
        with self.auth.connection() as connection:
            return connection.execute(
                "SELECT tech_id FROM Techs WHERE tech_code = ?", (code,)
            ).fetchone()[0]

    def _assignments(self, job_id):
        with self.auth.connection() as connection:
            return connection.execute(
                "SELECT * FROM JobAssignments WHERE job_id = ? "
                "ORDER BY job_assignment_id", (job_id,)
            ).fetchall()

    def test_options_only_include_active_technicians_in_name_order(self):
        options = self.service.list_active_technician_options()
        self.assertEqual([row["tech_id"] for row in options], [self.active_id, self.second_id])
        self.assertNotIn(self.inactive_id, [row["tech_id"] for row in options])

    def test_create_load_unchanged_change_and_clear_preserve_history(self):
        job_id = self.service.create_job(
            self.session, {"external_job_id": "JOB-1", "market_id": None}, self.active_id
        )
        current = self.service.get_current_primary_assignment(job_id)
        self.assertEqual(current["tech_id"], self.active_id)
        self.assertEqual(len(self._assignments(job_id)), 1)

        self.service.update_job(self.session, job_id, {}, self.active_id)
        self.assertEqual(len(self._assignments(job_id)), 1)

        self.service.update_job(self.session, job_id, {}, self.second_id)
        rows = self._assignments(job_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["assignment_status"], "Unassigned")
        self.assertIsNotNone(rows[0]["unassigned_at"])
        self.assertEqual(rows[1]["tech_id"], self.second_id)

        self.service.update_job(self.session, job_id, {}, None)
        rows = self._assignments(job_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["assignment_status"], "Unassigned")
        self.assertIsNone(self.service.get_current_primary_assignment(job_id))

    def test_inactive_technician_cannot_be_newly_assigned_and_job_rolls_back(self):
        with self.assertRaisesRegex(ValueError, "active technicians"):
            self.service.create_job(
                self.session, {"external_job_id": "ROLLBACK"}, self.inactive_id
            )
        self.assertIsNone(self.service.get_job_by_external_id("ROLLBACK"))

    def test_market_update_remains_atomic_with_assignment(self):
        with self.auth.connection() as connection:
            market_id = connection.execute(
                "INSERT INTO Markets (market_name, status, created_by) "
                "VALUES ('North', 'Active', ?)", (self.session.user_id,)
            ).lastrowid
        job_id = self.service.create_job(self.session, {"external_job_id": "JOB-2"})
        updated = self.service.update_job(
            self.session, job_id, {"market_id": market_id}, self.active_id
        )
        self.assertEqual(updated["market_id"], market_id)
        self.assertEqual(
            self.service.get_current_primary_assignment(job_id)["tech_id"], self.active_id
        )
