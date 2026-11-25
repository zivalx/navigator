import logging
import uuid
from typing import List, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone, time as dt_time, date as date_cls

from ..models.holding import HoldingLot
from ..models.asset import Asset
from ..models.price import PriceSnapshot
from ..models.portfolio_snapshot import PortfolioSnapshot
from ..schemas.holding import HoldingWithAsset, GroupedHolding
from ..schemas.portfolio import PortfolioSummary
from ..schemas.asset import Currency
from .market_data import MarketDataService
from .fx import FxService
from .log_redaction import redact_secrets

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


def _currency_code(value) -> str:
    """ISO code of a currency held as a model enum, schema enum or plain str
    (schemas use use_enum_values, so either may appear)."""
    return getattr(value, "value", value)


def _is_lot_currency_check(lot, price_currency=None) -> bool:
    """A non-ILS lot on an ILS listing: the known result of the old USD
    default, so the recorded cost is really shekels (spec 6.4).

    The listing is ILS when the stored asset currency says so or, before the
    audit has fixed an old TASE asset still labelled USD, when its price
    (live quote, or latest stored snapshot) is in ILS."""
    if _currency_code(lot.cost_currency) == Currency.ILS.value:
        return False
    return Currency.ILS.value in (
        _currency_code(lot.asset.currency),
        _currency_code(price_currency) if price_currency is not None else None,
    )


