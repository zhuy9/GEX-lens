CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id UUID PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    valuation_at TIMESTAMPTZ NOT NULL,
    source_mode VARCHAR NOT NULL,
    raw_payload JSON NOT NULL,
    dashboard_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS option_quotes (
    snapshot_id UUID NOT NULL,
    symbol VARCHAR NOT NULL,
    expiration DATE NOT NULL,
    strike DECIMAL(18,6) NOT NULL,
    option_type VARCHAR NOT NULL,
    bid DOUBLE,
    ask DOUBLE,
    last DOUBLE,
    volume BIGINT,
    open_interest BIGINT,
    multiplier INTEGER NOT NULL,
    provider_contract_id VARCHAR,
    quote_asof TIMESTAMPTZ,
    mid DOUBLE,
    iv DOUBLE,
    gamma DOUBLE,
    exclusion_reason VARCHAR,
    flags JSON NOT NULL,
    PRIMARY KEY (snapshot_id, symbol, expiration, strike, option_type)
);
