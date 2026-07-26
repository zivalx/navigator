# Navigator — Environment Variables

All backend variables are declared in `backend/app/config.py` (`class Settings(BaseSettings)`), loaded from a `.env` file in `backend/` (`Config.env_file = ".env"`, case-insensitive matching, so `DATABASE_URL` and `database_url` are equivalent). Two example files exist and drift slightly from each other and from `config.py` defaults — see notes under the table.

- `backend/.env.example` — local-dev template
- `.env.prod.example` (repo root) — production template, consumed by `docker-compose.prod.yml` via `env_file: .env.prod`

The frontend (`frontend/`) was checked for `import.meta.env` / `VITE_*` usage via `grep -r "VITE_\|import.meta.env" frontend/src` — **no matches found**. The frontend has no build-time environment variables today; in dev it talks to the backend purely through the Vite proxy (`frontend/vite.config.ts`: `/api` → `http://localhost:7000`), and in prod it's served as static files behind nginx, which proxies `/api/*` to the `api` container — so the frontend never needs to know a backend URL itself.

## Backend variables (`backend/app/config.py`)

| Variable | Required? | Default (in code) | What breaks or degrades without it |
|---|---|---|---|
| `APP_ENV` | Optional | `development` | Controls `database.py`'s SQLAlchemy `echo` flag (SQL logging on in `development`) and is set to `production` explicitly by `docker-compose.prod.yml`. Nothing else keys off it in the reviewed code. |
| `SECRET_KEY` | Optional (but should be set in prod) | `dev-secret-key-change-in-production` | Declared but **no code path in the reviewed backend reads `settings.secret_key`** (no JWT/session signing found) — currently a placeholder for future auth (`python-jose`/`passlib` are in `requirements.txt` but unused). Leaving the default is a no-op today, not a live security hole, but should still be changed before any auth work lands. |
| `CORS_ORIGINS` | Optional | `http://localhost:5173,http://localhost:7070,http://localhost:3000,http://localhost:3002` | Comma-separated list parsed by `cors_origins_list`; feeds `CORSMiddleware.allow_origins` in `main.py`. Without the right origin listed, the browser SPA gets CORS errors calling the API cross-origin (not an issue when nginx same-origin-proxies in prod). |
| `DATABASE_URL` | **Effectively required** | `postgresql://postgres:postgres@localhost:5432/navigator` | If Postgres isn't reachable at this URL, `init_db()` (`database.py`, called at startup in `main.py`'s lifespan) fails and the app won't start. Docker compose files override this to point at the `db`/`postgres` service names. |
| `REDIS_URL` | **Effectively required** | `redis://localhost:6379/0` | `cache.connect()` is awaited at startup (`main.py`); if Redis is unreachable this raises at startup. Even if it didn't, every quote/FX/movers cache lookup would fail — `Cache.get`/`set` guard on `self.redis` being set, but `connect()` itself isn't guarded, so an unreachable Redis is a hard startup failure, not a soft degrade. |
| `POLYGON_API_KEY` | Optional | `""` (falsy → provider disabled) | Without it, `MarketDataService.__init__` sets `self.polygon = None`, so Polygon is skipped in both the quote fallback chain and the movers fallback chain. App works fully keyless via Yahoo (stocks/ETFs) + CoinGecko (crypto) — Polygon is a rate-limit/data-quality upgrade, not a requirement. (`market_data.py`) |
| `FINNHUB_API_KEY` | Optional | `""` | Same pattern — `self.finnhub = None` if unset; Finnhub is the last-resort stock/ETF quote fallback after Yahoo and Polygon. Safe to omit. |
| `ALPHA_VANTAGE_API_KEY` | Optional | `""` | `self.alphavantage = None` if unset. Only used for the **movers** (gainers/losers) endpoint fallback chain (first choice there — one call returns both directions); quotes never use it. Without it, movers fall through to Polygon then Yahoo's curated list. |
| `MARKETAUX_API_KEY` | Optional, but **required for the news feature to do anything** | `""` | `NewsService.sync_news()` (`services/news.py`) checks this key first; if empty, it prints a warning and returns `0` immediately — no news is ever fetched. There is no fallback news provider. Existing news can still be *read* (`GET /api/news/*`) but the DB will stay empty without this key and a manual `POST /api/news/sync` call. |
| `EXCHANGERATE_API_KEY` | Optional | `""` | `FxService._fetch_rate` uses the keyed `v6.exchangerate-api.com` endpoint if set; otherwise falls back to the free, keyless `api.exchangerate-api.com/v4/latest/...` endpoint (lower rate limits, logs a `FALLBACK` warning each time). FX conversion still works keyless; this key just gets you a better-tier provider. |
| `ANTHROPIC_API_KEY` | Optional | `""` | Declared in `Settings` and `anthropic==0.42.0` is in `requirements.txt`, but a full-repo search found **no code that imports or calls the Anthropic SDK**. Currently unused — reserved for a future AI feature. |
| `OPENAI_API_KEY` | Optional | `""` | Same status as above — declared, dependency installed (`openai==1.59.5`), but not referenced anywhere in the reviewed backend code. Currently unused. |
| `QUOTE_CACHE_TTL` | Optional | `60` (seconds) | TTL for cached live quotes (`market_data.py`, key `quote:{SYMBOL}`). Lower = fresher but more provider calls; higher = staler but fewer calls/less fallback risk. |
| `NEWS_CACHE_TTL` | Optional | `900` (seconds) | TTL for cached news list responses (`routers/news.py`: `GET /api/news/`, `GET /api/news/symbols/{symbol}`). A successful `POST /api/news/sync` invalidates all cached lists immediately via a generation counter, so this TTL only bounds staleness between syncs. |
| `FX_CACHE_TTL` | Optional | `3600` (seconds) | TTL for cached FX rates (`fx.py`, key `fx:{FROM}:{TO}`). |
| `INDICATORS_CACHE_TTL` | Optional | `900` (seconds) | TTL for cached sentiment-indicator fetches (CNN Fear & Greed, alternative.me crypto Fear & Greed) in `services/indicators.py`, serving `GET /api/markets/indicators`. Yahoo-backed indicators (VIX, indices, rates, DXY, gold, WTI, BTC) ride the 60s quote cache instead; breadth values (s5fi/s5th) are stored without expiry by the breadth scheduler job. All indicator sources are keyless — no API key needed for this feature. |

