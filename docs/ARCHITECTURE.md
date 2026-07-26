# Navigator — Architecture

> Generated from a direct pass over the codebase (2026-07-23). File paths are cited so claims can be re-verified against source. Where the code is ambiguous, that is called out explicitly rather than guessed.

## 1. System Overview

Two deployment topologies exist in the repo: a local dev topology (Vite dev server proxying to FastAPI directly) and a production topology (nginx in front of static frontend + FastAPI).

### Local development

```
┌─────────────┐        /api/*        ┌──────────────────┐
│  Browser    │ ───────────────────▶ │  Vite dev server │
│ (React SPA) │ ◀─────────────────── │  :7070           │
└─────────────┘                      └─────────┬────────┘
                                        proxy "/api" ──▶ http://localhost:7000
                                                │
                                                ▼
                                      ┌──────────────────┐
                                      │  FastAPI (uvicorn)│
                                      │  :7000            │
                                      └────┬─────────┬────┘
                                           │         │
                              ┌────────────┘         └────────────┐
                              ▼                                   ▼
                     ┌─────────────────┐               ┌─────────────────┐
                     │ PostgreSQL 15   │               │ Redis 7          │
                     │ :5432           │               │ :6379            │
                     └─────────────────┘               └─────────────────┘
                              │
                              ▼ (outbound HTTP, no auth needed for defaults)
              ┌───────────────────────────────────────────────────┐
              │ Yahoo Finance · CoinGecko · Polygon · Finnhub ·    │
              │ Alpha Vantage · Marketaux · ExchangeRate-API ·     │
              │ CNN Fear&Greed · alternative.me (crypto F&G)       │
              └───────────────────────────────────────────────────┘
```

Vite proxy config: `frontend/vite.config.ts` — dev server on port `7070`, proxies `/api` to `http://localhost:7000` with `changeOrigin: true`.

Local docker compose (`backend/docker-compose.yml`) only stands up `db` (Postgres), `redis`, and `api` (uvicorn with `--reload`, port `7000:7000`); the frontend is expected to run separately via `npm run dev`.

### Production (`docker-compose.prod.yml`, `nginx/`)

```
Internet :443/:80
      │
      ▼
┌───────────────────────────────────────────────────────────┐
│ nginx:1.25-alpine (SSL termination, rate limiting, gzip)   │
│  - :80  → 301 redirect to https (except /.well-known/*)   │
│  - :443 → TLS via Let's Encrypt (certbot shared volume)    │
│  - /api/*  → proxy_pass http://api:7000                   │
│  - /health → proxy_pass http://api:7000 (no rate limit)   │
│  - /*      → static SPA files from shared "frontend_build"│
│              volume, try_files → index.html                │
└───────┬───────────────────────────────┬────────────────────┘
        │                               │
        ▼                               ▼
┌────────────────────┐         ┌────────────────────────┐
│ frontend (build     │         │ api                     │
│ container, runs      │         │ Dockerfile.prod:        │
│ once, exits)         │         │ gunicorn + 2 uvicorn     │
│ outputs static build │         │ workers, :7000           │
│ to shared volume     │         └────────┬────────┬───────┘
└────────────────────┘                   │        │
                                          ▼        ▼
                               ┌──────────────┐ ┌──────────────┐
                               │ postgres:15   │ │ redis:7       │
                               │ (internal net)│ │ (password-    │
                               │               │ │ protected,    │
                               │               │ │ internal net) │
                               └──────────────┘ └──────────────┘

certbot container shares the /etc/letsencrypt and webroot volumes with
nginx and renews certs on a 12h sleep loop (docker-compose.prod.yml).
```

Notes on production topology:
- `postgres` and `redis` publish **no host ports** in prod compose — only reachable from other containers on the `navigator-net` bridge network. In dev compose, `db`/`redis` do publish `5432`/`6379` to the host.
- `redis` in prod requires a password (`--requirepass ${REDIS_PASSWORD}`); dev Redis has no password.
- The `api` container's healthcheck hits `/health` (`backend/app/main.py` defines this route trivially, returning `{"status": "ok"}`).
- nginx config lives at `nginx/nginx.conf` (main config, not read in detail here) and `nginx/conf.d/default.conf.template`, which is `envsubst`'d for `${DOMAIN}` at container start (see the nginx service `command:` in `docker-compose.prod.yml`).

