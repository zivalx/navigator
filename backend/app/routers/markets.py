from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from ..services.market_data import MarketDataService
from ..services.indicators import IndicatorsService

router = APIRouter()


@router.get("/search")
async def search_assets(q: str, limit: int = 10, db: Session = Depends(get_db)):
    """Search for assets by name or ticker symbol (powered by Yahoo Finance)."""
    market_service = MarketDataService(db)
    results = await market_service.search_assets(q, limit=limit)
    return results


@router.get("/quote/{symbol}")
async def get_quote(symbol: str, db: Session = Depends(get_db)):
    """Get real-time quote for a symbol."""
    market_service = MarketDataService(db)
    try:
        quote = await market_service.get_quote(symbol)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return quote


@router.get("/quotes")
async def get_quotes(symbols: str, db: Session = Depends(get_db)):
    """Get quotes for multiple symbols (comma-separated)."""
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="No symbols provided")
    market_service = MarketDataService(db)
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


@router.get("/indicators")
async def get_indicators(keys: Optional[str] = Query(None)):
    """
    Get market indicators (sentiment, volatility, indices, rates, breadth,
    fx, commodities, crypto). Optional `keys` is a comma-separated list of
    indicator keys; unknown keys are silently ignored. Omitted/empty = all.

    Never 500s because one source is down — a failing indicator is returned
    with value=null and error set.
    """
    key_list = [k.strip() for k in keys.split(",") if k.strip()] if keys else None

    indicators_service = IndicatorsService()
    try:
        return await indicators_service.get_indicators(key_list)
    finally:
        await indicators_service.close()
