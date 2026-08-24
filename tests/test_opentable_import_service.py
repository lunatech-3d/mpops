import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.security.audit import record_event
from app.security.auth import AuthService
from app.security.user_manager import UserManager
from app.services.jobs_service import JobsService
from app.services.opentable_import_service import OpenTableImportService
from app.ui.opentable_import_window import protected_fields_display, preview_summary


COLUMNS = [
    "Record Number", "Request Date/Time", "MP Client.", "Job ID", "Project Name",
    "Scheduling Link", "Job Status", "Job Scheduled Date/Time", "Capture Address",
    "Floor/Unit/Suite", "Capture Size - Requested", "Additional Details",
    "Floor Plans/Attachments", "CT Travel Payout", "CT Off Hours Payout", "CT Rate",
    "AP Invoice Number", "CT Name", "On-Site Contact Name", "On-Site Contact Email",
    "On-Site Contact Number", "Preferred Date/Time 1", "Preferred Date/Time 2",
    "Alternative Date/Time", "Alternative Date/Time 2", "Alternative Date/Time 3",
]


def source_row(record_number, job_id, space, *, rate="0", size="", status="Scheduled",
               invoice="INV-100"):
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
        "AP Invoice Number": invoice,
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
        # The current production schema includes this field, while the minimal base
        # schema used by these focused importer tests predates it.
        with self.auth.connection() as connection:
            market_columns = {row[1] for row in connection.execute("PRAGMA table_info(Markets)")}
            if "state" not in market_columns:
                connection.execute("ALTER TABLE Markets ADD COLUMN state TEXT")
        users = UserManager(self.auth)
        users.create_user("Admin", "correct-horse-123", "admin")
        self.session = self.auth.authenticate("Admin", "correct-horse-123")
        self.service = OpenTableImportService(self.auth)
        self.csv_path = root / "opentable.csv"

    def tearDown(self):
        self.tempdir.cleanup()

    def write_rows(self, rows, *, columns=COLUMNS):
        with self.csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def test_parse_address_variants(self):
        cases = [
            (
                "123 Main St, Plymouth, MI, 48170, USA",
                ("123 Main St", "Plymouth", "MI", "48170", "USA"),
            ),
            (
                "Dental Care at Village Commons, 6400 Weddington Rd Ste J, "
                "Wesley Chapel, NC",
                ("6400 Weddington Rd Ste J", "Wesley Chapel", "NC", None, None),
            ),
            (
                "Stoney Point Dental Care, 7483 Rockfish Rd, Fayetteville, NC",
                ("7483 Rockfish Rd", "Fayetteville", "NC", None, None),
            ),
            (
                "6400 Veterans Blvd, Bryson City NC 28713",
                ("6400 Veterans Blvd", "Bryson City", "NC", "28713", None),
            ),
            (
                "5057 Woodward Ave, Detroit, MI 48202",
                ("5057 Woodward Ave", "Detroit", "MI", "48202", None),
            ),
            (
                "7483 Rockfish Rd, Fayetteville, NC",
                ("7483 Rockfish Rd", "Fayetteville", "NC", None, None),
            ),
            (
                "6400 Weddington Rd Ste J, Wesley Chapel, NC 28104",
                ("6400 Weddington Rd Ste J", "Wesley Chapel", "NC", "28104", None),
            ),
            (
                "123 Main St, Grand Rapids, MI 49503-1234",
                ("123 Main St", "Grand Rapids", "MI", "49503-1234", None),
            ),
            (
                "1965 Michigan Ave, Alma, MI, 48801, US",
                ("1965 Michigan Ave", "Alma", "MI", "48801", "USA"),
            ),
            (
                "123 Main St, Plymouth, mi 48170",
                ("123 Main St", "Plymouth", "MI", "48170", None),
            ),
            (
                "Studio 54 Dental, 25 Oak Ave Unit 4, Austin, TX",
                ("25 Oak Ave Unit 4", "Austin", "TX", None, None),
            ),
        ]

        for raw, expected in cases:
            with self.subTest(raw=raw):
                parsed = self.service._parse_address(raw)
                actual = tuple(parsed[field] for field in (
                    "address_1", "city", "state", "postal_code", "country"
                ))
                self.assertEqual(actual, expected)

    def test_partial_address_does_not_guess_street_as_city_and_preserves_raw(self):
        raw = "Studio 54 Dental, 100 Main St Suite 200"

        parsed = self.service._parse_address(raw)
        built = self.service._build_job(
            "JOB-PARTIAL", [source_row("1001", "JOB-PARTIAL", "Parent Record") | {
                "Capture Address": raw,
            }],
        )

        self.assertEqual(parsed["address_1"], "100 Main St Suite 200")
        self.assertIsNone(parsed["city"])
        self.assertEqual(built["capture_address_raw"], raw)

    def test_import_writes_business_prefixed_address_and_preserves_raw(self):
        raw = (
            "Dental Care at Village Commons, 6400 Weddington Rd Ste J, "
            "Wesley Chapel, NC"
        )
        row = source_row("1001", "JOB-1", "Parent Record")
        row["Capture Address"] = raw
        self.write_rows([row])

        self.service.import_csv(self.session, str(self.csv_path))

        with self.auth.connection() as connection:
            job = connection.execute(
                "SELECT address_1, city, state, postal_code, capture_address_raw FROM Jobs"
            ).fetchone()
        self.assertEqual(job["address_1"], "6400 Weddington Rd Ste J")
        self.assertEqual(job["city"], "Wesley Chapel")
        self.assertEqual(job["state"], "NC")
        self.assertIsNone(job["postal_code"])
        self.assertEqual(job["capture_address_raw"], raw)

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

    def test_import_creates_one_job_and_preserves_all_source_records(self):
        parent = source_row("1001", "JOB-1", "Parent Record", rate="200.80", size="5000")
        child = source_row("1002", "JOB-1", "LensCrafters", size="2500")
        self.write_rows([parent, child])

        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["source_rows_added"], 2)
        self.assertEqual(result["source_rows_updated"], 0)
        with self.auth.connection() as connection:
            job = connection.execute("SELECT * FROM Jobs WHERE external_job_id = 'JOB-1'").fetchone()
            records = connection.execute(
                "SELECT sr.*, jf.ap_invoice_number, jf.ct_rate, jf.ct_travel_payout, "
                "jf.ct_off_hours_payout FROM JobSourceRecords sr "
                "JOIN JobFinancials jf ON jf.job_source_record_id = sr.job_source_record_id "
                "WHERE sr.job_id = ? ORDER BY sr.external_record_number",
                (job["job_id"],),
            ).fetchall()
        self.assertEqual(job["job_status"], "Scheduled")
        self.assertNotIn("ap_invoice_number", job.keys())
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["is_parent_record"], 1)
        self.assertAlmostEqual(records[0]["ct_rate"], 200.80)
        self.assertAlmostEqual(records[0]["ct_travel_payout"], 10.25)
        self.assertAlmostEqual(records[0]["ct_off_hours_payout"], 5.50)
        preserved = json.loads(records[0]["source_row_json"])
        self.assertEqual(preserved, parent)
        self.assertNotIn("__source_row_number", preserved)

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
        self.assertEqual(result["source_rows_updated"], 0)
        with self.auth.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM Jobs").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM JobSourceRecords").fetchone()[0], 2
            )

    def test_local_normalized_address_corrections_survive_changed_source_reimport(self):
        original = source_row("1001", "JOB-1", "Parent Record")
        self.write_rows([original])
        self.service.import_csv(self.session, str(self.csv_path))
        jobs = JobsService(self.auth)
        job_id = jobs.get_job_by_external_id("JOB-1")["job_id"]
        jobs.update_job(self.session, job_id, {
            "address_1": "999 Corrected Ave",
            "address_2": "Suite B",
            "city": "Canton",
            "state": "OH",
            "postal_code": "99999",
        })

        changed = dict(original)
        changed["Capture Address"] = "500 Source Rd, Raleigh, NC, 27601, USA"
        self.write_rows([changed])
        preview = self.service.preview(str(self.csv_path))

        self.assertEqual(preview["items"][0]["changed_job_fields"], ["capture_address_raw"])
        self.assertEqual(preview["items"][0]["protected_job_fields"], [
            "address_1", "address_2", "city", "postal_code", "state",
        ])
        self.assertEqual(
            protected_fields_display(preview["items"][0]),
            "Address 1, Address 2, City, ZIP, State",
        )
        summary = preview_summary(preview)
        self.assertEqual((summary["protected_jobs"], summary["protected_fields"]), (1, 5))

        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(result["updated"], 1)
        loaded = jobs.get_job(job_id)
        self.assertEqual(
            tuple(loaded[field] for field in (
                "address_1", "address_2", "city", "state", "postal_code"
            )),
            ("999 Corrected Ave", "Suite B", "Canton", "OH", "99999"),
        )
        self.assertEqual(
            loaded["capture_address_raw"], "500 Source Rd, Raleigh, NC, 27601, USA"
        )
        self.assertEqual(loaded["protected_fields"], [
            "address_1", "address_2", "city", "postal_code", "state",
        ])

    def test_migration_backfills_audited_pre_protection_address_correction(self):
        self.write_rows([source_row("1001", "JOB-1", "Parent Record")])
        self.service.import_csv(self.session, str(self.csv_path))
        with self.auth.connection() as connection:
            job_id = connection.execute(
                "SELECT job_id FROM Jobs WHERE external_job_id = 'JOB-1'"
            ).fetchone()[0]
            connection.execute("UPDATE Jobs SET city = 'Canton' WHERE job_id = ?", (job_id,))
            record_event(
                connection,
                "job_updated",
                actor_user_id=self.session.user_id,
                details={
                    "job_id": job_id,
                    "external_job_id": "JOB-1",
                    "fields_changed": ["city"],
                    "before": {"city": "Plymouth"},
                    "after": {"city": "Canton"},
                },
            )

        migration_path = (
            PROJECT_ROOT / "database" / "migrations" / "028_backfill_job_field_overrides.py"
        )
        spec = importlib.util.spec_from_file_location("backfill_job_overrides", migration_path)
        migration = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(migration)
        with self.auth.connection() as connection:
            migration.migrate(connection)
            migration.migrate(connection)
            override = connection.execute(
                "SELECT field_name, source_system, reason FROM JobFieldOverrides "
                "WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            override_count = connection.execute(
                "SELECT count(*) FROM JobFieldOverrides WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
            backfill_events = connection.execute(
                "SELECT count(*) FROM AuditLog "
                "WHERE action = 'job_field_overrides_backfilled'"
            ).fetchone()[0]

        self.assertEqual(tuple(override[:2]), ("city", "OpenTable"))
        self.assertIn("pre-protection", override["reason"])
        self.assertEqual((override_count, backfill_events), (1, 1))
        self.service.import_csv(self.session, str(self.csv_path))
        with self.auth.connection() as connection:
            city = connection.execute(
                "SELECT city FROM Jobs WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
        self.assertEqual(city, "Canton")

    def test_reimport_reprocesses_unchanged_source_with_refined_address_parser(self):
        raw = (
            "Dental Care at Village Commons, 6400 Weddington Rd Ste J, "
            "Wesley Chapel, NC"
        )
        row = source_row("1001", "JOB-1", "Parent Record")
        row["Capture Address"] = raw
        self.write_rows([row])
        self.service.import_csv(self.session, str(self.csv_path))

        # Reproduce the address values written by the parser before it learned to
        # ignore a business-name prefix.
        with self.auth.connection() as connection:
            connection.execute(
                "UPDATE Jobs SET address_1 = ?, city = ?, state = NULL",
                ("Dental Care at Village Commons", "6400 Weddington Rd Ste J"),
            )

        preview = self.service.preview(str(self.csv_path))

        self.assertEqual(preview["counts"], {"updated": 1})
        self.assertEqual(preview["items"][0]["changed_source_rows"], 0)
        self.assertEqual(
            preview["items"][0]["changed_job_fields"],
            ["address_1", "city", "state"],
        )

        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["source_rows_updated"], 0)
        with self.auth.connection() as connection:
            job = connection.execute(
                "SELECT address_1, city, state FROM Jobs WHERE external_job_id = 'JOB-1'"
            ).fetchone()
        self.assertEqual(tuple(job), ("6400 Weddington Rd Ste J", "Wesley Chapel", "NC"))

    def test_reimport_clears_address_value_previously_inferred_in_error(self):
        raw = "Studio 54 Dental, 100 Main St Suite 200"
        row = source_row("1001", "JOB-1", "Parent Record")
        row["Capture Address"] = raw
        self.write_rows([row])
        self.service.import_csv(self.session, str(self.csv_path))
        with self.auth.connection() as connection:
            connection.execute("UPDATE Jobs SET city = '100 Main St Suite 200'")

        preview = self.service.preview(str(self.csv_path))
        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(preview["counts"], {"updated": 1})
        self.assertEqual(result["updated"], 1)
        with self.auth.connection() as connection:
            city = connection.execute("SELECT city FROM Jobs").fetchone()[0]
        self.assertIsNone(city)

    def test_blank_invoice_number_is_stored_as_null(self):
        self.write_rows([source_row("1001", "JOB-1", "Parent Record", invoice="   ")])

        self.service.import_csv(self.session, str(self.csv_path))

        with self.auth.connection() as connection:
            job = connection.execute("SELECT ap_invoice_number FROM JobFinancials").fetchone()
        self.assertIsNone(job["ap_invoice_number"])

    def test_reimport_backfills_job_invoice_from_non_parent_source_row(self):
        parent = source_row("1001", "JOB-1", "Parent Record", invoice="")
        child = source_row("1002", "JOB-1", "First Floor", invoice="AP-child-record")
        self.write_rows([parent, child])
        groups = self.service.read_csv(str(self.csv_path))

        # Reproduce a job imported before AP invoice numbers were mapped. Its preserved
        # source rows already match the CSV, so only the job-level backfill needs an update.
        self.service.import_csv(self.session, str(self.csv_path))
        with self.auth.connection() as connection:
            connection.execute("UPDATE JobFinancials SET ap_invoice_number = NULL")

        preview = self.service.preview(str(self.csv_path))
        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(groups[0]["source_rows"][1]["AP Invoice Number"], "AP-child-record")
        self.assertEqual(preview["counts"], {"skipped": 1})
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["source_rows_updated"], 0)
        with self.auth.connection() as connection:
            job = connection.execute(
                "SELECT ap_invoice_number FROM JobFinancials "
                "WHERE ap_invoice_number IS NOT NULL"
            ).fetchone()
        self.assertEqual(job["ap_invoice_number"], "AP-child-record")

    def test_duplicate_job_prefers_parent_payout_invoice_and_preserves_zero_row(self):
        zero_row = source_row(
            "1001", "JobID6595104381421649357", "LensCrafters",
            rate="0.00", invoice="AP-recEHBEkre6fJnsqX",
        )
        parent = source_row(
            "1002", "JobID6595104381421649357", "Parent Record",
            rate="200.80", invoice="AP-rec862qmpezHT0y6K",
        )
        self.write_rows([zero_row, parent])

        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual((result["created"], result["source_rows_added"]), (1, 2))
        with self.auth.connection() as connection:
            job = connection.execute("SELECT * FROM Jobs").fetchone()
            records = connection.execute(
                "SELECT sr.record_description, jf.ct_rate, jf.ct_travel_payout, "
                "jf.ct_off_hours_payout, jf.ap_invoice_number FROM JobSourceRecords sr "
                "JOIN JobFinancials jf ON jf.job_source_record_id = sr.job_source_record_id "
                "ORDER BY sr.external_record_number"
            ).fetchall()
        self.assertNotIn("ap_invoice_number", job.keys())
        self.assertEqual([tuple(row) for row in records], [
            ("LensCrafters", 0, 0, 0, "AP-recEHBEkre6fJnsqX"),
            ("Parent Record", 200.8, 10.25, 5.5, "AP-rec862qmpezHT0y6K"),
        ])

    def test_missing_invoice_column_is_rejected(self):
        columns = [column for column in COLUMNS if column != "AP Invoice Number"]
        self.write_rows([source_row("1001", "JOB-1", "Parent Record")], columns=columns)

        with self.assertRaisesRegex(ValueError, "AP Invoice Number"):
            self.service.read_csv(str(self.csv_path))

    def test_duplicate_import_keeps_identical_invoice_without_conflict(self):
        self.write_rows([source_row("1001", "JOB-1", "Parent Record",
                                    invoice="AP-rec1ZrtnPyo5sE9a5")])
        self.service.import_csv(self.session, str(self.csv_path))

        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(result["skipped"], 1)
        with self.auth.connection() as connection:
            job = connection.execute("SELECT ap_invoice_number FROM JobFinancials").fetchone()
        self.assertEqual(job["ap_invoice_number"], "AP-rec1ZrtnPyo5sE9a5")

    def test_changed_invoice_is_logged_and_does_not_overwrite_job(self):
        original = source_row("1001", "JOB-1", "Parent Record", invoice="AP-original")
        self.write_rows([original])
        self.service.import_csv(self.session, str(self.csv_path))
        changed = source_row("1001", "JOB-1", "Parent Record", invoice="AP-changed")
        self.write_rows([changed])

        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(result["source_rows_updated"], 1)
        with self.auth.connection() as connection:
            financial = connection.execute(
                "SELECT ap_invoice_number FROM JobFinancials"
            ).fetchone()
        self.assertEqual(financial["ap_invoice_number"], "AP-changed")

    def test_invoice_number_whitespace_is_trimmed(self):
        self.write_rows([source_row("1001", "JOB-1", "Parent Record",
                                    invoice="  AP-rec1ZrtnPyo5sE9a5 \t")])

        self.service.import_csv(self.session, str(self.csv_path))

        with self.auth.connection() as connection:
            job = connection.execute("SELECT ap_invoice_number FROM JobFinancials").fetchone()
        self.assertEqual(job["ap_invoice_number"], "AP-rec1ZrtnPyo5sE9a5")

    def test_job_validation_rejects_financial_fields(self):
        with self.assertRaisesRegex(ValueError, "ap_invoice_number"):
            JobsService._clean_job(
                {"external_job_id": "JOB-1", "ap_invoice_number": "AP-rec1"},
                creating=True,
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
        self.assertEqual(result["source_rows_updated"], 0)
        with self.auth.connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM JobSourceRecords").fetchone()[0], 2
            )

    def test_changed_source_record_is_previewed_and_updated(self):
        original = source_row("1001", "JOB-1", "Parent Record", rate="200.80", size="5000")
        self.write_rows([original])
        self.service.import_csv(self.session, str(self.csv_path))

        changed = source_row("1001", "JOB-1", "Parent Record", rate="225.50", size="5500")
        changed["Additional Details"] = "Use loading dock"
        self.write_rows([changed])

        preview = self.service.preview(str(self.csv_path))
        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(preview["counts"], {"updated": 1})
        self.assertEqual(preview["items"][0]["changed_source_rows"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["source_rows_added"], 0)
        self.assertEqual(result["source_rows_updated"], 1)
        with self.auth.connection() as connection:
            record = connection.execute(
                "SELECT sr.*, jf.ct_rate FROM JobSourceRecords sr "
                "JOIN JobFinancials jf ON jf.job_source_record_id = sr.job_source_record_id "
                "WHERE sr.external_record_number = '1001'"
            ).fetchone()
        self.assertAlmostEqual(record["ct_rate"], 225.50)
        self.assertEqual(record["requested_capture_size"], 5500.0)
        self.assertEqual(json.loads(record["source_row_json"]), changed)

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
