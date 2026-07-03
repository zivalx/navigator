# Navigator: Daily P&L + Production Deployment

**Date:** 2026-07-03
**Scope:** Two independent features to make Navigator production-ready.

---

## Feature 1: Historical P&L from Daily Snapshots

### Problem

`PortfolioSummary.dailyPnL` is hardcoded to 0. Weekly and monthly P&L are `None`. There's no reliable end-of-day price history.

### Solution

1. **APScheduler background job** runs daily at 16:30 ET (US market close). For each unique asset in `holding_lots`, it fetches a quote via `MarketDataService` and stores a `PriceSnapshot` row tagged with `source="eod_snapshot"`.

2. **Backfill on first run**: When the scheduler starts and finds fewer than 5 EOD snapshots total, it fetches 6-month historical daily closes from Yahoo Finance for each held asset and bulk-inserts them. This provides immediate weekly/monthly P&L without a 21-day wait.

3. **Portfolio service** gains a `_get_nav_at_date(target_date)` method:
   - For each held asset, finds the closest `PriceSnapshot` on or before `target_date`
   - Converts prices to base currency using FX rates
   - Returns total NAV at that date

4. **`get_summary()` updated**:
   - `dailyPnL` = live NAV - yesterday's EOD NAV
   - `dailyPnLPercent` = dailyPnL / yesterday's NAV * 100
   - `weeklyPnLPercent` = (live NAV - NAV 5 trading days ago) / NAV 5d ago * 100
   - `monthlyPnLPercent` = (live NAV - NAV 21 trading days ago) / NAV 21d ago * 100

### Files

| File | Change |
|------|--------|
| `backend/app/tasks/__init__.py` | New - empty |
| `backend/app/tasks/scheduler.py` | New - APScheduler setup + EOD snapshot job + backfill |
| `backend/app/services/portfolio.py` | Add `_get_nav_at_date()`, update `get_summary()` |
| `backend/app/main.py` | Start/stop scheduler in lifespan |

### No schema changes

`PriceSnapshot` already has all needed fields. EOD snapshots are distinguished by `source="eod_snapshot"`.

---

## Feature 2: Production Deployment

### Architecture

```
Internet :443 --> nginx (SSL termination)
                   |-- /* ---------> frontend static files (built into nginx image)
                   |-- /api/* -----> api:7000 (gunicorn + uvicorn workers)
                   |-- /.well-known -> certbot webroot

certbot <--> nginx (shared volumes: certs + webroot)
api --> postgres:5432 (internal network only)
api --> redis:6379 (internal network only)
```

### Files

| File | Purpose |
|------|---------|
| `docker-compose.prod.yml` | Production orchestration |
| `backend/Dockerfile.prod` | Gunicorn-based API image |
| `frontend/Dockerfile` | Multi-stage build: npm ci + build, serve via nginx |
| `nginx/nginx.conf` | Main nginx config |
| `nginx/conf.d/default.conf` | Site config: proxy, SSL, headers, gzip |
| `scripts/init-letsencrypt.sh` | First-time cert acquisition |
| `scripts/deploy.sh` | Pull, build, migrate, restart |
| `.env.prod.example` | Production env template |

### docker-compose.prod.yml services

- **postgres**: Postgres 15-alpine, persistent volume, credentials from env, internal network only, healthcheck
- **redis**: Redis 7-alpine, persistent volume, password from env, internal network only, healthcheck
- **api**: `Dockerfile.prod`, gunicorn with 2 uvicorn workers, `APP_ENV=production`, depends on postgres+redis, healthcheck on `/health`, memory limit 512MB
- **frontend**: `frontend/Dockerfile`, multi-stage build, outputs static files to shared volume
- **nginx**: nginx:alpine, ports 80+443, reverse proxy, SSL termination, security headers, gzip, rate limiting, shared volumes with certbot and frontend
- **certbot**: certbot image, webroot plugin, auto-renewal via entrypoint, shared volumes with nginx

All services: `restart: unless-stopped`, shared internal network `navigator-net`.

### nginx configuration

- HTTP :80 redirects to HTTPS (except `/.well-known/acme-challenge`)
- SSL with Let's Encrypt certs from certbot volume
- Security headers: X-Frame-Options DENY, X-Content-Type-Options nosniff, HSTS 1yr, Referrer-Policy, CSP
- Gzip on text/html, css, js, json, svg
- Rate limiting: 10 req/s burst 20 on `/api`
- Proxy `/api` to backend with proper headers (X-Real-IP, X-Forwarded-For/Proto)
- Serve frontend static with 1yr cache on assets, no-cache on index.html

### init-letsencrypt.sh

1. Download recommended TLS parameters
2. Create dummy self-signed cert so nginx can start
3. Start nginx
4. Delete dummy cert
5. Request real cert via certbot (webroot)
6. Reload nginx

### deploy.sh

1. `git pull`
2. `docker compose -f docker-compose.prod.yml build`
3. `docker compose -f docker-compose.prod.yml up -d`
4. `docker compose exec api alembic upgrade head`
5. Health check: curl localhost/health
6. Print status

---

## Out of scope

- Authentication (deferred)
- VPS provisioning/hardening (manual, one-time)
- Backup scripts
- Future services (market analyzer, trend detector)
- Alembic migration setup (existing `Base.metadata.create_all()` stays for now)
