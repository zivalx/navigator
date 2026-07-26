# Navigator API

Backend API for Navigator - A customizable personal finance hub consolidating assets, markets, news, and AI insights.

## Architecture

### Stack
- **FastAPI** - Modern async Python web framework
- **PostgreSQL** - Relational database for user data
- **Redis** - Caching layer for quotes and API responses
- **SQLAlchemy** - ORM for database operations
- **Pydantic** - Data validation and serialization

### Data Providers
- **Polygon.io** - Primary source for stock/ETF quotes
- **Finnhub** - Fallback for market data
- **CoinGecko** - Crypto prices (no API key needed)
- **Marketaux** - Financial news with sentiment
- **ExchangeRate-API** - Foreign exchange rates

### Key Features
- ✅ Real-time quotes with multi-provider fallback
- ✅ Redis caching to minimize API calls
- ✅ Multi-currency portfolio support with FX conversion
- ✅ News aggregation with sentiment analysis
- ✅ Watchlist management
- ✅ Portfolio P&L calculations
- 🔄 Background sync tasks (coming soon)
- 🔄 AI earnings summaries (coming soon)

## Setup

### Prerequisites
- Python 3.11+
- PostgreSQL 15+
- Redis 7+

### Option 1: Docker (Recommended)

1. **Copy environment file**
   ```bash
   cp .env.example .env
   ```

2. **Add your API keys to `.env`**
   ```bash
   POLYGON_API_KEY=your_key_here
   FINNHUB_API_KEY=your_key_here
   MARKETAUX_API_KEY=your_key_here
   ANTHROPIC_API_KEY=your_key_here
   ```

3. **Start services**
   ```bash
   docker-compose up -d
   ```

4. **API will be available at** `http://localhost:8000`
   - Docs: `http://localhost:8000/docs`
   - Health: `http://localhost:8000/health`

### Option 2: Local Development

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Start PostgreSQL and Redis**
   ```bash
   # macOS (Homebrew)
   brew services start postgresql@15
   brew services start redis

   # Linux
   sudo systemctl start postgresql
   sudo systemctl start redis
   ```

3. **Create database**
   ```bash
   createdb navigator
   ```

4. **Copy and configure .env**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

5. **Run the server**
   ```bash
   uvicorn app.main:app --reload
   ```

## API Endpoints

### Assets
- `GET /api/assets` - List all assets
- `GET /api/assets/{id}` - Get asset by ID
- `GET /api/assets/symbol/{symbol}` - Get asset by symbol
- `POST /api/assets` - Create new asset
- `PUT /api/assets/{id}` - Update asset
- `DELETE /api/assets/{id}` - Delete asset

### Portfolio
- `GET /api/portfolio/summary` - Portfolio summary (NAV, P&L)
- `GET /api/portfolio/holdings` - All holdings with prices
- `GET /api/portfolio/holdings/grouped` - Holdings grouped by asset
- `POST /api/portfolio/holdings` - Add new holding
- `DELETE /api/portfolio/holdings/{id}` - Remove holding
- `GET /api/portfolio/cash` - Cash balances
- `POST /api/portfolio/cash` - Add cash balance

### Watchlist
- `GET /api/watchlist` - All watchlists
- `GET /api/watchlist/{id}` - Get watchlist
- `POST /api/watchlist` - Create watchlist
- `DELETE /api/watchlist/{id}` - Delete watchlist
- `GET /api/watchlist/{id}/items` - Get watchlist items with prices
- `POST /api/watchlist/{id}/items` - Add item to watchlist
- `DELETE /api/watchlist/items/{id}` - Remove item

### Markets
- `GET /api/markets/quote/{symbol}` - Get quote for symbol
- `GET /api/markets/quotes?symbols=AAPL,GOOGL` - Get multiple quotes
- `GET /api/markets/movers/gainers` - Top gaining stocks
- `GET /api/markets/movers/losers` - Top losing stocks
- `GET /api/markets/indicators?keys=vix,sp500,...` - Market indicators strip: CNN/Crypto Fear & Greed, VIX, major indices, US 10Y/30Y yields, S&P 500 breadth (% above 50/200-day MA), DXY, gold, WTI, BTC. `keys` optional (comma-separated; omitted = all, unknown keys ignored). Never 500s — a failing indicator comes back with `value: null` and `error` set.

### News
- `GET /api/news` - Latest news
- `GET /api/news?asset_id={id}` - News for specific asset
- `GET /api/news/symbols/{symbol}` - News for symbol
- `GET /api/news/sync` - Trigger news sync

### Alerts

Price alerts / stop-loss rules, evaluated in the background every 5 minutes.
See `docs/superpowers/specs/2026-07-23-price-alerts-design.md` for the full design.

