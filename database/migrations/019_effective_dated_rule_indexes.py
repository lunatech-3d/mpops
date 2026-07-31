"""Allow multiple non-overlapping active effective-dated revenue rules."""


def migrate(connection):
    connection.execute("DROP INDEX IF EXISTS ux_compensation_rule_active_scope_component")
    connection.execute("""CREATE INDEX IF NOT EXISTS idx_compensation_rules_scope_component_dates
        ON TechnicianCompensationRules(
            scope_type, scope_id, compensation_component, is_active,
            effective_from, effective_to
        )""")
    connection.execute("""CREATE INDEX IF NOT EXISTS idx_market_revenue_rules_resolution
        ON MarketRevenueShareRules(
            market_id, recipient_code, is_active, effective_from, effective_to
        )""")