### Variables used only by Docker Compose / infra (not in `Settings`)

These aren't read by `config.py` directly but are required by the compose files that inject `DATABASE_URL`/`REDIS_URL` for you:

| Variable | File | Purpose |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `docker-compose.prod.yml` | Postgres container credentials; also interpolated into the `DATABASE_URL` the compose file passes to `api`. |
| `REDIS_PASSWORD` | `docker-compose.prod.yml` | Redis `--requirepass`; interpolated into `REDIS_URL` for `api`. Dev compose (`backend/docker-compose.yml`) has no Redis password. |
| `DOMAIN` | `docker-compose.prod.yml`, `nginx/conf.d/default.conf.template` | `envsubst`'d into the nginx server config for TLS server_name / cert paths. |
| `CERTBOT_EMAIL` | `.env.prod.example` (referenced by `scripts/init-letsencrypt.sh`, not read in detail here) | Let's Encrypt registration contact. |

## Discrepancies between the two `.env*.example` files and `config.py`

- `backend/.env.example` omits `INDICATORS_CACHE_TTL` and `CORS_ORIGINS`'s dev-oriented multi-origin default (it does list `CORS_ORIGINS` with the same value, so that one's fine) — actually both example files are missing `INDICATORS_CACHE_TTL` entirely; only `config.py`'s in-code default (900) governs it unless you add the var yourself.
- `.env.prod.example` (root) is also missing `INDICATORS_CACHE_TTL`.
- Neither example file sets `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` to anything but blank/placeholder, consistent with them being unused today.

## Minimal working `.env` (backend/.env)

This is the smallest `.env` that runs Navigator fully keyless (Yahoo Finance for stocks/ETFs, CoinGecko for crypto, keyless ExchangeRate-API tier for FX). News sync will be a no-op, and Polygon/Finnhub/Alpha Vantage fallbacks won't be available (Yahoo alone must succeed):

```dotenv
# Database & Redis — match backend/docker-compose.yml service names/ports
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/navigator
REDIS_URL=redis://localhost:6379/0

# App
APP_ENV=development
SECRET_KEY=dev-secret-key-change-in-production
CORS_ORIGINS=http://localhost:5173,http://localhost:7070,http://localhost:3000,http://localhost:3002

# Everything below is optional — leave blank to run fully keyless.
POLYGON_API_KEY=
FINNHUB_API_KEY=
ALPHA_VANTAGE_API_KEY=
MARKETAUX_API_KEY=
EXCHANGERATE_API_KEY=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Cache TTLs — defaults shown, safe to omit entirely
QUOTE_CACHE_TTL=60
NEWS_CACHE_TTL=900
FX_CACHE_TTL=3600
INDICATORS_CACHE_TTL=900
```

To get full functionality (all fallbacks + real news), add real values for `POLYGON_API_KEY`, `FINNHUB_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `MARKETAUX_API_KEY`, and `EXCHANGERATE_API_KEY`.
