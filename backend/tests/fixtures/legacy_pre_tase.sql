-- Navigator schema before the TASE / trailing-stop / maintenance-marker
-- changes: hand-derived from legacy_tase.sql (5 currencies, no IL region,
-- no INDEX asset type, no TRAILING_STOP rule, no alertintent, price_alerts
-- without trailing columns and threshold NOT NULL, no maintenance_runs, no
-- portfolio_snapshots, no cutover marker). Legacy fixture for migration tests.

CREATE TYPE alertrule AS ENUM (
    'PRICE_BELOW',
    'PRICE_ABOVE'
);

CREATE TYPE assettype AS ENUM (
    'STOCK',
    'ETF',
    'CRYPTO',
    'FUND',
    'OTHER'
);

CREATE TYPE currency AS ENUM (
    'USD',
    'EUR',
    'GBP',
    'JPY',
    'CHF'
);

CREATE TYPE marketregion AS ENUM (
    'US',
    'EU',
    'ASIA'
);

CREATE TABLE assets (
    id character varying NOT NULL,
    symbol character varying NOT NULL,
    name character varying NOT NULL,
    exchange character varying,
    currency currency NOT NULL,
    asset_type assettype NOT NULL,
    market_region marketregion NOT NULL,
    provider_ids json
);

CREATE TABLE cash_balances (
    id character varying NOT NULL,
    currency currency NOT NULL,
    amount double precision NOT NULL,
    account_name character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE fx_rates (
    id character varying NOT NULL,
    base_currency currency NOT NULL,
    quote_currency currency NOT NULL,
    rate double precision NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now()
);

CREATE TABLE holding_lots (
    id character varying NOT NULL,
    asset_id character varying NOT NULL,
    quantity double precision NOT NULL,
    avg_cost double precision NOT NULL,
    cost_currency currency NOT NULL,
    account_name character varying NOT NULL,
    tags character varying[],
    purchase_date timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE news_items (
    id character varying NOT NULL,
    asset_id character varying,
    title character varying NOT NULL,
    summary text NOT NULL,
    url character varying NOT NULL,
    publisher character varying NOT NULL,
    published_at timestamp with time zone NOT NULL,
    sentiment_score double precision NOT NULL,
    relevance_score double precision NOT NULL
);

CREATE TABLE price_alerts (
    id character varying NOT NULL,
    asset_id character varying NOT NULL,
    rule alertrule NOT NULL,
    threshold double precision NOT NULL,
    note text,
    is_active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    triggered_at timestamp with time zone,
    triggered_price double precision,
    acknowledged_at timestamp with time zone
);

CREATE TABLE price_snapshots (
    id character varying NOT NULL,
    asset_id character varying NOT NULL,
    price double precision NOT NULL,
    currency currency NOT NULL,
    "timestamp" timestamp with time zone NOT NULL,
    source character varying NOT NULL
);

CREATE TABLE watchlist_items (
    id character varying NOT NULL,
    watchlist_id character varying NOT NULL,
    asset_id character varying NOT NULL,
    notes text,
    target_price double precision,
    created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE watchlists (
    id character varying NOT NULL,
    name character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);

ALTER TABLE ONLY assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY cash_balances
    ADD CONSTRAINT cash_balances_pkey PRIMARY KEY (id);

ALTER TABLE ONLY fx_rates
    ADD CONSTRAINT fx_rates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY holding_lots
    ADD CONSTRAINT holding_lots_pkey PRIMARY KEY (id);

ALTER TABLE ONLY news_items
    ADD CONSTRAINT news_items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY news_items
    ADD CONSTRAINT news_items_url_key UNIQUE (url);

ALTER TABLE ONLY price_alerts
    ADD CONSTRAINT price_alerts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY price_snapshots
    ADD CONSTRAINT price_snapshots_pkey PRIMARY KEY (id);

ALTER TABLE ONLY watchlist_items
    ADD CONSTRAINT watchlist_items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY watchlists
    ADD CONSTRAINT watchlists_pkey PRIMARY KEY (id);

CREATE INDEX ix_assets_symbol ON assets USING btree (symbol);

CREATE INDEX ix_fx_currencies ON fx_rates USING btree (base_currency, quote_currency);

CREATE INDEX ix_news_asset_published ON news_items USING btree (asset_id, published_at);

CREATE INDEX ix_news_items_published_at ON news_items USING btree (published_at);

CREATE INDEX ix_news_published_at ON news_items USING btree (published_at);

CREATE INDEX ix_price_alerts_asset_active ON price_alerts USING btree (asset_id, is_active);

CREATE INDEX ix_price_alerts_asset_id ON price_alerts USING btree (asset_id);

CREATE INDEX ix_price_asset_timestamp ON price_snapshots USING btree (asset_id, "timestamp");

CREATE INDEX ix_price_snapshots_timestamp ON price_snapshots USING btree ("timestamp");

ALTER TABLE ONLY holding_lots
    ADD CONSTRAINT holding_lots_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE;

ALTER TABLE ONLY news_items
    ADD CONSTRAINT news_items_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL;

ALTER TABLE ONLY price_alerts
    ADD CONSTRAINT price_alerts_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE;

ALTER TABLE ONLY price_snapshots
    ADD CONSTRAINT price_snapshots_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE;

ALTER TABLE ONLY watchlist_items
    ADD CONSTRAINT watchlist_items_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE;

ALTER TABLE ONLY watchlist_items
    ADD CONSTRAINT watchlist_items_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE;
