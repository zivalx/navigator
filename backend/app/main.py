from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from .config import settings
from .cache import cache
from .database import init_db


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

    yield

    # Shutdown
    await cache.disconnect()
    print("👋 Shutting down...")


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


@app.get("/health")
async def health():
    return {"status": "ok"}


# Import and include routers
from .routers import assets, portfolio, watchlist, markets, news

app.include_router(assets.router, prefix="/api/assets", tags=["assets"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
app.include_router(watchlist.router, prefix="/api/watchlist", tags=["watchlist"])
app.include_router(markets.router, prefix="/api/markets", tags=["markets"])
app.include_router(news.router, prefix="/api/news", tags=["news"])