class PortfolioService:
    """Service for portfolio calculations and analytics."""

    def __init__(self, db: Session, base_currency: Currency = Currency.USD):
        self.db = db
        self.base_currency = base_currency
        self.market_service = MarketDataService(db)
        self.fx_service = FxService(db)

    async def get_holdings_with_prices(self) -> List[HoldingWithAsset]:
        """Get all holdings with current prices and P&L.

        A lot that cannot be valued in the base currency is flagged
        (`valueUnavailable` + `unavailableReason`) with every base-currency
        field None, never filled with an unconverted native amount."""
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
            holding_dict.update(await self._value_lot(holding))
            result.append(HoldingWithAsset(**holding_dict))

        return result

    async def _value_lot(self, holding: HoldingLot) -> dict:
        """Base-currency market fields for one lot, or the unavailable flag.

        Rules, first match wins (spec 6.4/6.5, plan Ruling 1):
        1. ILS asset with a non-ILS lot -> lot_currency_check
        2. cost->base FX conversion raises -> fx_unavailable
        3. the quote raises -> price_unavailable
        4. ILS quote with a non-ILS lot (asset label not yet fixed by the
           audit) -> lot_currency_check
        5. quote currency is not a Currency member, or price->base FX
           conversion raises -> fx_unavailable
        """
        symbol = holding.asset.symbol

        def unavailable(reason: str, detail=None, **extra) -> dict:
            logger.warning(
                "holding %s has no %s value: %s%s", symbol, self.base_currency.value,
                reason, f" ({redact_secrets(detail)})" if detail else "",
            )
            return {"valueUnavailable": True, "unavailableReason": reason, **extra}

        if _is_lot_currency_check(holding):
            return unavailable("lot_currency_check")

        # Cost basis in base currency, exposed as avgCostBase so callers can
        # aggregate cost without mixing native cost currencies into a total.
        try:
            avg_cost_base = await self.fx_service.convert(
                holding.avg_cost,
                Currency(_currency_code(holding.cost_currency)),
                self.base_currency,
            )
        except Exception:
            return unavailable("fx_unavailable")

        try:
            quote = await self.market_service.get_quote(
                symbol,
                holding.asset.asset_type.value
            )
            current_price = quote["price"]
            if current_price is None:
                raise ValueError("quote has no price")
        except Exception as e:
            return unavailable("price_unavailable", detail=e)

        if _is_lot_currency_check(holding, price_currency=quote.get("currency")):
            return unavailable("lot_currency_check")

        # change/changePercent are None on a stale db_cache fallback —
        # treat unknown as 0 so it contributes nothing to daily P&L.
        price_change = quote.get("change") or 0
        price_change_percent = quote.get("changePercent") or 0

        # Convert price and the daily change to base currency. The change is
        # a per-share delta, so the same FX rate applies; leaving it native
        # would make `quantity * priceChange` sums wrong for any non-base
        # holding.
        try:
            quote_currency = Currency(quote.get("currency", "USD"))
            current_price_base = await self.fx_service.convert(
                current_price, quote_currency, self.base_currency
            )
            price_change_base = await self.fx_service.convert(
                price_change, quote_currency, self.base_currency
            )
        except Exception:
            return unavailable("fx_unavailable", priceChangePercent=price_change_percent)

        market_value = current_price_base * holding.quantity
        cost_basis = avg_cost_base * holding.quantity
        unrealized_pnl = market_value - cost_basis
        unrealized_pnl_percent = (unrealized_pnl / cost_basis * 100) if cost_basis else 0

        return {
            "avgCostBase": avg_cost_base,
            "currentPrice": current_price_base,
            "priceChange": price_change_base,
            "priceChangePercent": price_change_percent,
            "marketValue": market_value,
            "unrealizedPnL": unrealized_pnl,
            "unrealizedPnLPercent": unrealized_pnl_percent,
        }

    async def get_grouped_holdings(self) -> List[GroupedHolding]:
        """Get holdings grouped by asset.

        A group is value_unavailable when any of its lots is (first flagged
        lot's reason); its base-currency fields are then None. The weighted
        average cost reuses each lot's avgCostBase — no FX calls here."""
        holdings_with_prices = await self.get_holdings_with_prices()

        # Group by asset_id
        grouped = {}
        for holding in holdings_with_prices:
            aid = holding.asset_id
            if aid not in grouped:
                grouped[aid] = {
                    "asset": holding.asset,
                    "lots": [],
                    "totalQuantity": 0,
                    "totalCost": 0,
                    "reason": None,
                }

            group = grouped[aid]
            group["lots"].append(holding)
            group["totalQuantity"] += holding.quantity
            if holding.value_unavailable:
                if group["reason"] is None:
                    group["reason"] = holding.unavailable_reason
            else:
                group["totalCost"] += holding.avg_cost_base * holding.quantity

        # Calculate weighted averages and totals
        result = []
        for asset_id, group in grouped.items():
            total_quantity = group["totalQuantity"]
            first_lot = group["lots"][0]

            if group["reason"] is not None:
                result.append(GroupedHolding(
                    assetId=asset_id,
                    asset=group["asset"],
                    lots=group["lots"],
                    totalQuantity=total_quantity,
                    priceChangePercent=first_lot.price_change_percent,
                    valueUnavailable=True,
                    unavailableReason=group["reason"],
                ))
                continue

            avg_cost = group["totalCost"] / total_quantity if total_quantity else 0

            # Use first lot's current price (they should all be the same)
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

    def _latest_price_currency(self, asset_id: str, when: datetime):
        """Currency of the asset's latest stored price on/before `when`, if any."""
        snap = self._latest_snapshot(asset_id, when)
        return snap.currency if snap is not None else None

    def _latest_snapshot(self, asset_id: str, when: datetime) -> Optional[PriceSnapshot]:
        """Most recent PriceSnapshot for an asset on or before `when`."""
        return (
            self.db.query(PriceSnapshot)
            .filter(
                PriceSnapshot.asset_id == asset_id,
                PriceSnapshot.timestamp <= when,
            )
            .order_by(PriceSnapshot.timestamp.desc())
            .first()
        )

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
        (already-purchased) asset, or if any snapshot price cannot be
        converted to the base currency (callers omit that point).

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
            snapshot = self._latest_snapshot(holding.asset_id, target_date)

            if snapshot is None:
                continue

            has_any_price = True

            try:
                price_base = await self.fx_service.convert(
                    snapshot.price,
                    Currency(_currency_code(snapshot.currency)),
                    self.base_currency,
                )
            except Exception:
                # A NAV missing a holding is a fake dip; omit the point.
                logger.warning(
                    "NAV at %s unavailable: no FX rate %s->%s",
                    target_date.date(), _currency_code(snapshot.currency),
                    self.base_currency.value,
                )
                return None

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
        all_holdings = await self.get_holdings_with_prices()
        # Flagged holdings have no base-currency value: they are excluded from
        # every total, current and historical alike.
        holdings = [h for h in all_holdings if not h.value_unavailable]
        flagged_ids = {h.id for h in all_holdings if h.value_unavailable}
        valued_lots = [
            lot for lot in self.db.query(HoldingLot).all() if lot.id not in flagged_ids
        ]

        total_nav = 0.0
        total_cost = 0.0
        for h in holdings:
            if h.market_value:
                total_nav += h.market_value
            # Base-currency cost basis (avgCostBase), never the native avgCost,
            # so mixed-currency lots don't corrupt the total.
            if h.avg_cost_base is not None:
                total_cost += h.avg_cost_base * h.quantity

        total_unrealized_pnl = total_nav - total_cost
        total_unrealized_pnl_percent = (total_unrealized_pnl / total_cost * 100) if total_cost else 0

        def current_nav_of_lots_held_at(cutoff: datetime) -> float:
            """Current market value of only the lots that already existed at
            `cutoff`, so a lot bought after the cutoff can't inflate the P&L
            delta by its full market value. (Both sides also require price
            data — the current side a live quote, the historical side a
            snapshot on/before the cutoff — so an asset with no snapshot yet
            is still excluded from the historical NAV as before.)"""
            cutoff = _ensure_aware(cutoff)
            return sum(
                h.market_value
                for h in holdings
                if h.market_value
                and h.purchase_date is not None
                and _ensure_aware(h.purchase_date) <= cutoff
            )

        # Calculate daily/weekly/monthly P&L from historical snapshots
        daily_pnl = 0.0
        daily_pnl_percent = 0.0
        weekly_pnl_percent = None
        monthly_pnl_percent = None

        try:
            now = datetime.now(timezone.utc)

            # Daily: most recent EOD snapshot before today; fall back up to 3
            # days back for weekends/holidays, using whichever cutoff matched.
            daily_cutoff = now - timedelta(days=1)
            yesterday_nav = await self._get_nav_at_date(daily_cutoff, holdings=valued_lots)
            if yesterday_nav is None:
                daily_cutoff = now - timedelta(days=3)
                yesterday_nav = await self._get_nav_at_date(daily_cutoff, holdings=valued_lots)

            if yesterday_nav and yesterday_nav > 0:
                daily_pnl = current_nav_of_lots_held_at(daily_cutoff) - yesterday_nav
                daily_pnl_percent = (daily_pnl / yesterday_nav) * 100

            # Weekly: ~7 calendar days back (covers 5 trading days)
            weekly_cutoff = now - timedelta(days=7)
            weekly_nav = await self._get_nav_at_date(weekly_cutoff, holdings=valued_lots)
            if weekly_nav and weekly_nav > 0:
                weekly_delta = current_nav_of_lots_held_at(weekly_cutoff) - weekly_nav
                weekly_pnl_percent = (weekly_delta / weekly_nav) * 100

            # Monthly: ~30 calendar days back (covers ~21 trading days)
            monthly_cutoff = now - timedelta(days=30)
            monthly_nav = await self._get_nav_at_date(monthly_cutoff, holdings=valued_lots)
            if monthly_nav and monthly_nav > 0:
                monthly_delta = current_nav_of_lots_held_at(monthly_cutoff) - monthly_nav
                monthly_pnl_percent = (monthly_delta / monthly_nav) * 100

        except Exception as e:
            logger.warning("Error calculating P&L from snapshots: %s", redact_secrets(e))

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

    # ------------------------------------------------------------------
    # Persisted daily P&L + EOD NAV series
    # (spec: docs/superpowers/specs/2026-08-20-daily-pnl-series-design.md)
    # ------------------------------------------------------------------

    async def _asset_usd_price_at(self, asset_id: str, when: datetime) -> Optional[float]:
        """Latest snapshot price for an asset on/before `when`, converted to USD.
        None when the asset has no snapshot yet (so it can't be diffed) or the
        price has no USD rate."""
        snap = self._latest_snapshot(asset_id, when)
        if snap is None:
            return None
        try:
            return await self.fx_service.convert(
                snap.price, Currency(_currency_code(snap.currency)), Currency.USD
            )
        except Exception:
            return None

    async def compute_daily_snapshot(
        self, day: date_cls, insert_only: bool = False
    ) -> Optional[PortfolioSnapshot]:
        """Compute and upsert the portfolio_snapshots row for `day`.

        NAV is the USD portfolio value at end of `day`. daily_pnl is the sum of
        each held lot's per-asset price move since the previous stored day —
        NOT a NAV difference. Summing per-asset moves is what makes it accurate:
        a lot bought after the prior day contributes nothing (it wasn't held
        then), and an asset whose price history only just begins contributes
        nothing (no prior price to diff), so uneven snapshot coverage (e.g.
        crypto-only weekend days) can't masquerade as a gain. net_flow (USD cost
        of lots bought on `day`) is stored for reference. Idempotent. With
        `insert_only`, a day that already has a row is left alone (None)."""
        if insert_only and self.db.query(PortfolioSnapshot.id).filter_by(date=day).first():
            return None
        end_of_day = datetime.combine(day, dt_time(23, 59, 59), tzinfo=timezone.utc)
        holdings = self.db.query(HoldingLot).all()

        # The stored series is canonically USD regardless of the service's
        # configured base_currency.
        usd = PortfolioService(self.db, base_currency=Currency.USD)

        # A stored row is permanent: if any held lot's cost or price has no
        # USD rate, skip the day rather than write a NAV missing a holding.
        missing = await usd._codes_without_usd_rate(holdings, end_of_day)
        if missing:
            logger.warning(
                "skipping portfolio snapshot for %s: no FX rate for %s",
                day, ", ".join(sorted(missing)),
            )
            return None

        bought_today = [
            lot for lot in holdings
            if lot.purchase_date is not None
            and _ensure_aware(lot.purchase_date).date() == day
        ]
        # A lot_currency_check lot's recorded cost is not in its labelled
        # currency, so it can't enter net_flow. Its NAV (price-based) is fine,
        # so only its purchase day is skipped.
        # No live quote here: the latest stored price's currency stands in
        # for it (an ILS price reveals a TASE asset still labelled USD).
        unchecked = sorted({
            lot.asset.symbol for lot in bought_today
            if _is_lot_currency_check(lot, price_currency=self._latest_price_currency(
                lot.asset_id, end_of_day))
        })
        if unchecked:
            logger.warning(
                "skipping portfolio snapshot for %s: lot currency check for %s",
                day, ", ".join(unchecked),
            )
            return None

        nav = await usd._get_nav_at_date(end_of_day, holdings=holdings)
        if nav is None:
            return None

        net_flow = 0.0
        for lot in bought_today:
            cost_code = _currency_code(lot.cost_currency)
            try:
                cost_usd = await usd.fx_service.convert(
                    lot.avg_cost, Currency(cost_code), Currency.USD
                )
            except Exception:
                logger.warning(
                    "skipping portfolio snapshot for %s: no FX rate for %s",
                    day, cost_code,
                )
                return None
            net_flow += cost_usd * lot.quantity

        prev = (
            self.db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.date < day)
            .order_by(PortfolioSnapshot.date.desc())
            .first()
        )
        prev_nav = prev.nav if prev else None

        daily_pnl = 0.0
        daily_pnl_pct = 0.0
        if prev is not None and prev_nav and prev_nav > 0:
            prev_end = datetime.combine(prev.date, dt_time(23, 59, 59), tzinfo=timezone.utc)
            for lot in holdings:
                pd = lot.purchase_date
                # Only lots held at the previous close contribute today's move.
                if pd is not None and _ensure_aware(pd) > prev_end:
                    continue
                prices = []
                for when in (end_of_day, prev_end):
                    price = await usd._asset_usd_price_at(lot.asset_id, when)
                    if price is None:
                        snap = usd._latest_snapshot(lot.asset_id, when)
                        if snap is not None:
                            # A price exists but has no USD rate: dropping
                            # the lot would store a partial daily_pnl.
                            logger.warning(
                                "skipping portfolio snapshot for %s: no FX rate for %s",
                                day, _currency_code(snap.currency),
                            )
                            return None
                    prices.append(price)
                p_now, p_prev = prices
                # No snapshot yet (history just begins): nothing to diff.
                if p_now is None or p_prev is None:
                    continue
                daily_pnl += lot.quantity * (p_now - p_prev)
            daily_pnl_pct = daily_pnl / prev_nav * 100

        row = self.db.query(PortfolioSnapshot).filter_by(date=day).first()
        if row is None:
            row = PortfolioSnapshot(id=str(uuid.uuid4()), date=day)
            self.db.add(row)
        row.nav = nav
        row.net_flow = net_flow
        row.daily_pnl = daily_pnl
        row.daily_pnl_pct = daily_pnl_pct
        row.base_currency = "USD"
        self.db.commit()
        self.db.refresh(row)
        return row

    async def _codes_without_usd_rate(
        self, holdings: List[HoldingLot], end_of_day: datetime
    ) -> set:
        """Currency codes of lots held at `end_of_day` (cost currency, and the
        currency of the latest price snapshot) that cannot be converted to USD."""
        codes = set()
        for lot in holdings:
            pd = lot.purchase_date
            if pd is not None and _ensure_aware(pd) > end_of_day:
                continue
            codes.add(_currency_code(lot.cost_currency))
            snap = self._latest_snapshot(lot.asset_id, end_of_day)
            if snap is not None:
                codes.add(_currency_code(snap.currency))

        missing = set()
        for code in codes:
            try:
                await self.fx_service.convert(1.0, Currency(code), Currency.USD)
            except Exception:
                missing.add(code)
        return missing

    async def backfill_daily_snapshots(
        self, before: date_cls | None = None, since: date_cls | None = None
    ) -> int:
        """Build the daily series from every distinct date that has price
        snapshots, oldest first. Idempotent (compute_daily_snapshot upserts).
        With `before` and/or `since` only dates in [since, before) are built, and
        insert-only: an existing row is never rewritten. Returns rows written."""
        date_rows = (
            self.db.query(func.date(PriceSnapshot.timestamp))
            .distinct()
            .order_by(func.date(PriceSnapshot.timestamp))
            .all()
        )
        written = 0
        for (raw_date,) in date_rows:
            if isinstance(raw_date, str):
                day = datetime.strptime(raw_date, "%Y-%m-%d").date()
            elif isinstance(raw_date, datetime):
                day = raw_date.date()
            elif isinstance(raw_date, date_cls):
                day = raw_date
            else:
                continue
            if before is not None and day >= before:
                continue
            if since is not None and day < since:
                continue
            row = await self.compute_daily_snapshot(day, insert_only=before is not None or since is not None)
            if row is not None:
                written += 1
        return written

    async def get_daily_pnl_series(self, period: str = DEFAULT_HISTORY_PERIOD) -> dict:
        """Read the stored daily series within `period`, converting the USD
        canonical values to the service's base currency for display."""
        days = HISTORY_PERIOD_DAYS.get(period, HISTORY_PERIOD_DAYS[DEFAULT_HISTORY_PERIOD])
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=days)).date()

        rows = (
            self.db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.date >= start)
            .order_by(PortfolioSnapshot.date)
            .all()
        )

        rate = 1.0
        if self.base_currency != Currency.USD:
            try:
                rate = await self.fx_service.get_rate(Currency.USD, self.base_currency)
            except Exception:
                # Never show the USD series labelled as another currency.
                logger.warning(
                    "daily P&L series unavailable: no FX rate USD->%s",
                    self.base_currency.value,
                )
                return {
                    "base_currency": self.base_currency.value,
                    "period": period,
                    "points": [],
                }

        def to_display(usd_amount: float) -> float:
            if self.base_currency == Currency.USD:
                return usd_amount
            return usd_amount * rate

        points = []
        for r in rows:
            points.append({
                "date": r.date.isoformat(),
                "nav": to_display(r.nav),
                "dailyPnL": to_display(r.daily_pnl),
                "dailyPnLPercent": r.daily_pnl_pct,
                "netFlow": to_display(r.net_flow),
            })

        return {
            "base_currency": self.base_currency.value,
            "period": period,
            "points": points,
        }
