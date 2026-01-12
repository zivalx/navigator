from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from ..services.market_data import MarketDataService

router = APIRouter()


@router.get("/quote/{symbol}")
async def get_quote(symbol: str, db: Session = Depends(get_db)):
    """Get real-time quote for a symbol."""
    market_service = MarketDataService(db)
    quote = await market_service.get_quote(symbol)
    return quote


@router.get("/quotes")
async def get_quotes(symbols: str, db: Session = Depends(get_db)):
    """Get quotes for multiple symbols (comma-separated)."""
    market_service = MarketDataService(db)
    symbol_list = [s.strip().upper() for s in symbols.split(",")]
    quotes = await market_service.get_quotes(symbol_list)
    return quotes


@router.get("/movers/gainers")
async def get_top_gainers(
    limit: int = 10,
    region: Optional[str] = "US",
    db: Session = Depends(get_db)
):
    """Get top gaining stocks."""
    market_service = MarketDataService(db)
    return await market_service.get_top_gainers(limit, region)


@router.get("/movers/losers")
async def get_top_losers(
    limit: int = 10,
    region: Optional[str] = "US",
    db: Session = Depends(get_db)
):
    """Get top losing stocks."""
    market_service = MarketDataService(db)
    return await market_service.get_top_losers(limit, region)
