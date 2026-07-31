"""Associate jobs with the market used by effective-dated revenue rules."""


def migrate(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(Jobs)")}
    if "market_id" not in columns:
        connection.execute(
            "ALTER TABLE Jobs ADD COLUMN market_id INTEGER REFERENCES Markets(market_id)"
        )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_Jobs_market_id ON Jobs(market_id)")
