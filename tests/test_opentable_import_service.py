import csv
import tempfile
import unittest
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.security.auth import AuthService
from app.security.user_manager import UserManager
from app.services.opentable_import_service import OpenTableImportService


COLUMNS = [
    "Record Number", "Request Date/Time", "MP Client.", "Job ID", "Project Name",
    "Scheduling Link", "Job Status", "Job Scheduled Date/Time", "Capture Address",
    "Floor/Unit/Suite", "Capture Size - Requested", "Additional Details",
    "Floor Plans/Attachments", "CT Travel Payout", "CT Off Hours Payout", "CT Rate",
    "AP Invoice Number", "CT Name", "On-Site Contact Name", "On-Site Contact Email",
    "On-Site Contact Number", "Preferred Date/Time 1", "Preferred Date/Time 2",
    "Alternative Date/Time", "Alternative Date/Time 2", "Alternative Date/Time 3",
]


def source_row(record_number, job_id, space, *, rate="0", size="", status="Scheduled"):
    return {
        "Record Number": record_number,
        "Request Date/Time": "1/2/2026 9:15am",
        "MP Client.": "Matterport Client",
        "Job ID": job_id,
        "Project Name": "Retail Capture",
        "Scheduling Link": "https://example.test/schedule",
        "Job Status": status,
        "Job Scheduled Date/Time": "1/14/2026 10:30am",
        "Capture Address": "123 Main St, Plymouth, MI, 48170, USA",
        "Floor/Unit/Suite": space,
        "Capture Size - Requested": size,
        "Additional Details": "Call before arrival",
        "Floor Plans/Attachments": "plan.pdf",
        "CT Travel Payout": "10.25" if space == "Parent Record" else "0",
        "CT Off Hours Payout": "5.50" if space == "Parent Record" else "0",
        "CT Rate": rate,
        "AP Invoice Number": "INV-100",
        "CT Name": "Test Technician",
        "On-Site Contact Name": "Site Manager",
        "On-Site Contact Email": "manager@example.test",
        "On-Site Contact Number": "555-0100",
        "Preferred Date/Time 1": "1/14/2026 10:30am",
        "Preferred Date/Time 2": "",
        "Alternative Date/Time": "",
        "Alternative Date/Time 2": "",
        "Alternative Date/Time 3": "",
    }


class OpenTableImportServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        settings = Settings(
            database_path=root / "mpops.db",
            schema_path=PROJECT_ROOT / "database" / "schema" / "001_initial.sql",
            password_iterations=100_000,
        )
        self.auth = AuthService(settings)
        users = UserManager(self.auth)
        users.create_user("Admin", "correct-horse-123", "admin")
        self.session = self.auth.authenticate("Admin", "correct-horse-123")
        self.service = OpenTableImportService(self.auth)
        self.csv_path = root / "opentable.csv"

    def tearDown(self):
        self.tempdir.cleanup()

    def write_rows(self, rows):
        with self.csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def test_preview_groups_parent_and_child_rows_into_one_job(self):
        self.write_rows([
            source_row("1001", "JOB-1", "Parent Record", rate="200.80", size="5000"),
            source_row("1002", "JOB-1", "LensCrafters", size="2500"),
        ])

        preview = self.service.preview(str(self.csv_path))

        self.assertEqual(preview["counts"], {"created": 1})
        self.assertEqual(len(preview["groups"]), 1)
        group = preview["groups"][0]
        self.assertEqual(group["source_row_count"], 2)
        self.assertEqual(group["parent_record_count"], 1)
        self.assertEqual(group["job"]["requested_capture_size"], 5000.0)
        self.assertIn("LensCrafters", group["job"]["additional_details"])
        self.assertEqual(group["job"]["city"], "Plymouth")
        self.assertEqual(group["job"]["state"], "MI")
        self.assertEqual(group["job"]["postal_code"], "48170")

    def test_import_creates_one_job_and_all_source_records(self):
        self.write_rows([
            source_row("1001", "JOB-1", "Parent Record", rate="200.80", size="5000"),
            source_row("1002", "JOB-1", "LensCrafters", size="2500"),
        ])

        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["source_rows_added"], 2)
        with self.auth.connection() as connection:
            job = connection.execute("SELECT * FROM Jobs WHERE external_job_id = 'JOB-1'").fetchone()
            records = connection.execute(
                "SELECT * FROM JobSourceRecords WHERE job_id = ? ORDER BY external_record_number",
                (job["job_id"],),
            ).fetchall()
        self.assertEqual(job["job_status"], "Scheduled")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["is_parent_record"], 1)
        self.assertAlmostEqual(records[0]["ct_rate"], 200.80)
        self.assertAlmostEqual(records[0]["ct_travel_payout"], 10.25)
        self.assertAlmostEqual(records[0]["ct_off_hours_payout"], 5.50)

    def test_reimport_is_idempotent(self):
        rows = [
            source_row("1001", "JOB-1", "Parent Record", rate="200.80", size="5000"),
            source_row("1002", "JOB-1", "LensCrafters", size="2500"),
        ]
        self.write_rows(rows)
        self.service.import_csv(self.session, str(self.csv_path))

        preview = self.service.preview(str(self.csv_path))
        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(preview["counts"], {"skipped": 1})
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["source_rows_added"], 0)
        with self.auth.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM Jobs").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM JobSourceRecords").fetchone()[0], 2
            )

    def test_existing_job_receives_only_new_source_record(self):
        self.write_rows([source_row("1001", "JOB-1", "Parent Record", rate="200.80")])
        self.service.import_csv(self.session, str(self.csv_path))
        self.write_rows([
            source_row("1001", "JOB-1", "Parent Record", rate="200.80"),
            source_row("1002", "JOB-1", "Second Floor"),
        ])

        preview = self.service.preview(str(self.csv_path))
        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(preview["counts"], {"updated": 1})
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["source_rows_added"], 1)
        with self.auth.connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM JobSourceRecords").fetchone()[0], 2
            )

    def test_invalid_currency_rolls_back_entire_import(self):
        bad = source_row("1002", "JOB-2", "Parent Record", rate="not-money")
        self.write_rows([
            source_row("1001", "JOB-1", "Parent Record", rate="200.80"),
            bad,
        ])

        with self.assertRaisesRegex(ValueError, "Invalid currency"):
            self.service.import_csv(self.session, str(self.csv_path))

        with self.auth.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM Jobs").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM JobSourceRecords").fetchone()[0], 0
            )


if __name__ == "__main__":
    unittest.main()
