# Navigator Backend - Setup & Architecture Guide

## 🎉 What We Built

A production-ready FastAPI backend that powers your personal finance hub with:

### ✅ Core Features Implemented
1. **Multi-Provider Market Data**
   - Polygon.io (primary for stocks/ETFs)
   - Finnhub (fallback)
   - CoinGecko (crypto, no API key needed)
   - Smart fallback chain with caching

2. **Portfolio Management**
   - Multi-lot holdings tracking
   - Real-time P&L calculations
   - Multi-currency support with auto FX conversion
   - Grouped holdings by asset

3. **Watchlist System**
   - Multiple watchlists
   - Target prices
   - Notes per asset
   - Real-time price updates

4. **News Aggregation**
   - Marketaux integration
   - Sentiment scoring (-1 to +1)
   - Asset-specific news filtering
   - Auto-sync capability

5. **FX Management**
   - Live exchange rates (5 currencies: USD, EUR, GBP, JPY, CHF)
   - ExchangeRate-API integration
   - Caching to minimize API calls

6. **Caching Layer**
   - Redis for performance
   - Quote cache: 60s TTL
   - News cache: 15min TTL
   - FX cache: 1hr TTL
   - Database fallback when APIs fail

## 📁 Project Structure

```
backend/
├── app/
│   ├── models/              # SQLAlchemy ORM models
│   │   ├── asset.py         # Assets (stocks, ETFs, crypto)
│   │   ├── holding.py       # Holdings & cash balances
│   │   ├── watchlist.py     # Watchlists & items
│   │   ├── price.py         # Price snapshots & FX rates
│   │   └── news.py          # News items
│   │
│   ├── schemas/             # Pydantic schemas (match your TS types!)
│   │   ├── asset.py
│   │   ├── holding.py
│   │   ├── watchlist.py
│   │   ├── price.py
│   │   ├── news.py
│   │   ├── portfolio.py
│   │   └── market.py
│   │
│   ├── providers/           # API integrations
│   │   ├── base.py          # Abstract provider interface
│   │   ├── polygon.py       # Polygon.io
│   │   ├── finnhub.py       # Finnhub
│   │   └── coingecko.py     # CoinGecko
│   │
│   ├── services/            # Business logic
│   │   ├── market_data.py   # Quote fetching w/ fallback
│   │   ├── portfolio.py     # NAV, P&L calculations
│   │   ├── news.py          # News aggregation
│   │   └── fx.py            # Currency conversion
│   │
│   ├── routers/             # API endpoints
│   │   ├── assets.py        # CRUD for assets
│   │   ├── portfolio.py     # Holdings, summary
│   │   ├── watchlist.py     # Watchlist management
│   │   ├── markets.py       # Quotes, movers
│   │   └── news.py          # News endpoints
│   │
│   ├── config.py            # Settings from .env
│   ├── database.py          # SQLAlchemy setup
│   ├── cache.py             # Redis client
│   └── main.py              # FastAPI app
│
├── alembic/                 # Database migrations
├── tests/                   # Test suite (TODO)
├── Dockerfile               # Container image
├── docker-compose.yml       # Local dev stack
├── requirements.txt         # Python dependencies
├── seed.py                  # Database seed script
├── start.sh                 # Quick start script
└── README.md                # Documentation
```

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose (easiest)
- OR: Python 3.11+, PostgreSQL 15+, Redis 7+

### Step 1: Get API Keys (Free Tiers)

1. **Polygon.io** (5 calls/min free)
   - Sign up: https://polygon.io/
   - Get key from dashboard

2. **Finnhub** (60 calls/min free)
   - Sign up: https://finnhub.io/
   - Free API key available

3. **Marketaux** (100 calls/day free)
   - Sign up: https://www.marketaux.com/
   - Get API token

4. **ExchangeRate-API** (1500/month free)
   - Sign up: https://www.exchangerate-api.com/
   - Get API key

5. **Anthropic** (for AI summaries - optional)
   - Get key: https://console.anthropic.com/

### Step 2: Configure Environment

```bash
cd backend
cp .env.example .env
```

Edit `.env` and add your keys:
```bash
POLYGON_API_KEY=your_polygon_key
FINNHUB_API_KEY=your_finnhub_key
MARKETAUX_API_KEY=your_marketaux_key
EXCHANGERATE_API_KEY=your_exchangerate_key
ANTHROPIC_API_KEY=your_anthropic_key  # Optional
```

### Step 3: Start Services

**Option A: Docker (Recommended)**
```bash
docker-compose up -d
```

**Option B: Local**
```bash
# Install dependencies
pip install -r requirements.txt

# Start PostgreSQL and Redis
# (varies by OS - see README.md)

# Seed database
python seed.py

# Start server
uvicorn app.main:app --reload
```

### Step 4: Test the API

