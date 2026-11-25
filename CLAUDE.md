# Navigator

Personal finance hub: portfolio tracking, real-time market data, news with sentiment,
and watchlists. Backend is FastAPI/Postgres/Redis; frontend is React/Vite/TypeScript.

## Repo layout

```
backend/app/
  routers/     # FastAPI endpoints (assets, portfolio, watchlist, markets, news, alerts, export, health)
  services/    # business logic (market_data, portfolio, fx, news, indicators, eod, sessions, health)
  providers/   # external API clients (yahoo, coingecko, polygon, finnhub,
               # alphavantage, cnn, alternative_me)
  tasks/       # APScheduler jobs (scheduler.py), leader election (leader.py), heartbeats (heartbeat.py)
  locks.py, migrate.py, legacy_schema.py   # advisory-lock keys, `python -m app.migrate`, legacy-DB adoption
  models/ schemas/  # SQLAlchemy models, Pydantic schemas
  config.py    # Settings (env-driven), database.py, cache.py, main.py
backend/alembic/versions/   # schema revisions (Alembic owns the Postgres schema)
backend/scripts/   # one-off ops scripts (audit_asset_currencies.py)
backend/seed.py, seed_watchlists.py   # sample data
frontend/src/
  pages/       # Dashboard (Index.tsx), Portfolio, Watchlist, Markets, Heatmap, Settings
  components/  # feature folders: dashboard, portfolio, watchlist, markets, heatmap, settings, layout, common, ui (shadcn)
  contexts/    # ThemeContext, PortfolioContext, MarketCardsContext
  hooks/ lib/  # useIndicatorPrefs, api.ts (fetch client), types.ts
docs/superpowers/specs/   # design docs for recent features (P&L, market indicators)
```

## Running locally

Backend (from `backend/`):
```bash
cp .env.example .env        # fill in API keys (all optional, see below)
docker-compose up -d        # postgres:15, redis:7, and the api container (entrypoint migrates first)
docker-compose exec api python seed.py          # sample assets/holdings
docker-compose exec api python seed_watchlists.py  # optional sample watchlists
```
This starts the API on **port 7000** (`docker-compose.yml` maps `7000:7000`; `Dockerfile`
CMD runs `uvicorn app.main:app --host 0.0.0.0 --port 7000`). Docs at
`http://localhost:7000/docs`, health at `/health` (shallow) and `/health/deep`.

**Alembic owns the Postgres schema.** `docker-entrypoint.sh` runs `python -m app.migrate`
before the app starts; a failed migration exits the container. `init_db()` does no DDL on
Postgres: it only raises if the DB revision is not the code's head (SQLite, used by tests,
still uses `create_all()`).

Local (non-Docker) alternative: `pip install -r requirements.txt`, run Postgres/Redis
yourself, then **`python -m app.migrate`** (re-run it after every pull that adds a
revision), `python seed.py` (needs the schema, so migrate first), then
`uvicorn app.main:app --reload --port 7000`.

Frontend (from `frontend/`):
```bash
npm install
npm run dev      # vite dev server on port 7070
```
Other scripts: `npm run build`, `npm run build:dev`, `npm run lint`, `npm run preview`.

Note: `README.md` and `BACKEND_SETUP.md` reference port 8000 / localhost:5173 —
that's stale. The actual configured ports (per `vite.config.ts` and
`backend/docker-compose.yml`) are **backend 7000, frontend 7070**.

## Frontend → backend wiring

`frontend/vite.config.ts` proxies `/api` to `http://localhost:7000`; the dev server
itself listens on port 7070. `frontend/src/lib/api.ts` calls relative paths
(`/api/...`) via a small `fetch` wrapper — no base URL config needed in the frontend.
`config.py`'s `cors_origins` includes `5173, 7070, 3000, 3002` for flexibility across
dev setups, but 7070 is what's actually wired up.

## Environment variables