## 2. Data Model

Source: `backend/app/models/*.py`, wired together in `backend/app/models/__init__.py`. ORM is SQLAlchemy; `Base.metadata.create_all()` is called from `backend/app/database.py:init_db()` at every app startup (see §6, "Migrations" caveat — there are no actual Alembic migrations in the repo despite `alembic` being a dependency).

### ASCII ERD

```
┌────────────────┐        ┌──────────────────────┐
│   watchlists    │ 1    N │   watchlist_items     │
│  id (PK)        │───────▶│  id (PK)               │
│  name           │        │  watchlist_id (FK)     │
│  created_at     │        │  asset_id (FK) ────────┼──┐
└────────────────┘        │  notes                 │  │
                            │  target_price          │  │
                            │  created_at            │  │
                            └──────────────────────┘  │
                                                        │
┌────────────────┐        ┌──────────────────────┐   │
│  holding_lots   │ N    1 │       assets          │◀──┘
│  id (PK)        │───────▶│  id (PK)               │
│  asset_id (FK)  │        │  symbol (indexed)      │
│  quantity       │        │  name                  │
│  avg_cost       │        │  exchange              │
│  cost_currency  │        │  currency (enum)       │
│  account_name   │        │  asset_type (enum)     │
│  tags[]         │        │  market_region (enum)  │
│  purchase_date  │        │  provider_ids (JSON)   │
│  created_at     │        └──────────┬─────┬───────┘
└────────────────┘                    │     │
                                        │     │ 1        N
                            1        N │     └────────▶┌──────────────────┐
                    ┌───────────────────┘               │   news_items      │
                    ▼                                   │  id (PK)          │
        ┌──────────────────────┐                        │  asset_id (FK,    │
        │   price_snapshots     │                        │   nullable,       │
        │  id (PK)              │                        │   ON DELETE SET   │
        │  asset_id (FK)        │                        │   NULL)           │
        │  price                │                        │  title / summary  │
        │  currency (enum)      │                        │  url (unique)     │
        │  timestamp (indexed)  │                        │  publisher        │
        │  source (yahoo/       │                        │  published_at     │
        │   polygon/finnhub/    │                        │   (indexed)       │
        │   eod_snapshot/       │                        │  sentiment_score  │
        │   eod_backfill/       │                        │  relevance_score  │
        │   db_cache/...)       │                        └──────────────────┘
        └──────────────────────┘

┌──────────────────┐            ┌────────────────────┐
│   fx_rates         │            │   cash_balances      │
│  id (PK)           │            │  id (PK)             │
│  base_currency(enum)│           │  currency (enum)     │
│  quote_currency(enum)│          │  amount              │
│  rate               │            │  account_name        │
│  timestamp          │            │  created_at/updated_at│
└──────────────────┘            └────────────────────┘
   (standalone table — no FK to assets/holdings)
```

Not drawn above: `price_alerts` (N→1 `assets`, `ON DELETE CASCADE`) — see the table below.

Table-by-table (file: `backend/app/models/…`):

