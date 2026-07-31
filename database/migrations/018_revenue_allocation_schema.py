"""Add effective-dated revenue-share rules and immutable revenue allocations."""


TECHNICIAN_RULE_SQL = """
CREATE TABLE TechnicianCompensationRules (
    compensation_rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('Job','Technician','Market','System')),
    scope_id INTEGER,
    rule_type TEXT NOT NULL CHECK(rule_type IN ('Percentage','Flat Amount')),
    rule_value INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER REFERENCES Users(id),
    compensation_component TEXT NOT NULL DEFAULT 'Overall'
        CHECK(compensation_component IN ('Overall','Base','Travel','Off Hours')),
    effective_from TEXT,
    effective_to TEXT,
    CHECK((scope_type='System' AND scope_id IS NULL) OR
          (scope_type<>'System' AND scope_id IS NOT NULL)),
    CHECK((rule_type='Percentage' AND rule_value BETWEEN 0 AND 10000) OR
          (rule_type='Flat Amount' AND rule_value >= 0)),
    CHECK(effective_to IS NULL OR effective_to >= effective_from)
)"""

MARKET_RULE_SQL = """
CREATE TABLE MarketRevenueShareRules (
    market_revenue_share_rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id INTEGER NOT NULL,
    recipient_code TEXT NOT NULL CHECK(recipient_code IN ('LUNATECH_EAST')),
    share_basis_points INTEGER NOT NULL CHECK(share_basis_points BETWEEN 0 AND 10000),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER,
    updated_at TEXT,
    updated_by INTEGER,
    FOREIGN KEY (market_id) REFERENCES Markets(market_id),
    FOREIGN KEY (created_by) REFERENCES Users(id),
    FOREIGN KEY (updated_by) REFERENCES Users(id),
    CHECK(effective_to IS NULL OR effective_to >= effective_from)
)"""

ALLOCATION_SQL = """
CREATE TABLE CompanyRevenueAllocations (
    company_revenue_allocation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_item_id INTEGER NOT NULL,
    job_id INTEGER NOT NULL,
    market_id INTEGER NOT NULL,
    gross_revenue_cents INTEGER NOT NULL CHECK(gross_revenue_cents >= 0),
    technician_earning_id INTEGER,
    technician_share_basis_points INTEGER NOT NULL
        CHECK(technician_share_basis_points BETWEEN 0 AND 10000),
    technician_amount_cents INTEGER NOT NULL CHECK(technician_amount_cents >= 0),
    lunatech_east_share_basis_points INTEGER NOT NULL
        CHECK(lunatech_east_share_basis_points BETWEEN 0 AND 10000),
    lunatech_east_amount_cents INTEGER NOT NULL CHECK(lunatech_east_amount_cents >= 0),
    lunatech_share_basis_points INTEGER NOT NULL
        CHECK(lunatech_share_basis_points BETWEEN 0 AND 10000),
    lunatech_amount_cents INTEGER NOT NULL CHECK(lunatech_amount_cents >= 0),
    market_revenue_share_rule_id INTEGER,
    allocation_status TEXT NOT NULL DEFAULT 'Calculated'
        CHECK(allocation_status IN ('Calculated', 'Approved', 'Superseded')),
    calculation_details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER,
    approved_at TEXT,
    approved_by INTEGER,
    superseded_at TEXT,
    superseded_by INTEGER,
    superseded_reason TEXT,
    FOREIGN KEY (payment_item_id) REFERENCES MatterportPaymentItems(payment_item_id),
    FOREIGN KEY (job_id) REFERENCES Jobs(job_id),
    FOREIGN KEY (market_id) REFERENCES Markets(market_id),
    FOREIGN KEY (technician_earning_id)
        REFERENCES TechnicianJobEarnings(technician_earning_id),
    FOREIGN KEY (market_revenue_share_rule_id)
        REFERENCES MarketRevenueShareRules(market_revenue_share_rule_id),
    FOREIGN KEY (created_by) REFERENCES Users(id),
    FOREIGN KEY (approved_by) REFERENCES Users(id),
    FOREIGN KEY (superseded_by) REFERENCES Users(id),
    CHECK(technician_share_basis_points + lunatech_east_share_basis_points
          + lunatech_share_basis_points = 10000),
    CHECK(technician_amount_cents + lunatech_east_amount_cents
          + lunatech_amount_cents = gross_revenue_cents)
)"""

MARKET_COLUMNS = (
    "market_revenue_share_rule_id", "market_id", "recipient_code", "share_basis_points",
    "effective_from", "effective_to", "is_active", "notes", "created_at", "created_by",
    "updated_at", "updated_by",
)
ALLOCATION_COLUMNS = (
    "company_revenue_allocation_id", "payment_item_id", "job_id", "market_id",
    "gross_revenue_cents", "technician_earning_id", "technician_share_basis_points",
    "technician_amount_cents", "lunatech_east_share_basis_points",
    "lunatech_east_amount_cents", "lunatech_share_basis_points", "lunatech_amount_cents",
    "market_revenue_share_rule_id", "allocation_status", "calculation_details_json",
    "created_at", "created_by", "approved_at", "approved_by", "superseded_at",
    "superseded_by", "superseded_reason",
)


