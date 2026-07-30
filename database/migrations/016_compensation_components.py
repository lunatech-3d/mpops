"""Add component-aware compensation policies for base, travel, and off-hours pay."""


def migrate(connection):
    columns = {row[1] for row in connection.execute(
        "PRAGMA table_info(TechnicianCompensationRules)")}
    if "compensation_component" not in columns:
        connection.execute(
            "ALTER TABLE TechnicianCompensationRules ADD COLUMN "
            "compensation_component TEXT NOT NULL DEFAULT 'Overall' "
            "CHECK(compensation_component IN ('Overall','Base','Travel','Off Hours'))")
    connection.execute("DROP INDEX IF EXISTS ux_compensation_rule_active_scope")
    connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_compensation_rule_active_scope_component
      ON TechnicianCompensationRules(scope_type, COALESCE(scope_id,-1), compensation_component)
      WHERE is_active=1""")
