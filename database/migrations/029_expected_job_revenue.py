"""Add the operator-entered expected gross revenue for a Job."""


def migrate(connection):
    columns = {row[1] for row in connection.execute('PRAGMA table_info("Jobs")')}
    if "expected_job_revenue" not in columns:
        connection.execute(
            "ALTER TABLE Jobs ADD COLUMN expected_job_revenue NUMERIC "
            "CHECK(expected_job_revenue IS NULL OR expected_job_revenue >= 0)"
        )