- `GET /api/alerts` - List all alerts, joined with asset symbol/name. Optional `?status=active|triggered|unacknowledged` filter.
- `POST /api/alerts` - Create an alert. Body: `{ assetId | symbol, rule: "price_below" | "price_above", threshold, note? }`. When `symbol` is given and no matching asset exists yet, a minimal asset record is created (best-effort enriched with currency from a live quote).
- `PUT /api/alerts/{id}` - Edit `rule` / `threshold` / `note`. Setting `isActive: true` reactivates a triggered alert, clearing `triggeredAt` / `triggeredPrice` / `acknowledgedAt` so it can fire again (one-shot re-arm).
- `DELETE /api/alerts/{id}` - Delete an alert.
- `POST /api/alerts/{id}/acknowledge` - Mark a triggered alert as acknowledged (idempotent).

**Evaluation**: an APScheduler job (`price_alert_evaluation`, every 5 minutes) loads all active alerts, batch-fetches quotes via `MarketDataService.get_quotes()` (riding the 60s quote cache), and triggers on `price <= threshold` for `price_below` or `price >= threshold` for `price_above` (boundary inclusive). A triggered alert gets `isActive=false`, `triggeredAt`, `triggeredPrice` set and won't fire again unless reactivated. Alerts are one-shot in v1 — no email/push, no %-change or indicator-based rules, no market-hours logic (crypto triggers 24/7; equities just won't move off-hours).

Example requests:
```bash
# Create a stop-loss by symbol (asset auto-created if unknown)
curl -X POST http://localhost:8000/api/alerts \
  -H "Content-Type: application/json" \
  -d '{"symbol": "AAPL", "rule": "price_below", "threshold": 180, "note": "stop loss"}'

# List unacknowledged (triggered but not yet seen) alerts
curl http://localhost:8000/api/alerts?status=unacknowledged

# Acknowledge one
curl -X POST http://localhost:8000/api/alerts/{id}/acknowledge

# Reactivate a triggered alert
curl -X PUT http://localhost:8000/api/alerts/{id} \
  -H "Content-Type: application/json" \
  -d '{"isActive": true}'
```

## Database Schema

### Core Tables
- `assets` - Stocks, ETFs, crypto, etc.
- `holding_lots` - Individual purchase lots
- `cash_balances` - Cash positions by account
- `watchlists` - User watchlists
- `watchlist_items` - Items in watchlists
- `price_snapshots` - Historical price data
- `fx_rates` - Foreign exchange rates
- `news_items` - News articles with sentiment
- `price_alerts` - Price alerts / stop-loss rules (see [Alerts](#alerts))

## Caching Strategy

### Quote Cache
- **TTL**: 60 seconds
- **Key**: `quote:{SYMBOL}`
- **Fallback**: Database price_snapshots

### News Cache
- **TTL**: 15 minutes
- **Key**: `news:{params_hash}`

### FX Cache
- **TTL**: 1 hour
- **Key**: `fx:{FROM}:{TO}`

### Indicators Cache
- **TTL**: 15 minutes (`indicators_cache_ttl`) for the two sentiment fetches (CNN, alternative.me); Yahoo-backed indicators ride the existing 60s quote cache
- **Key**: `indicators:{key}` (e.g. `indicators:fear_greed_stocks`)
- **Breadth (S5FI/S5TH)**: computed by a background scheduler job (never inline), cached in Redis under `breadth:s5fi` / `breadth:s5th` without expiry

## API Rate Limits

### Free Tier Limits (as of 2025)
- **Polygon.io**: 5 calls/min
- **Finnhub**: 60 calls/min
- **CoinGecko**: 10-50 calls/min (no key)
- **Marketaux**: 100 calls/day

### Mitigation
- Aggressive Redis caching
- Multi-provider fallback
- Database fallback for stale data

## Development

### Running Tests
```bash
pytest
```

### Database Migrations
```bash
# Create migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head
```

### Code Style
```bash
# Format code
black app/

# Lint
pylint app/
```

## Deployment

### Railway
```bash
railway login
railway init
railway up
```

### Fly.io
```bash
fly launch
fly deploy
```

### Environment Variables
See `.env.example` for all required configuration.

## Next Steps

1. **Background Tasks**
   - Periodic price sync (every 60s)
   - News sync (every 15min)
   - End-of-day portfolio snapshots

2. **AI Features**
   - Earnings call summaries (Claude Haiku)
   - Portfolio insights
   - Risk analysis

3. **Economic Calendar**
   - FRED API integration
   - FOMC meetings
   - Economic indicators

4. **Authentication**
   - Multi-user support
   - JWT tokens
   - User settings

## Troubleshooting

### Database connection errors
```bash
# Check PostgreSQL is running
pg_isready

# Check database exists
psql -l | grep navigator
```

### Redis connection errors
```bash
# Check Redis is running
redis-cli ping
# Should return: PONG
```

### API rate limit errors
- Check your API keys are valid
- Monitor cache hit rates
- Consider upgrading provider plans

## License

MIT