- **Health Check**: http://localhost:8000/health
- **API Docs**: http://localhost:8000/docs
- **Portfolio Summary**: http://localhost:8000/api/portfolio/summary

## 📊 API Examples

### Get Quote
```bash
curl http://localhost:8000/api/markets/quote/AAPL
```

Response:
```json
{
  "symbol": "AAPL",
  "price": 196.42,
  "change": 2.35,
  "changePercent": 1.21,
  "timestamp": "2025-01-12T10:30:00Z",
  "currency": "USD"
}
```

### Portfolio Summary
```bash
curl http://localhost:8000/api/portfolio/summary
```

### Add Holding
```bash
curl -X POST http://localhost:8000/api/portfolio/holdings \
  -H "Content-Type: application/json" \
  -d '{
    "assetId": "...",
    "quantity": 10,
    "avgCost": 150.00,
    "costCurrency": "USD",
    "accountName": "Robinhood",
    "purchaseDate": "2025-01-01T00:00:00Z"
  }'
```

### Sync News
```bash
curl http://localhost:8000/api/news/sync
```

## 🔧 Configuration

### Cache TTLs (in `.env`)
```bash
QUOTE_CACHE_TTL=60        # 1 minute
NEWS_CACHE_TTL=900        # 15 minutes
FX_CACHE_TTL=3600         # 1 hour
```

### Database URL
```bash
# Local
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/navigator

# Docker
DATABASE_URL=postgresql://postgres:postgres@db:5432/navigator
```

## 🎯 Next Steps to Complete

### Phase 1: Background Tasks (1-2 hours)
Add scheduled jobs with APScheduler:

```python
# app/tasks/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job('interval', seconds=60)
async def sync_prices():
    """Update prices for all holdings every 60s"""
    # ...

@scheduler.scheduled_job('interval', minutes=15)
async def sync_news():
    """Fetch new articles every 15min"""
    # ...
```

### Phase 2: AI Earnings Summaries (2-3 hours)
```python
# app/services/earnings.py
from anthropic import Anthropic

async def summarize_earnings(ticker: str, date: str):
    # 1. Fetch transcript from FMP or API Ninjas
    # 2. Send to Claude Haiku for summary
    # 3. Cache result in DB
    # Cost: ~$0.003 per transcript
```

### Phase 3: Economic Calendar (3-4 hours)
- FRED API for Fed data
- Calendar endpoints for FOMC, NFP, CPI, etc.
- Store upcoming events

### Phase 4: Authentication (4-6 hours)
- JWT tokens
- User registration/login
- Multi-user support
- User-specific portfolios

## 🐛 Troubleshooting

### "No data for symbol" errors
- Check API keys are valid
- Verify symbol is correct (uppercase)
- Check provider rate limits
- Look at cache: `redis-cli GET quote:AAPL`

### Database connection refused
```bash
# Check PostgreSQL is running
docker-compose ps
# or
pg_isready
```

### Redis errors
```bash
# Check Redis
docker-compose ps redis
# or
redis-cli ping
```

### API rate limit hit
- Check logs: `docker-compose logs -f api`
- Monitor cache hit rates
- Consider upgrading provider plans

## 💡 Architecture Decisions

### Why this stack?
| Choice | Reason |
|--------|--------|
| FastAPI | Fast, async, auto docs, type hints |
| PostgreSQL | Relational data, JSONB for flexibility |
| Redis | Fast caching, reduces API costs |
| Pydantic | Type safety matching TS frontend |
| SQLAlchemy | Mature ORM, migration support |

### Provider Selection Logic
```python
# For quotes:
if asset_type == "crypto":
    try CoinGecko (free, no key)
elif asset_type in ["stock", "etf"]:
    try Polygon → fallback Finnhub → fallback DB cache

# For news:
Marketaux (sentiment included)

# For FX:
ExchangeRate-API (free tier 1500/month)
```

### Caching Strategy
- **Why cache?** Free tier APIs have strict limits
- **How long?** Quotes 60s (balance freshness vs API calls)
- **Fallback?** Always try DB cache if all APIs fail

## 📈 Production Deployment

### Railway (Easiest)
```bash
railway login
railway init
railway up
```
- Auto Postgres & Redis
- $5/month for small apps

### Fly.io
```bash
fly launch
fly postgres create
fly redis create
fly deploy
```

### Environment for Production
```bash
APP_ENV=production
SECRET_KEY=<generate-strong-key>
DATABASE_URL=<production-db>
REDIS_URL=<production-redis>
```

## 🎓 Learning Resources

- **FastAPI**: https://fastapi.tiangolo.com/
- **SQLAlchemy**: https://docs.sqlalchemy.org/
- **Polygon.io**: https://polygon.io/docs/stocks
- **Finnhub**: https://finnhub.io/docs/api
- **Marketaux**: https://www.marketaux.com/documentation

## 📝 License

MIT - Feel free to use this for your personal finance tracking!
