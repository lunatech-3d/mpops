"""Add the timestamp and reason used when a Job is archived."""


def migrate(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(Jobs)")}
    if "archived_at" not in columns:
        connection.execute("ALTER TABLE Jobs ADD COLUMN archived_at TEXT")
    if "archive_reason" not in columns:
        connection.execute("ALTER TABLE Jobs ADD COLUMN archive_reason TEXT")