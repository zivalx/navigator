import logging
from typing import List, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone, time as dt_time, date as date_cls

from ..models.holding import HoldingLot
from ..models.asset import Asset
from ..models.price import PriceSnapshot
from ..schemas.holding import HoldingWithAsset, GroupedHolding
from ..schemas.portfolio import PortfolioSummary
from ..schemas.asset import Currency
from .market_data import MarketDataService
from .fx import FxService

logger = logging.getLogger(__name__)

# Lookback window (in calendar days) for each supported history period.
HISTORY_PERIOD_DAYS = {
    "1w": 7,
    "1m": 30,
    "3m": 90,
    "6m": 182,
    "1y": 365,
}
DEFAULT_HISTORY_PERIOD = "3m"


def _ensure_aware(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC so comparisons with tz-aware datetimes are safe."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class PortfolioService:
    """Service for portfolio calculations and analytics."""

    def __init__(self, db: Session, base_currency: Currency = Currency.USD):
        self.db = db
        self.base_currency = base_currency
        self.market_service = MarketDataService(db)
        self.fx_service = FxService(db)

    async def get_holdings_with_prices(self) -> List[HoldingWithAsset]:
        """Get all holdings with current prices and P&L."""
        holdings = self.db.query(HoldingLot).all()
        result = []

        for holding in holdings:
            holding_dict = {
                "id": holding.id,
                "assetId": holding.asset_id,
                "quantity": holding.quantity,
                "avgCost": holding.avg_cost,
                "costCurrency": holding.cost_currency,
                "accountName": holding.account_name,
                "tags": holding.tags,
                "purchaseDate": holding.purchase_date,
                "createdAt": holding.created_at,
                "asset": holding.asset,
            }

            # Get current price
            try:
                quote = await self.market_service.get_quote(
                    holding.asset.symbol,
                    holding.asset.asset_type.value
                )

                current_price = quote["price"]
                price_change = quote.get("change", 0)
                price_change_percent = quote.get("changePercent", 0)

                # Convert to base currency if needed
                current_price_base = await self.fx_service.convert(
                    current_price,
                    Currency(quote.get("currency", "USD")),
                    self.base_currency
                )

                avg_cost_base = await self.fx_service.convert(
                    holding.avg_cost,
                    holding.cost_currency,
                    self.base_currency
                )

                market_value = current_price_base * holding.quantity
                cost_basis = avg_cost_base * holding.quantity
                unrealized_pnl = market_value - cost_basis
                unrealized_pnl_percent = (unrealized_pnl / cost_basis * 100) if cost_basis else 0

                holding_dict.update({
                    "currentPrice": current_price_base,
                    "priceChange": price_change,
                    "priceChangePercent": price_change_percent,
                    "marketValue": market_value,
                    "unrealizedPnL": unrealized_pnl,
                    "unrealizedPnLPercent": unrealized_pnl_percent,
                })

            except Exception as e:
                # If we can't get current price, just return without market data
                logger.warning("Error getting price for %s: %s", holding.asset.symbol, e)

            result.append(HoldingWithAsset(**holding_dict))

        return result

    async def get_grouped_holdings(self) -> List[GroupedHolding]:
        """Get holdings grouped by asset."""
        holdings_with_prices = await self.get_holdings_with_prices()

        # Group by asset_id
        grouped = {}
        for holding in holdings_with_prices:
            aid = holding.asset_id
            if aid not in grouped:
                grouped[aid] = {
                    "assetId": aid,
                    "asset": holding.asset,
                    "lots": [],
                    "totalQuantity": 0,
                    "totalCost": 0,
                }

            grouped[aid]["lots"].append(holding)
            grouped[aid]["totalQuantity"] += holding.quantity

            # Convert cost to base currency for weighted average
            cost_base = await self.fx_service.convert(
                holding.avg_cost,
                Currency(holding.cost_currency),
                self.base_currency
            )
            grouped[aid]["totalCost"] += cost_base * holding.quantity

        # Calculate weighted averages and totals
        result = []
        for asset_id, group in grouped.items():
            total_quantity = group["totalQuantity"]
            avg_cost = group["totalCost"] / total_quantity if total_quantity else 0

            # Use first lot's current price (they should all be the same)
            first_lot = group["lots"][0]
            current_price = first_lot.current_price
            price_change = first_lot.price_change
            price_change_percent = first_lot.price_change_percent

            market_value = None
            unrealized_pnl = None
            unrealized_pnl_percent = None

            if current_price:
                market_value = current_price * total_quantity
                cost_basis = avg_cost * total_quantity
                unrealized_pnl = market_value - cost_basis
                unrealized_pnl_percent = (unrealized_pnl / cost_basis * 100) if cost_basis else 0

            result.append(GroupedHolding(
                assetId=asset_id,
                asset=group["asset"],
                lots=group["lots"],
                totalQuantity=total_quantity,
                avgCost=avg_cost,
                currentPrice=current_price,
                priceChange=price_change,
                priceChangePercent=price_change_percent,
                marketValue=market_value,
                unrealizedPnL=unrealized_pnl,
                unrealizedPnLPercent=unrealized_pnl_percent,
            ))

        return result

    async def _get_nav_at_date(
        self,
        target_date: datetime,
        holdings: Optional[List[HoldingLot]] = None,
    ) -> Optional[float]:
        """
        Calculate portfolio NAV at a historical date using price snapshots.

        This is the SHARED valuation helper used by both `get_summary()`
        (daily/weekly/monthly P&L) and `get_nav_history()` (the history
        chart). There is only one at-date valuation method — do not
        duplicate this logic elsewhere.

        For each held asset, finds the most recent PriceSnapshot on or before
        target_date, converts to base currency, and sums price * quantity.
        A lot only contributes from its `purchase_date` onward — lots
        purchased after `target_date` are excluded.
        Returns None if no historical price data is available for any
        (already-purchased) asset.

        `holdings` can be passed in to avoid re-querying the DB when this is
        called repeatedly across many dates (see `get_nav_history`).
        """
        if holdings is None:
            holdings = self.db.query(HoldingLot).all()
        if not holdings:
            return None

        target_date = _ensure_aware(target_date)

        total_nav = 0.0
        has_any_price = False

        for holding in holdings:
            purchase_date = holding.purchase_date
            if purchase_date is not None and _ensure_aware(purchase_date) > target_date:
                # Lot not yet purchased as of target_date - excluded from NAV.
                continue

            # Find the closest price snapshot on or before the target date
            snapshot = (
                self.db.query(PriceSnapshot)
                .filter(
                    PriceSnapshot.asset_id == holding.asset_id,
                    PriceSnapshot.timestamp <= target_date,
                )
                .order_by(PriceSnapshot.timestamp.desc())
                .first()
            )

            if snapshot is None:
                continue

            has_any_price = True

            try:
                price_base = await self.fx_service.convert(
                    snapshot.price,
                    Currency(snapshot.currency),
                    self.base_currency,
                )
            except Exception:
                price_base = snapshot.price

            total_nav += price_base * holding.quantity

        return total_nav if has_any_price else None

    async def get_nav_history(self, period: str = DEFAULT_HISTORY_PERIOD) -> dict:
        """
        Build the NAV time series for the dashboard performance chart.

        Reuses `_get_nav_at_date` (the same at-date valuation method used by
        `get_summary()`) for every date in the period that actually has a
        price snapshot. Dates with no snapshot (weekends/holidays) are
        simply absent - no forward-fill.

        `pnl`/`pnl_pct` are relative to the first point in the series.
        """
        days = HISTORY_PERIOD_DAYS.get(period, HISTORY_PERIOD_DAYS[DEFAULT_HISTORY_PERIOD])
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)

        holdings = self.db.query(HoldingLot).all()
        if not holdings:
            return {
                "base_currency": self.base_currency.value,
                "period": period,
                "points": [],
            }

        asset_ids = {holding.asset_id for holding in holdings}

        # Union of all calendar dates that have a price snapshot for any
        # currently held asset within the requested window.
        date_rows = (
            self.db.query(func.date(PriceSnapshot.timestamp))
            .filter(
                PriceSnapshot.asset_id.in_(asset_ids),
                PriceSnapshot.timestamp >= start,
                PriceSnapshot.timestamp <= now,
            )
            .distinct()
            .order_by(func.date(PriceSnapshot.timestamp))
            .all()
        )

        points = []
        first_nav = None

        for (raw_date,) in date_rows:
            if isinstance(raw_date, str):
                day = datetime.strptime(raw_date, "%Y-%m-%d").date()
            elif isinstance(raw_date, datetime):
                day = raw_date.date()
            elif isinstance(raw_date, date_cls):
                day = raw_date
            else:
                continue

            target_dt = datetime.combine(day, dt_time(23, 59, 59), tzinfo=timezone.utc)
            nav = await self._get_nav_at_date(target_dt, holdings=holdings)

            if nav is None:
                continue

            if first_nav is None:
                first_nav = nav

            pnl = nav - first_nav
            pnl_pct = (pnl / first_nav * 100) if first_nav else 0.0

            points.append({
                "date": day.isoformat(),
                "nav": nav,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            })

        return {
            "base_currency": self.base_currency.value,
            "period": period,
            "points": points,
        }

    async def get_summary(self) -> PortfolioSummary:
        """Calculate portfolio summary with daily, weekly, and monthly P&L."""
        grouped = await self.get_grouped_holdings()

        total_nav = 0
        total_cost = 0

        for holding in grouped:
            if holding.market_value:
                total_nav += holding.market_value
            total_cost += holding.avg_cost * holding.total_quantity

        total_unrealized_pnl = total_nav - total_cost
        total_unrealized_pnl_percent = (total_unrealized_pnl / total_cost * 100) if total_cost else 0

        # Calculate daily/weekly/monthly P&L from historical snapshots
        daily_pnl = 0.0
        daily_pnl_percent = 0.0
        weekly_pnl_percent = None
        monthly_pnl_percent = None

        try:
            now = datetime.now(timezone.utc)

            # Daily: use the most recent EOD snapshot before today
            yesterday_nav = await self._get_nav_at_date(
                now - timedelta(days=1)
            )
            # If no data for 1 day ago, try up to 3 days back (weekends)
            if yesterday_nav is None:
                yesterday_nav = await self._get_nav_at_date(
                    now - timedelta(days=3)
                )

            if yesterday_nav and yesterday_nav > 0:
                daily_pnl = total_nav - yesterday_nav
                daily_pnl_percent = (daily_pnl / yesterday_nav) * 100

            # Weekly: ~7 calendar days back (covers 5 trading days)
            weekly_nav = await self._get_nav_at_date(
                now - timedelta(days=7)
            )
            if weekly_nav and weekly_nav > 0:
                weekly_pnl_percent = ((total_nav - weekly_nav) / weekly_nav) * 100

            # Monthly: ~30 calendar days back (covers ~21 trading days)
            monthly_nav = await self._get_nav_at_date(
                now - timedelta(days=30)
            )
            if monthly_nav and monthly_nav > 0:
                monthly_pnl_percent = ((total_nav - monthly_nav) / monthly_nav) * 100

        except Exception as e:
            logger.warning("Error calculating P&L from snapshots: %s", e)

        return PortfolioSummary(
            totalNav=total_nav,
            baseCurrency=self.base_currency,
            dailyPnL=daily_pnl,
            dailyPnLPercent=daily_pnl_percent,
            weeklyPnLPercent=weekly_pnl_percent,
            monthlyPnLPercent=monthly_pnl_percent,
            totalCost=total_cost,
            totalUnrealizedPnL=total_unrealized_pnl,
            totalUnrealizedPnLPercent=total_unrealized_pnl_percent,
            lastUpdated=datetime.now(),
        )