| Table | Key fields | Relationships |
|---|---|---|
| `assets` (`asset.py`) | `id` (PK, string/UUID), `symbol` (indexed), `name`, `exchange`, `currency` (enum USD/EUR/GBP/JPY/CHF), `asset_type` (enum stock/etf/crypto/fund/other), `market_region` (enum US/EU/ASIA), `provider_ids` (JSON map e.g. `{"polygon":"AAPL"}`, largely unused today — see §4) | 1→N `holding_lots`, `watchlist_items`, `price_snapshots`, `news_items`; all four relationships have `cascade="all, delete-orphan"` (deleting an asset deletes its holdings/watchlist rows/prices/news) |
| `holding_lots` (`holding.py`) | `id` (PK), `asset_id` (FK → assets, `ON DELETE CASCADE`), `quantity`, `avg_cost`, `cost_currency` (enum), `account_name`, `tags` (Postgres `ARRAY(String)`), `purchase_date`, `created_at` | N→1 `assets` |
| `cash_balances` (`holding.py`) | `id` (PK), `currency` (enum), `amount`, `account_name`, `created_at`/`updated_at` | No FK to any other table — standalone per-account cash ledger |
| `watchlists` (`watchlist.py`) | `id` (PK), `name`, `created_at` | 1→N `watchlist_items`, cascade delete-orphan |
| `watchlist_items` (`watchlist.py`) | `id` (PK), `watchlist_id` (FK → watchlists, CASCADE), `asset_id` (FK → assets, CASCADE), `notes`, `target_price`, `created_at` | N→1 `watchlists`, N→1 `assets` |
| `price_snapshots` (`price.py`) | `id` (PK), `asset_id` (FK → assets, CASCADE), `price`, `currency` (enum), `timestamp` (indexed, and composite index `(asset_id, timestamp)`), `source` (free-text tag: `"yahoo"`, `"polygon"`, `"finnhub"`, `"coingecko"`, `"db_cache"`, `"eod_snapshot"`, `"eod_backfill"`) | N→1 `assets`. This table is the append-only time series used for both live-quote caching-to-DB and EOD/backfill history — see §3. |
| `fx_rates` (`price.py`) | `id` (PK), `base_currency` (enum), `quote_currency` (enum), `rate`, `timestamp` (server default now), composite index `(base_currency, quote_currency)` | No FK — pure lookup/history table, also append-only |
| `news_items` (`news.py`) | `id` (PK), `asset_id` (FK → assets, `ON DELETE SET NULL` — news can outlive its asset link), `title`, `summary` (Text), `url` (unique), `publisher`, `published_at` (indexed; plus composite `(asset_id, published_at)`), `sentiment_score` (−1..1), `relevance_score` (0..1) | N→1 `assets`, nullable |
| `price_alerts` (`alert.py`, new) | `id` (PK), `asset_id` (FK → assets, CASCADE, indexed), `rule` (enum `price_below`/`price_above`), `threshold`, `note`, `is_active` (default true), `created_at`, `triggered_at`, `triggered_price`, `acknowledged_at`; composite index `(asset_id, is_active)` | N→1 `assets` (one-sided relationship — `Asset` intentionally declares no back-reference). Evaluated every 5 minutes by the scheduler (§5); a triggered alert flips `is_active=False` and records `triggered_at`/`triggered_price`. |