def _table_exists(connection, name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(connection, table):
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _copy(connection, old_table, new_table, columns, defaults, required):
    available = _columns(connection, old_table)
    missing = sorted(required - available)
    if missing and connection.execute(f'SELECT 1 FROM "{old_table}" LIMIT 1').fetchone():
        raise RuntimeError(
            f"cannot upgrade {new_table}: populated prototype is missing required "
            f"column(s): {', '.join(missing)}"
        )
    targets = []
    expressions = []
    for column in columns:
        if column in available:
            targets.append(f'"{column}"')
            expressions.append(f'"{column}"')
        elif column in defaults:
            targets.append(f'"{column}"')
            expressions.append(defaults[column])
    if not targets:
        return
    try:
        connection.execute(
            f'INSERT INTO "{new_table}" ({", ".join(targets)}) '
            f'SELECT {", ".join(expressions)} FROM "{old_table}"'
        )
    except Exception as error:
        raise RuntimeError(
            f"cannot upgrade {new_table}: prototype data violates the final schema: {error}"
        ) from error


def _rebuild_technician_rules(connection):
    columns = _columns(connection, "TechnicianCompensationRules")
    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='TechnicianCompensationRules'"
    ).fetchone()[0]
    if {"effective_from", "effective_to"} <= columns and "effective_to >= effective_from" in sql:
        return
    connection.execute("ALTER TABLE TechnicianCompensationRules RENAME TO _018_old_compensation_rules")
    connection.execute(TECHNICIAN_RULE_SQL)
    rule_columns = (
        "compensation_rule_id", "scope_type", "scope_id", "rule_type", "rule_value",
        "is_active", "created_at", "created_by", "compensation_component",
        "effective_from", "effective_to",
    )
    _copy(
        connection, "_018_old_compensation_rules", "TechnicianCompensationRules",
        rule_columns, {"compensation_component": "'Overall'"},
        {"compensation_rule_id", "scope_type", "scope_id", "rule_type", "rule_value"},
    )
    connection.execute("DROP TABLE _018_old_compensation_rules")
    connection.execute("""CREATE UNIQUE INDEX ux_compensation_rule_active_scope_component
        ON TechnicianCompensationRules(scope_type, COALESCE(scope_id,-1), compensation_component)
        WHERE is_active=1""")


def _prepare_old_table(connection, table, old_table):
    if _table_exists(connection, old_table):
        raise RuntimeError(f"cannot upgrade {table}: leftover migration table {old_table} exists")
    if _table_exists(connection, table):
        connection.execute(f'ALTER TABLE "{table}" RENAME TO "{old_table}"')
        return True
    return False


def migrate(connection):
    _rebuild_technician_rules(connection)

    # Rename both prototypes first. This prevents SQLite from rewriting an existing
    # allocation foreign key to point at the temporary market-rule table name.
    old_allocations = _prepare_old_table(
        connection, "CompanyRevenueAllocations", "_018_old_company_allocations"
    )
    old_market_rules = _prepare_old_table(
        connection, "MarketRevenueShareRules", "_018_old_market_rules"
    )

    connection.execute(MARKET_RULE_SQL)
    if old_market_rules:
        _copy(
            connection, "_018_old_market_rules", "MarketRevenueShareRules", MARKET_COLUMNS,
            {"recipient_code": "'LUNATECH_EAST'", "is_active": "1",
             "created_at": "CURRENT_TIMESTAMP"},
            {"market_revenue_share_rule_id", "market_id", "share_basis_points", "effective_from"},
        )
        connection.execute("DROP TABLE _018_old_market_rules")

    connection.execute(ALLOCATION_SQL)
    if old_allocations:
        _copy(
            connection, "_018_old_company_allocations", "CompanyRevenueAllocations",
            ALLOCATION_COLUMNS,
            {"allocation_status": "'Calculated'", "calculation_details_json": "'{}'",
             "created_at": "CURRENT_TIMESTAMP"},
            {"company_revenue_allocation_id", "payment_item_id", "job_id", "market_id",
             "gross_revenue_cents", "technician_share_basis_points",
             "technician_amount_cents", "lunatech_east_share_basis_points",
             "lunatech_east_amount_cents", "lunatech_share_basis_points",
             "lunatech_amount_cents"},
        )
        connection.execute("DROP TABLE _018_old_company_allocations")

    connection.execute("""CREATE INDEX idx_market_revenue_rules_market_dates
        ON MarketRevenueShareRules(market_id, recipient_code, effective_from, effective_to)""")
    connection.execute("CREATE INDEX idx_company_allocations_payment_item ON CompanyRevenueAllocations(payment_item_id)")
    connection.execute("CREATE INDEX idx_company_allocations_job ON CompanyRevenueAllocations(job_id)")
    connection.execute("CREATE INDEX idx_company_allocations_market ON CompanyRevenueAllocations(market_id)")
    connection.execute("CREATE INDEX idx_company_allocations_status ON CompanyRevenueAllocations(allocation_status)")
    connection.execute("""CREATE UNIQUE INDEX ux_current_company_allocation
        ON CompanyRevenueAllocations(payment_item_id)
        WHERE allocation_status IN ('Calculated', 'Approved')""")
