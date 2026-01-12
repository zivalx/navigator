from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import uuid
from datetime import datetime

from ..database import get_db
from ..schemas import holding as schemas
from ..schemas.portfolio import PortfolioSummary, MarketMover
from ..models import holding as models
from ..models.asset import Asset
from ..services.portfolio import PortfolioService

router = APIRouter()


@router.get("/summary", response_model=PortfolioSummary)
async def get_portfolio_summary(db: Session = Depends(get_db)):
    """Get portfolio summary with NAV, PnL, etc."""
    portfolio_service = PortfolioService(db)
    return await portfolio_service.get_summary()


@router.get("/holdings", response_model=List[schemas.HoldingWithAsset])
async def get_holdings(db: Session = Depends(get_db)):
    """Get all holdings with current prices."""
    portfolio_service = PortfolioService(db)
    return await portfolio_service.get_holdings_with_prices()


@router.get("/holdings/grouped", response_model=List[schemas.GroupedHolding])
async def get_grouped_holdings(db: Session = Depends(get_db)):
    """Get holdings grouped by asset."""
    portfolio_service = PortfolioService(db)
    return await portfolio_service.get_grouped_holdings()


@router.post("/holdings", response_model=schemas.HoldingLot, status_code=201)
async def create_holding(
    holding_in: schemas.HoldingLotCreate,
    db: Session = Depends(get_db)
):
    """Add a new holding lot."""
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