Everything uses string primary keys populated with `str(uuid.uuid4())` at insert time in the service/router layer (SQLAlchemy models don't auto-generate IDs; e.g. `backend/app/services/portfolio.py`, `market_data.py`, `fx.py`, `tasks/scheduler.py` all call `uuid.uuid4()` explicitly).

## 3. P&L Calculation (from `backend/app/services/portfolio.py`)

`PortfolioService` is constructed per-request with `base_currency` defaulting to `Currency.USD`. All portfolio routes (`/summary`, `/history`, `/holdings`, `/holdings/grouped` in `backend/app/routers/portfolio.py`) now accept an optional `base_currency` query parameter (enum-validated: USD/EUR/GBP/JPY/CHF) and pass it through to the service.

### 3.1 Current holdings & unrealized P&L (`get_holdings_with_prices`, `get_grouped_holdings`)

Per lot (`get_holdings_with_prices`, lines 27–90):
1. Fetch the live quote for the lot's asset via `MarketDataService.get_quote(symbol, asset_type)` (provider fallback chain, §4).
2. Convert `current_price` from the quote's currency to `base_currency` via `FxService.convert(...)`.
3. Convert the lot's `avg_cost` from `cost_currency` to `base_currency` the same way.
4. `market_value = current_price_base * quantity`; `cost_basis = avg_cost_base * quantity`; `unrealized_pnl = market_value - cost_basis`; `unrealized_pnl_percent = unrealized_pnl / cost_basis * 100` (0 if `cost_basis` is falsy).
5. If the quote fetch throws, the lot is returned **without** price/P&L fields (caught and swallowed with a `print(...)`, lines 84–86) — no explicit null defaults are set beyond what the Pydantic schema defaults to.

`get_grouped_holdings` (lines 92–156) groups the above per-lot results by `asset_id`:
- Sums `total_quantity` across lots.
- Computes a **cost-weighted average cost** in base currency: `totalCost += cost_base * lot.quantity` for every lot, then `avg_cost = totalCost / totalQuantity`.
- Reuses the **first lot's** `current_price`/`price_change`/`price_change_percent` for the group (comment: "they should all be the same" — i.e., it assumes all lots of one asset were priced identically in the same request, which is true since one `get_quote` call is cached per symbol per request cycle).
- Recomputes `market_value`/`unrealized_pnl`/`unrealized_pnl_percent` at the group level from the weighted average cost.

### 3.2 Historical NAV lookup (`_get_nav_at_date`)

This is the single shared at-date valuation helper, used by both `get_summary()` and `get_nav_history()`. For a given `target_date`:
- **Lots purchased after `target_date` are excluded** — a lot only contributes to NAV from its `purchase_date` onward (naive datetimes are coerced to UTC via `_ensure_aware`).
- For each remaining `HoldingLot`, finds the **most recent `PriceSnapshot` with `timestamp <= target_date`** for that asset (ordered `desc`, `.first()`) — i.e., last-known-price-on-or-before, not an exact-date match.
- Converts that snapshot's price to `base_currency` via FX (falls back to the raw snapshot price if FX conversion throws).
- Sums `price_base * holding.quantity` across all included lots into `total_nav`.
- Returns `None` if **no** (already-purchased) holding had any snapshot on/before that date (`has_any_price` stays `False`) — this makes weekly/monthly P&L silently become `None` rather than 0 when history is thin, which is intentional per the spec doc.
- Accepts an optional pre-fetched `holdings` list so callers iterating many dates (the history endpoint) don't re-query `holding_lots` per date.

### 3.3 NAV history for the performance chart (`get_nav_history`, `GET /api/portfolio/history`)

`get_nav_history(period)` builds the dashboard time series (`period` ∈ 1w/1m/3m/6m/1y, mapped to 7/30/90/182/365 calendar days in `HISTORY_PERIOD_DAYS`):
- Collects the **distinct calendar dates** that have any `PriceSnapshot` for a currently held asset within the window (weekends/holidays are simply absent — no forward-fill).
- Values the portfolio at `23:59:59 UTC` of each such date via the shared `_get_nav_at_date` (so purchase-date exclusion applies here too).
- Each point carries `pnl`/`pnl_pct` relative to the **first point in the series**.
- The route (`routers/portfolio.py`) validates `period`, and caches the response in Redis for 300s under `portfolio:history:{period}:{base_currency}` (base currency is part of the key to avoid cross-contamination).

### 3.4 Portfolio summary / daily/weekly/monthly P&L (`get_summary`)

1. Computes `total_nav`/`total_cost` from live `get_grouped_holdings()` (current-moment values, not from snapshots).
2. `total_unrealized_pnl = total_nav - total_cost`, percent likewise.
3. **Daily P&L**: look up NAV at `now - 1 day` via `_get_nav_at_date`. If that returns `None` (e.g. weekend gap), retries at `now - 3 days`. If a valid `yesterday_nav > 0` is found: `daily_pnl = total_nav - yesterday_nav`, `daily_pnl_percent = daily_pnl / yesterday_nav * 100`. Otherwise both stay `0.0` (defaults set at top of the try block).
4. **Weekly P&L%**: NAV at `now - 7 days` (comment: "covers 5 trading days"); percent-only, no absolute weekly P&L field exists in the schema.
5. **Monthly P&L%**: NAV at `now - 30 days` ("covers ~21 trading days"); percent-only.
6. Any exception during the whole snapshot-based block is caught and logged as a warning (`logger.warning`), leaving whatever partial values were computed (daily defaults to 0/0, weekly/monthly default to `None`).
7. Returned as `PortfolioSummary` (`backend/app/schemas/portfolio.py`): `totalNav`, `baseCurrency`, `dailyPnL`, `dailyPnLPercent`, `weeklyPnLPercent` (optional), `monthlyPnLPercent` (optional), `totalCost`, `totalUnrealizedPnL`, `totalUnrealizedPnLPercent`, `lastUpdated`.

This whole EOD-snapshot approach (and the `_get_nav_at_date`/backfill machinery) matches the design in `docs/superpowers/specs/2026-07-03-pnl-and-deployment-design.md`, which describes exactly this as "Feature 1: Historical P&L from Daily Snapshots" — the spec's stated motivation was that `dailyPnL` used to be hardcoded to `0` and weekly/monthly were always `None`.

## 4. Provider Fallback Chain & Caching (`backend/app/services/market_data.py`, `fx.py`, `config.py`, `cache.py`)

`cache.py` wraps `redis.asyncio`, storing JSON-serialized values with `SETEX key ttl value`; passing `ttl=None` stores without expiry (plain `SET`) — used by the breadth service to persist daily-computed values until overwritten. Configurable TTLs come from `Settings` in `config.py`:

| Setting | Default | Used for |
|---|---|---|
| `quote_cache_ttl` | 60s | Live quotes (`market_data.py: cache.set(cache_key, quote, ttl=settings.quote_cache_ttl)`) |
| `news_cache_ttl` | 900s | News list responses in `routers/news.py` (`GET /api/news/` and `/api/news/symbols/{symbol}`). Keys embed a generation counter (`news:gen`, stored without expiry) that a successful `POST /api/news/sync` bumps, so fresh articles show up immediately instead of waiting out the TTL. |
| `fx_cache_ttl` | 3600s | FX rate cache (`fx.py: cache.set(cache_key, rate, ttl=settings.fx_cache_ttl)`) |
| `indicators_cache_ttl` | 900s | Sentiment indicator fetches (CNN Fear & Greed, alternative.me crypto F&G) in `services/indicators.py`; Yahoo-backed indicators ride the 60s quote cache instead |

Hardcoded TTLs outside `Settings`: movers (gainers/losers) use 1800s in `market_data.py` (`movers:{direction}` keys); historical-changes cache (`hist:{symbol}`) also 1800s; portfolio NAV history responses use 300s (`HISTORY_CACHE_TTL_SECONDS` in `routers/portfolio.py`); breadth values (`s5fi`/`s5th`) are stored with **no expiry** in `services/breadth.py` (overwritten by the daily job).

### 4.1 Quote fallback chain (`MarketDataService.get_quote`)

1. **Cache** (`quote:{SYMBOL}`) — return immediately if hit.
2. Asset type resolution: look up the `Asset` row by symbol in DB to get its real `asset_type`; default to `"stock"` if unknown/asset missing.
3. **Crypto assets**: try CoinGecko only (no key required).
4. **Stocks/ETFs**: try Yahoo Finance first (no key required, "always available" per comment).
5. If Yahoo failed (or asset isn't stock/etf and crypto path also failed) and `POLYGON_API_KEY` is set: try Polygon (logs a `FALLBACK` warning).
6. If still no quote and `FINNHUB_API_KEY` is set: try Finnhub (logs a `FALLBACK` warning). Note this branch has no `asset_type` guard, unlike Polygon's, so Finnhub is attempted even for crypto if CoinGecko failed.
7. If a quote was obtained: tag it with `source`, cache it (`quote_cache_ttl`), and persist a `PriceSnapshot` row (`_store_price_snapshot`, swallows errors and rolls back rather than failing the request).
8. If **all** providers failed: fall back to the **last stored `PriceSnapshot` in the DB** for the asset (resolved via the asset row looked up in step 2 — `PriceSnapshot.asset_id == asset.id`; returned with `source: "db_cache"` and zeroed change fields). If the symbol has no `Asset` row, this fallback is skipped. *(This path previously filtered by `asset_id == symbol` — ticker vs. UUID mismatch — and could never match; fixed during this documentation pass.)*
9. If even that fails: raises `ValueError` with all accumulated provider errors joined together.

Alpha Vantage (`alpha_vantage_api_key`) is not used in the quote path at all — only in the movers path (`_fetch_and_cache_movers`, §4.2). It's instantiated in `__init__` but never called from `get_quote`.

### 4.2 Movers fallback chain (`_fetch_and_cache_movers`)

1. Alpha Vantage (if key present) — single call returns both gainers and losers; cached separately as `movers:gainers` / `movers:losers` (1800s TTL).
2. Polygon (if key present) — per-direction calls.
3. Yahoo — curated/fallback list, no key required, always available as last resort.

### 4.3 FX fallback chain (`FxService.get_rate`)

1. Same currency → `1.0`, no lookup.
2. Cache (`fx:{FROM}:{TO}`) → return if hit.
3. Fetch live: if `EXCHANGERATE_API_KEY` set, call `v6.exchangerate-api.com` (paid/keyed tier, per-pair endpoint); else fall back to the free, keyless `api.exchangerate-api.com/v4/latest/{FROM}` endpoint (logs a `FALLBACK` warning about missing key).
4. On success: cache (`fx_cache_ttl`) and persist an `FxRate` row.
5. On failure: query the DB for the most recent stored `FxRate` for that exact pair; if none, try the **reverse** pair and invert the rate (`1.0 / reverse.rate`).
6. If nothing at all is found: raises `ValueError`.

### 4.4 News (`NewsService`, `backend/app/services/news.py`)

No fallback chain — single source, Marketaux, and it is **not** on the scheduler. `sync_news()` is only triggered by a manual `POST` to `/api/news/sync` (`backend/app/routers/news.py`). If `MARKETAUX_API_KEY` is unset, `sync_news()` no-ops (logs a warning, returns `0`) — the news feature is entirely disabled without that key, with no fallback provider.

Read endpoints are cached in Redis for `news_cache_ttl` (default 900s) at the router level, keyed by `news:{gen}:{asset_id|'all'}:{limit}` / `news:{gen}:sym:{SYMBOL}:{limit}`. A successful sync bumps the `news:gen` generation counter, invalidating all cached lists at once (the cache wrapper has no pattern-delete). Covered by `backend/tests/test_news_cache.py`.

## 5. Scheduler Jobs (`backend/app/tasks/scheduler.py`)

An `AsyncIOScheduler` (APScheduler) is started/stopped in the FastAPI `lifespan` (`backend/app/main.py`: `start_scheduler()` on startup, `shutdown_scheduler()` on shutdown). Five jobs are registered in `start_scheduler()`:

1. **`eod_snapshot`** — "EOD Price Snapshot", cron job at `16:30` `US/Eastern`, every day. Runs `snapshot_eod_prices()`:
   - Gets the distinct set of `asset_id`s across all `holding_lots`.
   - For each, fetches a live quote via `MarketDataService.get_quote` and inserts a `PriceSnapshot` with `source="eod_snapshot"`, `timestamp=now(UTC)`.
   - Per-asset failures are caught, rolled back, and logged; job continues for remaining assets and logs a final success/failure count.
2. **`breadth_snapshot`** — "Market Breadth Computation (S5FI/S5TH)", cron job at `17:00` `US/Eastern` (shortly after the EOD snapshot). Runs `services/breadth.py:compute_and_store_breadth()` — computes the % of S&P 500 constituents above their 50-day/200-day SMAs and stores the values in Redis **without expiry** (overwritten daily).
3. **`backfill_historical`** — "Historical Price Backfill", a one-shot `date` job scheduled for `now(UTC) + 15s` at every process startup (not cron — re-registered, and thus re-run, on every app restart). Runs `backfill_historical_prices()`:
   - Guard: counts existing `price_snapshots` rows where `source == "eod_snapshot"`; if `>= 5`, skips entirely (logged). This means the backfill only ever does real work in the first few days of a fresh deployment, or if EOD snapshots have been failing.
   - Otherwise, for each held asset, calls the Yahoo Finance chart endpoint directly via `httpx` (`https://query1.finance.yahoo.com/v8/finance/chart/{symbol}`, `range=6mo`, `interval=1d`, with a spoofed desktop `User-Agent`) and bulk-inserts one `PriceSnapshot` per daily close with `source="eod_backfill"`.
   - Per-asset failures are caught/rolled back/logged; does not use `MarketDataService` or its provider fallback — this is Yahoo-only, hardcoded.
4. **`breadth_warm_start`** — one-shot `date` job at `now(UTC) + 30s` after startup. Runs `breadth.ensure_breadth_warm()` — kicks off the (network-heavy, minutes-long) breadth computation only if the Redis cache is cold; a no-op otherwise.
5. **`price_alert_evaluation`** — "Price Alert Evaluation", `interval` job every **5 minutes**. Runs `evaluate_price_alerts()`:
   - Loads all `price_alerts` rows with `is_active=True`, batch-fetches quotes for their distinct symbols (riding the 60s quote cache).
   - Triggers alerts whose rule condition is met (boundary-inclusive: `price <= threshold` for `price_below`, `price >= threshold` for `price_above`), setting `is_active=False` and recording `triggered_at`/`triggered_price`. Triggered alerts don't fire again unless reactivated via the alerts API.

No news-sync or FX-refresh cron exists — news sync remains manual-only (`POST /api/news/sync`).

## 6. Backend Module Map (for orientation)

```
backend/app/
├── main.py            FastAPI app, lifespan (DB init, Redis connect, scheduler), CORS, routers
├── config.py           Settings (pydantic-settings, reads .env)
├── database.py         SQLAlchemy engine/session/Base, init_db() = create_all() (no Alembic runtime use)
├── cache.py             Redis async wrapper (get/set/delete/exists, JSON de/serialize)
├── models/              asset.py, holding.py, watchlist.py, price.py, news.py, alert.py  (see §2)
├── schemas/             Pydantic request/response models (holding.py, portfolio.py, asset.py, ...)
├── services/             portfolio.py, market_data.py, fx.py, news.py, indicators.py, breadth.py
├── providers/            base.py, yahoo.py, coingecko.py, polygon.py, finnhub.py,
│                         alphavantage.py, cnn.py, alternative_me.py
├── routers/              assets.py, portfolio.py, watchlist.py, markets.py, news.py, alerts.py
├── data/                 sp500_constituents fallback list (breadth computation)
└── tasks/                scheduler.py (APScheduler jobs)

backend/tests/            pytest suite (indicators providers/service/endpoint, breadth,
                          alerts, portfolio base-currency and history)
```

The market-indicators feature from `docs/superpowers/specs/2026-07-23-market-indicators-design.md` is implemented: `services/indicators.py` holds the v1 registry (sentiment gauges from CNN/alternative.me; VIX, indices, rates with the ^TNX/^TYX ÷10 scaling, DXY, gold, WTI, BTC via Yahoo; s5fi/s5th from the breadth service) and `routers/markets.py` exposes `GET /api/markets/indicators?keys=...` — per-indicator failures return `value: null` + `error` instead of a 500.

## 7. Notable Findings / Things To Double-Check

- **Base currency** (previously hardcoded to USD): now selectable per-request via the `base_currency` query parameter on all portfolio routes; USD remains the default.
- **Fixed during this pass**: `market_data.py`'s last-resort DB lookup used to query `PriceSnapshot.asset_id == symbol` (UUID vs. ticker mismatch — the fallback could never match a row). It now resolves through the asset row (`asset.id`); verified by the full test suite (60 passed).
- **Alpha Vantage key is unused for quotes**, only for the movers endpoint, despite being grouped under "Market Data" API keys generally.
- **`anthropic_api_key` / `openai_api_key`** are defined in `config.py` and listed in `requirements.txt` (`anthropic==0.42.0`, `openai==1.59.5`) but a repo-wide grep found **no code that imports or calls either SDK** — these are currently unused, reserved for a future AI feature.
- **Alembic is a listed dependency and `scripts/deploy.sh`'s spec calls `alembic upgrade head`**, but there is no `alembic/` migrations directory in `backend/`, and `database.py` uses `Base.metadata.create_all()` at every startup instead. Schema changes today rely on `create_all()` (additive only — it won't alter existing tables), not real migrations.
- **News sync has no scheduler job** — syncing is fully manual via `POST /api/news/sync`, and entirely inert without `MARKETAUX_API_KEY`. (The previously-dead `news_cache_ttl` config is now wired into the news read endpoints — fixed during this pass, see §4.4.)
- **Backfill job re-runs its 15-second-after-startup schedule on every process restart**, but its internal `>=5 eod_snapshot rows` guard makes repeated runs cheap no-ops once real EOD history accumulates.
- **Breadth values live only in Redis (no expiry)** — a Redis flush loses the s5fi/s5th history point used for day-over-day change until the next 17:00 ET job (the startup warm job re-computes if cold).
