-- One Market may contain many Jobs; each Job may reference one Market.
ALTER TABLE Jobs
    ADD COLUMN market_id INTEGER REFERENCES Markets(market_id);

CREATE INDEX IF NOT EXISTS idx_Jobs_market_id
    ON Jobs(market_id);
