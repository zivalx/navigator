# Navigator

Personal finance hub: portfolio tracking, real-time market data, news with sentiment,
and watchlists. Backend is FastAPI/Postgres/Redis; frontend is React/Vite/TypeScript.

## Repo layout

```
backend/app/
  routers/     # FastAPI endpoints (assets, portfolio, watchlist, markets, news)
  services/    # business logic (market_data, portfolio, fx, news)
  providers/   # external API clients (yahoo, coingecko, polygon, finnhub,
               # alphavantage, cnn, alternative_me)
  tasks/       # APScheduler jobs (scheduler.py)
  models/ schemas/  # SQLAlchemy models, Pydantic schemas
  config.py    # Settings (env-driven), database.py, cache.py, main.py
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
docker-compose up -d        # postgres:15, redis:7, and the api container
docker-compose exec api python seed.py          # sample assets/holdings
docker-compose exec api python seed_watchlists.py  # optional sample watchlists
```
This starts the API on **port 7000** (`docker-compose.yml` maps `7000:7000`; `Dockerfile`
CMD runs `uvicorn app.main:app --host 0.0.0.0 --port 7000`). Docs at
`http://localhost:7000/docs`, health at `/health`. There is no Alembic migration
history yet despite being in `requirements.txt` — `database.py: init_db()` just calls
`Base.metadata.create_all()` on startup.

Local (non-Docker) alternative: `pip install -r requirements.txt`, run Postgres/Redis
yourself, `python seed.py`, then `uvicorn app.main:app --reload --port 7000`.

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
- **APScheduler jobs** (`app/tasks/scheduler.py`, started in `main.py` lifespan):
  - `eod_snapshot` — daily EOD price snapshot at 16:30 US/Eastern for every held
    asset; powers historical P&L.
  - `backfill_historical` — one-off, runs 15s after startup, backfills ~6 months of
    daily closes from Yahoo's chart API when fewer than 5 EOD snapshots exist.
- **Market indicators feature (in progress)**: design doc at
  `docs/superpowers/specs/2026-07-23-market-indicators-design.md`. Planned API surface
  is `GET /api/markets/indicators?keys=...`. As of this writing the frontend
  (`IndicatorsStrip`, `IndicatorTile`, `IndicatorsCustomizeDialog`, `FearGreedGauge`,
  `useIndicatorPrefs`, `lib/indicatorTypes.ts`) already calls this endpoint, and the
  providers it needs (`app/providers/cnn.py` for CNN Fear & Greed,
  `app/providers/alternative_me.py` for Crypto Fear & Greed) and
  `config.indicators_cache_ttl` exist, but there is no `/indicators` route in
  `app/routers/markets.py` and no `app/services/indicators.py` yet — check current
  state before assuming it's wired up end to end.

## Tests

Backend: ~200 tests in `backend/tests/`; run `pytest -q` from `backend/`. The Postgres
enum-migration test is opt-in: `TEST_POSTGRES_URL=... pytest tests/test_enum_migration.py`
(skipped otherwise). No frontend test setup was found.

## Gotchas

- **Yahoo yield tickers are ×10**: `^TNX` (US 10Y) and `^TYX` (US 30Y) come back from
  Yahoo scaled by 10 — divide value and change by 10 to get the real percentage. This
  isn't yet implemented anywhere in `providers/yahoo.py`; it's a documented requirement
  for the indicators work, not existing behavior.
- **Yahoo minor units are normalized to major units** (ILA agorot, GBp/GBX pence, ZAc)
  at every Yahoo entry point via `services/currency_units.py` `normalize_quote_currency`.
  Quotes carry `unitFactor`; the quote cache key is `quote:v2:{SYMBOL}` (v2 so
  pre-normalization entries are ignored). Never price non-USD assets with USD-only fallbacks.
- **Enum migration**: new currencies (ILS HKD CNY KRW TWD SGD INR SEK DKK NOK AUD CAD ZAR)
  and region IL are added to Postgres enums by `_migrate_enum_values` in `init_db()`
  (`ALTER TYPE ... ADD VALUE IF NOT EXISTS`). The whole Postgres init runs under a
  `pg_advisory_lock` so concurrent workers don't race; it also writes the
  `minor_units_cutover` marker into the `maintenance_runs` table.
- **Cutover marker and paused alerts**: price alerts on minor-unit listings (or on stale
  quotes) created before the cutover are paused until the audit script's `--apply`
  writes `minor_unit_alerts_converted`. Holdings that can't be valued are flagged
  `valueUnavailable` + `unavailableReason` (fx_unavailable | lot_currency_check |
  price_unavailable) and excluded from totals; the EOD snapshot is skipped if a rate is missing.
- **Audit script + deploy order**: `docker compose exec api python -m scripts.audit_asset_currencies`
  is a dry run (prints symbols/codes/counts only); `--apply` fixes asset
  currency/region/exchange and converts pre-cutover alert/watch-target prices x0.01 once.
  It never changes lots and exits 1 if a listing can't be verified and has legacy price
  rows. Deploy Navigator first -> audit dry run -> `--apply` -> then trender.
  `POST /assets/` for stock/etf/fund derives currency/exchange/region from Yahoo (503 if
  unreachable, 422 unsupported currency). Spec: `docs/superpowers/specs/2026-11-17-tase-ils-support-design.md`.
- Ports: backend 7000, frontend dev 7070 (not the 8000/5173 in the older docs).
- No Alembic migrations are actually applied/initialized — schema comes from
  `Base.metadata.create_all()`. If you add models, no migration step is needed locally.
- `seed.py` populates sample assets/holdings; `seed_watchlists.py` separately seeds
  categorized watchlists (Energy, Metals, etc.) by calling the running API at
  `http://localhost:7000/api` — run it after the API is up, not standalone.
