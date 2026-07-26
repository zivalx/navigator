from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import uuid
from datetime import datetime

from ..database import get_db
from ..schemas import holding as schemas
from ..schemas.asset import Currency
from ..schemas.portfolio import PortfolioSummary, MarketMover
from ..models import holding as models
from ..models.asset import Asset
from ..services.portfolio import PortfolioService
from ..cache import cache

router = APIRouter()

# NAV history responses are cached per-period since they're driven by
# once-daily EOD snapshots - a 5 minute TTL is plenty fresh.
HISTORY_CACHE_TTL_SECONDS = 300
VALID_HISTORY_PERIODS = {"1w", "1m", "3m", "6m", "1y"}


@router.get("/summary", response_model=PortfolioSummary)
async def get_portfolio_summary(
    base_currency: Currency = Currency.USD,
    db: Session = Depends(get_db),
):
    """Get portfolio summary with NAV, PnL, etc."""
    portfolio_service = PortfolioService(db, base_currency=base_currency)
    return await portfolio_service.get_summary()


@router.get("/history")
async def get_portfolio_history(
    period: str = "3m",
    base_currency: Currency = Currency.USD,
    db: Session = Depends(get_db),
):
    """Get the portfolio NAV time series for the dashboard performance chart."""
    if period not in VALID_HISTORY_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid period '{period}'. Must be one of {sorted(VALID_HISTORY_PERIODS)}",
        )

    # base_currency is part of the cache key so currencies don't cross-contaminate.
    cache_key = f"portfolio:history:{period}:{base_currency.value}"
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    portfolio_service = PortfolioService(db, base_currency=base_currency)
    result = await portfolio_service.get_nav_history(period)

    await cache.set(cache_key, result, ttl=HISTORY_CACHE_TTL_SECONDS)
    return result


@router.get("/holdings", response_model=List[schemas.HoldingWithAsset])
async def get_holdings(
    base_currency: Currency = Currency.USD,
    db: Session = Depends(get_db),
):
    """Get all holdings with current prices."""
    portfolio_service = PortfolioService(db, base_currency=base_currency)
    return await portfolio_service.get_holdings_with_prices()


@router.get("/holdings/grouped", response_model=List[schemas.GroupedHolding])
async def get_grouped_holdings(
    base_currency: Currency = Currency.USD,
    db: Session = Depends(get_db),
):
    """Get holdings grouped by asset."""
    portfolio_service = PortfolioService(db, base_currency=base_currency)
    return await portfolio_service.get_grouped_holdings()


@router.post("/holdings", response_model=schemas.HoldingLot, status_code=201)
async def create_holding(
    holding_in: schemas.HoldingLotCreate,
    db: Session = Depends(get_db)
):
    """Add a new holding lot."""
    if holding_in.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive")
    if holding_in.avg_cost < 0:
        raise HTTPException(status_code=400, detail="Average cost cannot be negative")

    # Verify asset exists
    asset = db.query(Asset).filter(Asset.id == holding_in.asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    holding = models.HoldingLot(
        id=str(uuid.uuid4()),
        asset_id=holding_in.asset_id,
        quantity=holding_in.quantity,
        avg_cost=holding_in.avg_cost,
        cost_currency=holding_in.cost_currency,
        account_name=holding_in.account_name,
        tags=holding_in.tags,
        purchase_date=holding_in.purchase_date,
    )
    db.add(holding)
    db.commit()
    db.refresh(holding)
    return holding


@router.put("/holdings/{holding_id}", response_model=schemas.HoldingLot)
async def update_holding(
    holding_id: str,
    holding_in: schemas.HoldingLotUpdate,
    db: Session = Depends(get_db)
):
    """Update a holding lot (quantity, cost, date, account, tags)."""
    holding = db.query(models.HoldingLot).filter(
        models.HoldingLot.id == holding_id
    ).first()
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")

    update_data = holding_in.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    if "quantity" in update_data and update_data["quantity"] <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive")

    if "avg_cost" in update_data and update_data["avg_cost"] < 0:
        raise HTTPException(status_code=400, detail="Average cost cannot be negative")

    for field, value in update_data.items():
        setattr(holding, field, value)

    db.commit()
    db.refresh(holding)
    return holding


@router.delete("/holdings/{holding_id}", status_code=204)
async def delete_holding(holding_id: str, db: Session = Depends(get_db)):
    """Delete a holding lot."""
    holding = db.query(models.HoldingLot).filter(
        models.HoldingLot.id == holding_id
    ).first()
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")

    db.delete(holding)
    db.commit()
    return None


@router.get("/cash", response_model=List[schemas.CashBalance])
async def get_cash_balances(db: Session = Depends(get_db)):
    """Get all cash balances."""
    balances = db.query(models.CashBalance).all()
    return balances


@router.post("/cash", response_model=schemas.CashBalance, status_code=201)
async def create_cash_balance(
    balance_in: schemas.CashBalanceCreate,
    db: Session = Depends(get_db)
):
    """Add or update cash balance."""
    if balance_in.amount < 0:
        raise HTTPException(status_code=400, detail="Amount cannot be negative")

    balance = models.CashBalance(
        id=str(uuid.uuid4()),
        currency=balance_in.currency,
        amount=balance_in.amount,
        account_name=balance_in.account_name,
    )
    db.add(balance)
    db.commit()
    db.refresh(balance)
    return balance
