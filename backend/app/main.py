from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from .config import settings
from .cache import cache
from .database import init_db
from .tasks import leader as scheduler_leader
from .logging_config import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown events."""
    # Startup
    print("🚀 Starting Navigator API...")

    # Initialize database
    init_db()
    print("✅ Database initialized")

    # Connect to Redis
    await cache.connect()
    print("✅ Redis connected")

    # Every worker runs the leader loop; only the advisory-lock holder runs jobs.
    leader = scheduler_leader.make_leader()
    if leader is not None:
        scheduler_leader.set_current(leader)
        await leader.start()
        print("Scheduler leader loop started")

    yield

    # Shutdown
    if leader is not None:
        await leader.stop()
        scheduler_leader.set_current(None)
    await cache.disconnect()
    print("Shutting down...")


app = FastAPI(
    title="Navigator API",
    description="Backend API for Navigator - Personal Finance Hub",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check endpoint
@app.get("/")
async def root():
    return {
        "name": "Navigator API",
        "version": "1.0.0",
        "status": "healthy",
    }


# Import and include routers
from .routers import assets, portfolio, watchlist, markets, news, alerts, export, health

app.include_router(health.router)
app.include_router(assets.router, prefix="/api/assets", tags=["assets"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
app.include_router(watchlist.router, prefix="/api/watchlist", tags=["watchlist"])
app.include_router(markets.router, prefix="/api/markets", tags=["markets"])
app.include_router(news.router, prefix="/api/news", tags=["news"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(export.router, prefix="/api/export", tags=["export"])
