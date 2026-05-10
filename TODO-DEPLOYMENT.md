# Navigator — Production Deployment Plan

## Context

Navigator is part of a broader market intelligence platform. The goal is to run Navigator
alongside future services (market parsers, trend detectors, opportunity identifiers) on a
single VPS, accessible via `zivalx.com` with password protection.

The system runs a few hours/day for active analysis, but the dashboard and data should be
always available.

---

## Phase 1: Production Docker Setup

### 1.1 Create `docker-compose.prod.yml`

A production-ready compose file (separate from the dev one) with:

- [ ] **PostgreSQL 15** — persistent volume, non-default credentials from env
- [ ] **Redis 7** — persistent volume, password-protected
- [ ] **Navigator API** — production uvicorn (no `--reload`), gunicorn with workers
- [ ] **Navigator Frontend** — built static files served by nginx (not Vite dev server)
- [ ] **nginx** — reverse proxy, SSL termination, serves frontend + proxies `/api` to backend
- [ ] Restart policies (`unless-stopped`) on all services
- [ ] Health checks on all services
- [ ] Resource limits (memory/CPU) to prevent runaway processes
- [ ] Shared Docker network for inter-service communication
- [ ] No ports exposed to host except 80/443 (nginx only)

### 1.2 Production Dockerfile for Frontend

- [ ] Multi-stage build: `npm run build` in builder stage, copy dist to nginx
- [ ] Minimal nginx-alpine image for serving

### 1.3 Production Backend Config

- [ ] Gunicorn with uvicorn workers (`gunicorn -w 2 -k uvicorn.workers.UvicornWorker`)
- [ ] `APP_ENV=production` disables debug, reload, etc.
- [ ] Secure `SECRET_KEY` from env (not default)
- [ ] Tighten CORS to `navigator.zivalx.com` only

### 1.4 nginx Configuration

- [ ] Reverse proxy: `/` serves frontend static files, `/api` proxies to backend:8000
- [ ] SSL with Let's Encrypt (certbot or Cloudflare origin certs)
- [ ] Gzip compression
- [ ] Security headers (X-Frame-Options, CSP, HSTS)
- [ ] Rate limiting at nginx level

---

## Phase 2: VPS Setup (Hetzner CX22)

### 2.1 Server Provisioning

- [ ] Sign up at hetzner.com, create CX22 instance (2 vCPU, 4GB RAM, 40GB, ~$5/mo)
- [ ] Choose location: Ashburn (US) or Falkenstein (EU) based on latency preference
- [ ] Set up SSH key access (no password login)
- [ ] Basic hardening: firewall (ufw), fail2ban, unattended-upgrades

### 2.2 Install Dependencies

- [ ] Docker + Docker Compose
- [ ] Certbot (if not using Cloudflare for SSL)

### 2.3 Deploy

- [ ] Clone repo to `/opt/navigator`
- [ ] Copy `.env.prod` with real credentials
- [ ] `docker-compose -f docker-compose.prod.yml up -d`
- [ ] Run migrations: `docker-compose exec api alembic upgrade head`
- [ ] Seed database: `docker-compose exec api python seed.py`
- [ ] Verify all services healthy

---

## Phase 3: Domain + SSL + Auth

### 3.1 DNS Setup

- [ ] In Cloudflare (assuming zivalx.com is there): add A record `navigator.zivalx.com` pointing to VPS IP
- [ ] Enable Cloudflare proxy (orange cloud) for DDoS protection

### 3.2 SSL

Two options (pick one):
- [ ] **Option A: Cloudflare Origin Cert** — generate in Cloudflare, install in nginx (simplest)
- [ ] **Option B: Let's Encrypt** — certbot with auto-renewal cron

### 3.3 Password Protection (Cloudflare Access)

- [ ] Go to Cloudflare Zero Trust > Access > Applications
- [ ] Add application: `navigator.zivalx.com`
- [ ] Policy: allow your email(s) only
- [ ] Auth method: one-time PIN to email (simplest) or Google OAuth
- [ ] This adds a login page in front of the app — zero code changes needed
- [ ] Free for up to 50 users

Alternative (no Cloudflare Access):
- [ ] nginx basic auth (`htpasswd`) — simpler but less elegant
- [ ] Add auth middleware to FastAPI — more work but app-level control

---

## Phase 4: Future Services Infrastructure

This is for when you add market analysis, trend detection, and opportunity parsing services.

### 4.1 Service Architecture

```
docker-compose.prod.yml
├── nginx              (reverse proxy, SSL)
├── navigator-api      (FastAPI backend)
├── navigator-frontend (static files via nginx)
├── postgres           (shared database)
├── redis              (shared cache + message broker)
├── market-analyzer    (future: scheduled market analysis)
├── trend-detector     (future: pattern recognition)
└── opportunity-parser (future: signal generation)
```

### 4.2 Scheduling Strategy

- [ ] APScheduler (already in requirements) for in-process scheduled jobs
- [ ] Or: separate worker containers triggered by cron
- [ ] Or: Redis-based task queue (Celery/ARQ) for more complex workflows
- [ ] Workers can be started/stopped on schedule to save resources

### 4.3 Shared Database Schema

- [ ] Future services share the same PostgreSQL instance
- [ ] Each service gets its own schema/tables via Alembic migrations
- [ ] Redis used for cross-service caching and pub/sub

### 4.4 Monitoring (Optional)

- [ ] Uptime Kuma (self-hosted, lightweight) for service health
- [ ] Docker logs with log rotation
- [ ] Cloudflare analytics for traffic

---

## Files to Create

| File | Purpose |
|------|---------|
| `docker-compose.prod.yml` | Production service definitions |
| `frontend/Dockerfile` | Multi-stage frontend build |
| `nginx/nginx.conf` | Reverse proxy + SSL config |
| `nginx/conf.d/navigator.conf` | Site-specific config |
| `.env.prod.example` | Production env template |
| `scripts/deploy.sh` | Deployment automation script |
| `scripts/backup.sh` | Database backup script |

---

## Cost Summary

| Item | Monthly Cost |
|------|-------------|
| Hetzner CX22 | ~$5 |
| Domain (zivalx.com) | Already owned |
| Cloudflare (DNS + Access) | Free |
| API keys (free tiers) | Free |
| **Total** | **~$5/mo** |
