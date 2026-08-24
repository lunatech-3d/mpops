"""Protect auditable local Job address corrections made before migration 026."""

import json


PROTECTABLE_FIELDS = frozenset({
    "address_1", "address_2", "city", "state", "postal_code", "county", "country",
})


def _table_exists(connection, table_name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def migrate(connection):
    required_tables = {"AuditLog", "Jobs", "JobSourceRecords", "JobFieldOverrides", "Users"}
    if not all(_table_exists(connection, table) for table in required_tables):
        return

    latest_edits = {}
    for row in connection.execute(
        "SELECT id, occurred_at, actor_user_id, details_json FROM AuditLog "
        "WHERE action = 'job_updated' ORDER BY id"
    ):
        try:
            details = json.loads(row[3] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(details, dict):
            continue
        try:
            job_id = int(details.get("job_id"))
        except (TypeError, ValueError):
            continue
        changed_fields = details.get("fields_changed") or ()
        after = details.get("after") or {}
        if not isinstance(changed_fields, list) or not isinstance(after, dict):
            continue
        for field_name in PROTECTABLE_FIELDS.intersection(changed_fields):
            if field_name in after:
                latest_edits[(job_id, field_name)] = {
                    "value": after[field_name],
                    "protected_at": row[1],
                    "protected_by": row[2],
                }

    valid_users = {row[0] for row in connection.execute("SELECT id FROM Users")}
    fallback_timestamp = connection.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    inserted = 0
    protected_jobs = set()
    protected_fields = set()
    for (job_id, field_name), edit in sorted(latest_edits.items()):
        # Field names come only from the fixed allow-list above, so this identifier
        # interpolation cannot contain untrusted input.
        current = connection.execute(
            f"SELECT {field_name} FROM Jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if current is None or current[0] != edit["value"]:
            # The audited value is no longer current. Do not protect a later parser or
            # source update merely because an older manual correction once existed.
            continue
        source_systems = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT source_system FROM JobSourceRecords "
                "WHERE job_id = ? AND source_system IS NOT NULL AND source_system <> ''",
                (job_id,),
            )
        }
        protected_by = edit["protected_by"]
        if protected_by not in valid_users:
            protected_by = None
        for source_system in sorted(source_systems):
            cursor = connection.execute(
                "INSERT OR IGNORE INTO JobFieldOverrides "
                "(job_id, field_name, source_system, protected_at, protected_by, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    field_name,
                    source_system,
                    edit["protected_at"] or fallback_timestamp,
                    protected_by,
                    "Backfilled from a pre-protection job_updated audit event",
                ),
            )
            if cursor.rowcount:
                inserted += 1
                protected_jobs.add(job_id)
                protected_fields.add(field_name)

    if inserted:
        connection.execute(
            "INSERT INTO AuditLog (occurred_at, action, details_json) "
            "VALUES (CURRENT_TIMESTAMP, 'job_field_overrides_backfilled', ?)",
            (json.dumps({
                "override_count": inserted,
                "job_count": len(protected_jobs),
                "fields": sorted(protected_fields),
            }, sort_keys=True),),
        )
