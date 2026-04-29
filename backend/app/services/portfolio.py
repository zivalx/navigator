from typing import List
from sqlalchemy.orm import Session
from datetime import datetime

from ..models.holding import HoldingLot
from ..models.asset import Asset
from ..schemas.holding import HoldingWithAsset, GroupedHolding
from ..schemas.portfolio import PortfolioSummary
from ..schemas.asset import Currency
from .market_data import MarketDataService
from .fx import FxService


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
                print(f"Error getting price for {holding.asset.symbol}: {e}")

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

    async def get_summary(self) -> PortfolioSummary:
        """Calculate portfolio summary."""
        grouped = await self.get_grouped_holdings()

        total_nav = 0
        total_cost = 0

        for holding in grouped:
            if holding.market_value:
                total_nav += holding.market_value
            total_cost += holding.avg_cost * holding.total_quantity

        total_unrealized_pnl = total_nav - total_cost
        total_unrealized_pnl_percent = (total_unrealized_pnl / total_cost * 100) if total_cost else 0

        # TODO: Calculate daily/weekly/monthly P&L from historical snapshots
        daily_pnl = 0
        daily_pnl_percent = 0

        return PortfolioSummary(
            totalNav=total_nav,
            baseCurrency=self.base_currency,
            dailyPnL=daily_pnl,
            dailyPnLPercent=daily_pnl_percent,
            weeklyPnLPercent=None,
            monthlyPnLPercent=None,
            totalCost=total_cost,
            totalUnrealizedPnL=total_unrealized_pnl,
            totalUnrealizedPnLPercent=total_unrealized_pnl_percent,
            lastUpdated=datetime.now(),
        )
