# Navigator

Personal finance hub — portfolio tracking, real-time market data, news with sentiment, and watchlists.

## Quick Start (Local Development)

### Prerequisites

- Docker Desktop (includes Docker Compose)
- Node.js 18+
- API keys (all have free tiers):
  - [Polygon.io](https://polygon.io/) — stock/ETF quotes
  - [Finnhub](https://finnhub.io/) — fallback quotes
  - [Marketaux](https://www.marketaux.com/) — financial news
  - [ExchangeRate-API](https://www.exchangerate-api.com/) — FX rates
  - CoinGecko — crypto (no key needed)

### 1. Start Backend

```bash
cd backend

# Create env file and add your API keys
cp .env.example .env
# Edit .env — fill in POLYGON_API_KEY, FINNHUB_API_KEY, MARKETAUX_API_KEY, EXCHANGERATE_API_KEY

# Start PostgreSQL, Redis, and the API
docker-compose up -d

# Wait for services to be healthy (~10 seconds), then seed the database
docker-compose exec api python seed.py
```

Verify: open http://localhost:7000/docs — you should see the Swagger UI.

### 2. Start Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:7070 — Navigator is running.

### 3. Add Your Real Holdings

The seed script creates sample data. To add your actual portfolio:

**Via the UI:** Use the Portfolio page to add holdings through the dialog.

**Via the API:**

```bash
# Step 1: Add an asset (if not already seeded)
curl -X POST http://localhost:7000/api/assets/ \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "JPM",
    "name": "JPMorgan Chase",
    "exchange": "NYSE",
    "currency": "USD",
    "asset_type": "stock",
    "market_region": "us",
    "provider_ids": {"polygon": "JPM"}
  }'

# Step 2: Add a holding (use the asset_id from step 1)
curl -X POST http://localhost:7000/api/portfolio/holdings \
  -H "Content-Type: application/json" \
  -d '{
    "asset_id": "<asset-id-from-above>",
    "quantity": 100,
    "avg_cost": 150.00,
    "cost_currency": "USD",
    "account_name": "Interactive Brokers",
    "tags": ["financials"]
  }'
```

### 4. Common Commands

```bash
# Backend
docker-compose up -d          # Start all services
docker-compose down            # Stop all services
docker-compose logs -f api     # Watch API logs
docker-compose exec api alembic upgrade head  # Run migrations

# Frontend
npm run dev                    # Dev server with hot reload
npm run build                  # Production build
```

## Project Structure

```
navigator/
├── backend/           # FastAPI + PostgreSQL + Redis
│   ├── app/
│   │   ├── models/    # SQLAlchemy models
│   │   ├── schemas/   # Pydantic schemas
│   │   ├── routers/   # API endpoints
│   │   ├── services/  # Business logic
│   │   └── providers/ # Market data providers (Polygon, Finnhub, CoinGecko)
│   ├── alembic/       # Database migrations
│   ├── seed.py        # Sample data seeder
│   └── docker-compose.yml
│
└── frontend/          # React + TypeScript + Tailwind
    └── src/
        ├── pages/     # Dashboard, Portfolio, Watchlist, Markets, Heatmap
        ├── components/
        ├── contexts/  # State management
        └── hooks/
```

## API Docs

With the backend running: http://localhost:7000/docs

See [backend/README.md](backend/README.md) for full API reference.

### Building behind a TLS-inspecting proxy (local only)

If your network re-signs HTTPS with a corporate root CA, image builds fail with `UnknownIssuer`. Pass the
public root certificate as an optional BuildKit secret (`corp_ca`); it is mounted only during the `pip install`
and `npm ci` steps and is never copied into an image layer, `ENV` or the repo:

```
CORP_CA=/path/to/root-ca.pem sh scripts/build-images.sh
# or: docker build --secret id=corp_ca,src=/path/to/root-ca.pem -f backend/Dockerfile.prod backend
```

Without `CORP_CA` (as in `scripts/deploy.sh` on the server) the build is unchanged. Certificate verification
is never disabled, and package-firewall blocks are not worked around.

## Deployment

See [TODO-DEPLOYMENT.md](TODO-DEPLOYMENT.md) for the production deployment plan (Hetzner VPS + Cloudflare).
