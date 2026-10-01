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
from app.ui.opentable_import_window import (
    address_review_details, address_review_display, protected_fields_display,
    preview_summary,
)


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
                "7221 Waverly Walk Ave, Charlotte, NC, 28277 8030",
                ("7221 Waverly Walk Ave", "Charlotte", "NC", "28277-8030", None),
            ),
            (
                "444 S Broad St  Brevard, NC 28712",
                ("444 S Broad St", "Brevard", "NC", "28712", None),
            ),
            (
                "1015 Barbara Jean Lane, Wingate, NC 28174 ,  Wingate ,   28174",
                ("1015 Barbara Jean Lane", "Wingate", "NC", "28174", None),
            ),
            (
                "105 Chestnut Cir Lake Lure, NC 28746",
                ("105 Chestnut Cir", "Lake Lure", "NC", "28746", None),
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
            (
                "20759 Hall Road, Macomb, MI, Macomb County, USA, 48044",
                ("20759 Hall Road", "Macomb", "MI", "48044", "USA"),
            ),
            (
                "25 Barclay Cir, Rochester Hills, Michigan 48307-4508",
                ("25 Barclay Cir", "Rochester Hills", "MI", "48307-4508", None),
            ),
            (
                "2125 South Telegraph Road, Bloomfield Township, MI, "
                "Oakland County, USA, 48302",
                ("2125 South Telegraph Road", "Bloomfield Township", "MI", "48302", "USA"),
            ),
            (
                "123 Main St, Raleigh, North Carolina 27601",
                ("123 Main St", "Raleigh", "NC", "27601", None),
            ),
            (
                "40053 8 Mile Road, Township of Northville, MI, USA",
                ("40053 8 Mile Road", "Township of Northville", "MI", None, "USA"),
            ),
            (
                "13205 E 14 Mile Rd        Sterling Heights    MI    48312",
                ("13205 E 14 Mile Rd", "Sterling Heights", "MI", "48312", None),
            ),
            (
                "13205 E 14 Mile Rd\tSterling Heights\tMI\t48312",
                ("13205 E 14 Mile Rd", "Sterling Heights", "MI", "48312", None),
            ),
            (
                "1209 North Wisner Street, Jackson, MI, Jackson County, USA, 49202",
                ("1209 North Wisner Street", "Jackson", "MI", "49202", "USA"),
            ),
        ]

        for raw, expected in cases:
            with self.subTest(raw=raw):
                parsed = self.service._parse_address(raw)
                actual = tuple(parsed[field] for field in (
                    "address_1", "city", "state", "postal_code", "country"
                ))
                self.assertEqual(actual, expected)

        county = self.service._parse_address(
            "20759 Hall Road, Macomb, MI, Macomb County, USA, 48044"
        )
        self.assertEqual(county["county"], "Macomb County")
        jackson = self.service._parse_address(
            "1209 North Wisner Street, Jackson, MI, Jackson County, USA, 49202"
        )
        self.assertEqual(jackson["county"], "Jackson County")

    def test_ambiguous_michigan_address_is_held_for_review(self):
        parsed = self.service._parse_address(
            "44000 GARFIELD RD CLINTON TOWNSHIP MI"
        )
        self.assertEqual(parsed["address_1"], "44000 GARFIELD RD CLINTON TOWNSHIP")
        self.assertIsNone(parsed["city"])
        self.assertEqual(parsed["state"], "MI")
        self.assertIn("city", self.service._address_warnings(
            {"capture_address_raw": "44000 GARFIELD RD CLINTON TOWNSHIP MI", **parsed}
        )[0])

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

    def test_reimport_fills_missing_michigan_columns_without_replacing_local_values(self):
        raw = "20759 Hall Road, Macomb, MI, Macomb County, USA, 48044"
        row = source_row("1001", "JOB-MI", "Parent Record")
        row["Capture Address"] = raw
        self.write_rows([row])
        self.service.import_csv(self.session, str(self.csv_path))
        # An older local database may have been imported by the previous parser.
        with self.auth.connection() as connection:
            connection.execute(
                "UPDATE Jobs SET city = NULL, state = NULL, county = NULL, country = NULL "
                "WHERE external_job_id = 'JOB-MI'"
            )

        preview = self.service.preview(str(self.csv_path))
        self.assertEqual(preview["items"][0]["changed_source_rows"], 0)
        self.assertEqual(set(preview["items"][0]["address_changes"]),
                         {"city", "state", "county", "country"})
        self.assertEqual(preview_summary(preview)["address_fill_jobs"], 1)
        self.assertIn("Fill: City", address_review_display(preview["items"][0]))
        self.service.import_csv(self.session, str(self.csv_path))
        with self.auth.connection() as connection:
            job = connection.execute(
                "SELECT address_1,city,state,postal_code,county,country FROM Jobs "
                "WHERE external_job_id = 'JOB-MI'"
            ).fetchone()
        self.assertEqual(tuple(job), (
            "20759 Hall Road", "Macomb", "MI", "48044", "Macomb County", "USA"
        ))

    def test_untracked_legacy_corrections_are_held_on_changed_source(self):
        row = source_row("1001", "JOB-LEGACY", "Parent Record")
        self.write_rows([row])
        self.service.import_csv(self.session, str(self.csv_path))
        with self.auth.connection() as connection:
            connection.execute(
                "UPDATE Jobs SET address_1 = '12 Corrected St', city = 'Canton', "
                "state = 'OH', postal_code = '99999' WHERE external_job_id = 'JOB-LEGACY'"
            )
        changed = dict(row)
        changed["Capture Address"] = "500 Source Rd, Raleigh, NC, 27601, USA"
        self.write_rows([changed])

        preview = self.service.preview(str(self.csv_path))
        self.assertEqual(set(preview["items"][0]["held_address_fields"]),
                         {"address_1", "city", "state", "postal_code"})
        self.assertEqual(preview_summary(preview)["address_review_jobs"], 1)
        self.service.import_csv(self.session, str(self.csv_path))
        with self.auth.connection() as connection:
            job = connection.execute(
                "SELECT capture_address_raw,address_1,city,state,postal_code "
                "FROM Jobs WHERE external_job_id = 'JOB-LEGACY'"
            ).fetchone()
        self.assertEqual(tuple(job), (
            changed["Capture Address"], "12 Corrected St", "Canton", "OH", "99999"
        ))

    def test_malformed_csv_row_with_extra_columns_is_rejected(self):
        self.write_rows([source_row("1001", "JOB-1", "Parent Record")])
        lines = self.csv_path.read_text(encoding="utf-8-sig").splitlines()
        lines[1] += ",extra-cell"
        self.csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        with self.assertRaisesRegex(ValueError, "extra CSV columns"):
            self.service.preview(str(self.csv_path))

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

    def test_reimport_fills_missing_address_fields_but_holds_nonblank_conflicts(self):
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
        self.assertEqual(preview["items"][0]["changed_job_fields"], ["state"])
        self.assertEqual(preview["items"][0]["held_address_fields"],
                         ["address_1", "city"])
        self.assertIn("Held for review", address_review_details(preview["items"][0]))
        self.assertIn("Review:", address_review_display(preview["items"][0]))

        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["source_rows_updated"], 0)
        with self.auth.connection() as connection:
            job = connection.execute(
                "SELECT address_1, city, state FROM Jobs WHERE external_job_id = 'JOB-1'"
            ).fetchone()
        self.assertEqual(tuple(job),
                         ("Dental Care at Village Commons", "6400 Weddington Rd Ste J", "NC"))

    def test_reimport_repairs_unprotected_whitespace_parser_artifact(self):
        raw = "13205 E 14 Mile Rd        Sterling Heights    MI    48312"
        row = source_row("1001", "JOB-WHITESPACE", "Parent Record")
        row["Capture Address"] = raw
        self.write_rows([row])
        self.service.import_csv(self.session, str(self.csv_path))

        # Reproduce the values written by the former parser, which collapsed the
        # column spacing before it tried to identify the city.
        legacy = self.service._legacy_parse_address(raw)
        with self.auth.connection() as connection:
            connection.execute(
                "UPDATE Jobs SET address_1 = ?, city = ? "
                "WHERE external_job_id = 'JOB-WHITESPACE'",
                (legacy["address_1"], legacy["city"]),
            )

        preview = self.service.preview(str(self.csv_path))

        self.assertEqual(preview["counts"], {"updated": 1})
        self.assertEqual(preview["items"][0]["changed_source_rows"], 0)
        self.assertEqual(
            preview["items"][0]["address_changes"], ["address_1", "city"]
        )
        self.assertEqual(preview["items"][0]["address_repairs"], ["address_1"])
        self.assertEqual(preview["items"][0]["held_address_fields"], [])
        self.assertEqual(
            address_review_display(preview["items"][0]),
            "Repair: Address 1; Fill: City",
        )
        self.assertIn(
            "Will repair former parser value",
            address_review_details(preview["items"][0]),
        )

        self.service.import_csv(self.session, str(self.csv_path))
        with self.auth.connection() as connection:
            job = connection.execute(
                "SELECT capture_address_raw,address_1,city,state,postal_code "
                "FROM Jobs WHERE external_job_id = 'JOB-WHITESPACE'"
            ).fetchone()
        self.assertEqual(tuple(job), (
            raw, "13205 E 14 Mile Rd", "Sterling Heights", "MI", "48312"
        ))

    def test_reimport_does_not_clear_legacy_nonblank_address(self):
        raw = "Studio 54 Dental, 100 Main St Suite 200"
        row = source_row("1001", "JOB-1", "Parent Record")
        row["Capture Address"] = raw
        self.write_rows([row])
        self.service.import_csv(self.session, str(self.csv_path))
        with self.auth.connection() as connection:
            connection.execute("UPDATE Jobs SET city = '100 Main St Suite 200'")

        preview = self.service.preview(str(self.csv_path))
        result = self.service.import_csv(self.session, str(self.csv_path))

        self.assertEqual(preview["counts"], {"skipped": 1})
        self.assertEqual(preview["items"][0]["held_address_fields"], ["city"])
        self.assertEqual(result["skipped"], 1)
        with self.auth.connection() as connection:
            city = connection.execute("SELECT city FROM Jobs").fetchone()[0]
        self.assertEqual(city, "100 Main St Suite 200")

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