All API keys are optional — the app works keyless using Yahoo Finance (quotes,
indices, search, movers) and CoinGecko (crypto) as the no-key defaults. Keys add
fallback providers / features, none are required to run:
- `POLYGON_API_KEY`, `FINNHUB_API_KEY`, `ALPHA_VANTAGE_API_KEY` — stock/ETF quote and
  movers fallbacks behind Yahoo
- `MARKETAUX_API_KEY` — news with sentiment (news router degrades without it)
- `EXCHANGERATE_API_KEY` — live FX rates
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` — reserved for future AI features, not yet wired into any router/service
- `DATABASE_URL`, `REDIS_URL` — default to the docker-compose service names/ports
- `SCHEDULER_ENABLED` — default `true`; `false` skips the scheduler leader loop entirely
  (this process runs no jobs), e.g. for a second API process or a one-off shell
- `EXPORT_TOKEN` — `X-Export-Token` for `GET /api/export/positions` (trender); empty = 503
- `CORS_ORIGINS` — comma-separated list, see `config.py`

See `backend/.env.example` for the full list and `backend/app/config.py` (`Settings`)
for defaults.

## Architecture conventions

- **router → service → provider**: routers (`app/routers/*.py`) stay thin — they parse
  params and call a service; services (`app/services/*.py`) hold business logic and
  orchestrate providers; providers (`app/providers/*.py`) are the only place that talk
  to external APIs.
- **Provider fallback chains** live in `services/market_data.py`: crypto tries
  CoinGecko then Yahoo; stocks/ETFs try Yahoo, then Polygon, then Finnhub, with
  Alpha Vantage used for movers. DB price snapshots are the last-resort fallback.
- **Redis caching (`app/cache.py`)** with TTLs from `config.py`: quotes 60s
  (`quote_cache_ttl`), news 15min (`news_cache_ttl`), FX 1hr (`fx_cache_ttl`),
  indicators 15min (`indicators_cache_ttl`).
- **Scheduler leader** (`tasks/leader.py`): only one process runs jobs. It holds the
  Postgres advisory lock `7400418` on a dedicated connection and pings it every 30 s,
  starting the scheduler on acquire and stopping it on loss. Failover bounds: the leader
  demotes within about 50 s of losing its connection (30 s interval + 2 x 10 s call
  timeout, `tcp_user_timeout` 20 s) and the server frees the lock at about 60 s (session
  keepalives), so the old leader always stops first. In-flight jobs can outlive demotion;
  idempotency (the EOD unique index, `SKIP LOCKED` on notifications) covers it.
  Non-Postgres URLs use an always-leader lock. Lock key registry (identical across the
  platform, never reuse): `7400417` Navigator init/migrate, `7400418` Navigator scheduler
  leader, `7400419` and `7400420` trender (see `app/locks.py`).
- **APScheduler jobs** (`tasks/scheduler.py`, `build_scheduler()`; defaults `coalesce`,
  `max_instances=1`, `misfire_grace_time=120`; every job is wrapped by a heartbeat that
  writes `job_heartbeats` and is skipped when this process is not the leader):

  | Job id (= heartbeat name) | Trigger | What it does |
  |---|---|---|
  | `scheduler_leader` | every 30 s (leader loop, not a job) | leader heartbeat |
  | `quote_refresh` | every 15 min (`QUOTE_REFRESH_INTERVAL_MINUTES`) | quotes for held/alerted assets whose exchange is in session; writes the price snapshots that prove freshness |
  | `price_alert_evaluation` | every 15 min (`ALERT_EVALUATION_INTERVAL_MINUTES`) | evaluates active alerts; sends Telegram notifications |
  | `eod_snapshot` | 16:30 America/New_York, daily | EOD close per held asset; today's portfolio row only when every held, priceable, in-session asset has today's row |
  | `breadth_snapshot` | 17:00 America/New_York, daily | S5FI/S5TH into Redis (no expiry) |
  | `eod_catchup` | 17:15 America/New_York, daily, and from `leader_startup` | fills missed EOD days from Yahoo daily bars, never overwriting (window: last row - 5 d, at least 30 d back, 183 d for new holdings; a 404 or `chart.error` is "no data", not a failure); then portfolio rows (skipped while `portfolio_snapshots` is empty), and earlier missing portfolio days insert-only |
  | `leader_startup` | once, 15 s after becoming leader | runs `eod_snapshot` (only if the US session is closed and its latest 16:30 slot was missed; writes only exchanges that are already closed), `eod_catchup`, `backfill_daily_snapshots_job` (only if `portfolio_snapshots` is empty), `breadth_warm_start` in order |

  `backfill_historical` no longer exists: `eod_catchup` and `leader_startup` replace it.
- **EOD rules** (`services/eod.py`, `services/sessions.py`): EOD eligibility is separate
  from alertability. An EOD row needs only a price, a usable timestamp and no error/stale/
  `db_cache` (a fund or a Polygon/Finnhub bar still counts), while `is_alertable` also needs
  a live source and a fresh market time. There is one EOD row per asset per trade day
  (partial unique index `uq_price_snapshots_eod` on `(asset_id, trade_date)`); reruns keep
  the earlier timestamp. `eod_backfill` rows are stamped at the session close (crypto 23:59:59
  UTC). Funds (no live source) report `not_monitored` and are never alertable. Holidays are
  not modelled; `old_market_time` is what they look like. `old_market_time` and `not_monitored`
  are neutral (logged at INFO, outside the failure rule). Known gap: a feed that returns a
  frozen `regularMarketTime` yields `old_market_time` (neutral) and pauses alerts silently;
  the freshness check does not catch it.
- **Health** (`routers/health.py`, `services/health.py`): `GET /health` is shallow
  liveness (no DB). `GET /health/deep` returns `{"status": "ok|degraded|down", "checks":
  {name: {"ok", "detail"}}}` covering db, migrations, scheduler leader, each job heartbeat,
  alert delivery and price freshness for US/TASE/crypto. HTTP 200 for ok/degraded, 503 for
  down (a critical check failed). Details are counts and ages only: no secrets, symbols or
  quantities.
- **Market indicators** are wired end to end: `GET /api/markets/indicators?keys=` ->
  `routers/markets.py` -> `services/indicators.py` (`IndicatorsService`) -> providers
  (`cnn.py`, `alternative_me.py`, Yahoo). Design doc:
  `docs/superpowers/specs/2026-07-23-market-indicators-design.md`. Breadth (S5FI/S5TH) is
  computed by the `breadth_snapshot` job into Redis. A failing indicator returns
  `value: null` plus `error`, never a 500.
- **Migrations**: new revisions go in `backend/alembic/versions/` with readable ids
  (`0003`, ...). `python -m app.migrate` runs under advisory lock `7400417`: empty schema ->
  `upgrade head`; legacy schema (tables but no `alembic_version`, i.e. old local DBs) ->
  converge, verify, stamp `0001`, upgrade; it refuses and stamps nothing if verification
  fails. The legacy-adopt path is for local/dev DBs only (production starts fresh at head).
  Adding a value to a Postgres enum needs `op.get_context().autocommit_block()` around
  `ALTER TYPE ... ADD VALUE`.
- **Deploy**: Navigator sits behind the shared edge Caddy (zivalx-ops), which owns TLS and
  basic auth. Navigator has neither; only `navigator-nginx` joins the external `edge`
  network. `scripts/deploy.sh` takes a pre-deploy dump first (skipped on a first deploy).

## Tests

Backend: run `pytest -q` from `backend/` (SQLite, no services needed). Postgres tests are
opt-in and skipped unless `TEST_POSTGRES_URL` is set: `test_pg_migrations.py`,
`test_pg_leader.py`, `test_pg_notify.py`, `test_enum_migration.py`. Each test gets its own
scratch schema. Use a throwaway container with a free port and remove it afterwards:
```bash
docker run -d --name nav-test-pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:15
TEST_POSTGRES_URL=postgresql://postgres:test@localhost:55432/postgres \
  .venv/bin/python -m pytest -q tests/test_pg_*.py tests/test_enum_migration.py
docker rm -f nav-test-pg
```
No frontend test setup was found.

## Gotchas

- **Yahoo yield tickers are ×10**: `^TNX` (US 10Y) and `^TYX` (US 30Y) come back from
  Yahoo scaled by 10. This is implemented in `services/indicators.py` (`"scale": 0.1`),
  not in `providers/yahoo.py`.
- **Yahoo minor units are normalized to major units** (ILA agorot, GBp/GBX pence, ZAc)
  at every Yahoo entry point via `services/currency_units.py` `normalize_quote_currency`.
  Quotes carry `unitFactor`; the quote cache key is `quote:v2:{SYMBOL}` (v2 so
  pre-normalization entries are ignored). Never price non-USD assets with USD-only fallbacks.
- **Enum values**: the currencies (ILS HKD CNY KRW TWD SGD INR SEK DKK NOK AUD CAD ZAR)
  and region IL are part of the `0001` baseline (legacy DBs get them from the frozen
  converge in `app/legacy_schema.py`). The `minor_units_cutover` marker in
  `maintenance_runs` is written by the baseline on fresh DBs and by the converge on legacy ones.
- **Cutover marker and paused alerts**: price alerts on minor-unit listings (or on stale
  quotes) created before the cutover are paused until the audit script's `--apply`
  writes `minor_unit_alerts_converted`. Holdings that can't be valued are flagged
  `valueUnavailable` + `unavailableReason` (fx_unavailable | lot_currency_check |
  price_unavailable) and excluded from totals; the EOD snapshot is skipped if a rate is missing.
- **Audit script + deploy order**: `docker compose exec api python -m scripts.audit_asset_currencies`
  is a dry run (prints symbols/codes/counts only); `--apply` fixes asset
  currency/region/exchange and converts pre-cutover alert/watch-target prices x0.01 once.
  It never changes lots. `--apply` exits 0 once its writes are committed and the
  conversion marker is written (or already present), even if some listings were skipped
  (they are listed as `skipped (not verified, not changed): ...`); it exits 1 and writes
  nothing if Yahoo was unreachable or a skipped listing still has legacy price rows, and
  exits 1 if there is no cutover marker (nothing could be converted).
  Deploy Navigator first -> audit dry run -> `--apply` -> then trender.
  Don't edit price alerts or watchlist targets between deploy and `--apply`; pre-cutover
  rows are converted ×0.01 by creation time.
  `POST /assets/` for stock/etf/fund derives currency/exchange/region from Yahoo (503 if
  unreachable, 422 unsupported currency). Spec: `docs/superpowers/specs/2026-11-17-tase-ils-support-design.md`.
- Ports: backend 7000, frontend dev 7070 (not the 8000/5173 in the older docs).
- Schema changes: add a revision under `backend/alembic/versions/` (see Migrations
  above); editing a model alone changes nothing on Postgres. SQLite tests still use
  `create_all()`.
- `/health/deep` returns 503 only when a critical check is down; the ops watchdog polls it.
  TASE close times in `services/sessions.py` (`_TASE_DAYS`) are unverified constants taken
  from the design spec; check them against tase.co.il before relying on them.
- Prod containers: `navigator-postgres`, `navigator-redis`, `navigator-api`,
  `navigator-frontend`, `navigator-nginx`. Basic auth used to be in Navigator's nginx; it now
  lives at the edge Caddy (zivalx-ops), and nginx has no `auth_basic`.
- `seed.py` populates sample assets/holdings (run `python -m app.migrate` first); `seed_watchlists.py` separately seeds
  categorized watchlists (Energy, Metals, etc.) by calling the running API at
  `http://localhost:7000/api` — run it after the API is up, not standalone.
