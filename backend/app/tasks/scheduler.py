import logging
import uuid
from datetime import datetime, timezone

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func

from ..database import SessionLocal
from ..models.asset import Asset
from ..models.alert import AlertRule, PriceAlert
from ..models.holding import HoldingLot
from ..models.price import PriceSnapshot
from ..services.market_data import MarketDataService
from ..services import breadth as breadth_service

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
}

_scheduler: AsyncIOScheduler | None = None


async def snapshot_eod_prices() -> None:
    """Take end-of-day price snapshots for all held assets."""
    db = SessionLocal()
    try:
        asset_ids = (
            db.query(HoldingLot.asset_id)
            .distinct()
            .all()
        )
        asset_ids = [row[0] for row in asset_ids]

        if not asset_ids:
            logger.info("EOD snapshot: no holdings found, skipping")
            return

        market_service = MarketDataService(db)
        success_count = 0
        error_count = 0

        for asset_id in asset_ids:
            asset = db.query(Asset).filter(Asset.id == asset_id).first()
            if not asset:
                continue

            try:
                quote = await market_service.get_quote(
                    asset.symbol, asset.asset_type.value
                )
                snapshot = PriceSnapshot(
                    id=str(uuid.uuid4()),
                    asset_id=asset.id,
                    price=quote["price"],
                    currency=quote.get("currency", "USD"),
                    timestamp=datetime.now(timezone.utc),
                    source="eod_snapshot",
                )
                db.add(snapshot)
                db.commit()
                success_count += 1
            except Exception as e:
                db.rollback()
                error_count += 1
                logger.warning(
                    "EOD snapshot failed for %s: %s", asset.symbol, e
                )

        logger.info(
            "EOD snapshot complete: %d succeeded, %d failed",
            success_count,
            error_count,
        )
    except Exception as e:
        logger.error("EOD snapshot job error: %s", e)
    finally:
        db.close()


async def backfill_historical_prices() -> None:
    """
    One-time backfill of historical prices from Yahoo Finance.
    Runs only when fewer than 5 eod_snapshot rows exist in the database.
    """
    db = SessionLocal()
    try:
        eod_count = (
            db.query(func.count(PriceSnapshot.id))
            .filter(PriceSnapshot.source == "eod_snapshot")
            .scalar()
        )

        if eod_count >= 5:
            logger.info(
                "Backfill skipped: already have %d EOD snapshots", eod_count
            )
            return

        asset_ids = (
            db.query(HoldingLot.asset_id)
            .distinct()
            .all()
        )
        asset_ids = [row[0] for row in asset_ids]

        if not asset_ids:
            logger.info("Backfill: no holdings found, skipping")
            return

        total_inserted = 0

        async with httpx.AsyncClient(timeout=15.0) as client:
            for asset_id in asset_ids:
                asset = db.query(Asset).filter(Asset.id == asset_id).first()
                if not asset:
                    continue

                try:
                    url = YAHOO_CHART_URL.format(symbol=asset.symbol.upper())
                    params = {"interval": "1d", "range": "6mo"}
                    response = await client.get(
                        url, params=params, headers=YAHOO_HEADERS
                    )
                    response.raise_for_status()
                    data = response.json()

                    result = data.get("chart", {}).get("result")
                    if not result:
                        logger.warning(
                            "Backfill: no Yahoo data for %s", asset.symbol
                        )
                        continue

                    timestamps = result[0].get("timestamp", [])
                    closes = (
                        result[0]
                        .get("indicators", {})
                        .get("quote", [{}])[0]
                        .get("close", [])
                    )
                    currency = (
                        result[0]
                        .get("meta", {})
                        .get("currency", "USD")
                    )

                    snapshots = []
                    for ts, close in zip(timestamps, closes):
                        if close is None:
                            continue
                        snapshots.append(
                            PriceSnapshot(
                                id=str(uuid.uuid4()),
                                asset_id=asset.id,
                                price=close,
                                currency=currency,
                                timestamp=datetime.fromtimestamp(
                                    ts, tz=timezone.utc
                                ),
                                source="eod_backfill",
                            )
                        )

                    if snapshots:
                        db.bulk_save_objects(snapshots)
                        db.commit()
                        total_inserted += len(snapshots)
                        logger.info(
                            "Backfill: inserted %d snapshots for %s",
                            len(snapshots),
                            asset.symbol,
                        )

                except Exception as e:
                    db.rollback()
                    logger.warning(
                        "Backfill failed for %s: %s", asset.symbol, e
                    )

        logger.info("Backfill complete: %d total snapshots inserted", total_inserted)
    except Exception as e:
        logger.error("Backfill job error: %s", e)
    finally:
        db.close()


async def evaluate_price_alerts() -> None:
    """Evaluate all active price alerts against the latest quotes.

    Batch-fetches quotes for every distinct symbol involved (riding the 60s
    quote cache in MarketDataService), then triggers alerts whose rule
    condition is met (price <= threshold for price_below, price >= threshold
    for price_above — boundary inclusive). Triggered alerts are marked
    is_active=False with triggered_at/triggered_price recorded; they don't
    fire again unless reactivated via the API.
    """
    db = SessionLocal()
    try:
        alerts = (
            db.query(PriceAlert)
            .filter(PriceAlert.is_active.is_(True))
            .all()
        )
        if not alerts:
            return

        symbols = sorted({a.asset.symbol for a in alerts if a.asset})
        if not symbols:
            return

        market_service = MarketDataService(db)
        try:
            quotes = await market_service.get_quotes(symbols)
        except Exception as e:
            logger.error("Price alert evaluation: quote fetch failed: %s", e)
            return

        triggered_count = 0
        for alert in alerts:
            if not alert.asset:
                continue

            quote = quotes.get(alert.asset.symbol)
            if not quote or quote.get("error") or quote.get("price") is None:
                continue

            price = quote["price"]
            hit = (
                (alert.rule == AlertRule.PRICE_BELOW and price <= alert.threshold)
                or (alert.rule == AlertRule.PRICE_ABOVE and price >= alert.threshold)
            )
            if hit:
                alert.is_active = False
                alert.triggered_at = datetime.now(timezone.utc)
                alert.triggered_price = price
                triggered_count += 1

        if triggered_count:
            db.commit()
            logger.info(
                "Price alert evaluation: %d alert(s) triggered", triggered_count
            )
    except Exception as e:
        db.rollback()
        logger.error("Price alert evaluation job error: %s", e)
    finally:
        db.close()


def start_scheduler() -> None:
    """Start the APScheduler with EOD and backfill jobs."""
    global _scheduler
    _scheduler = AsyncIOScheduler()

    # Daily EOD snapshot at 16:30 US/Eastern (after market close)
    _scheduler.add_job(
        snapshot_eod_prices,
        "cron",
        hour=16,
        minute=30,
        timezone="US/Eastern",
        id="eod_snapshot",
        name="EOD Price Snapshot",
        replace_existing=True,
    )

    # Daily market breadth computation (S5FI/S5TH) at 17:00 US/Eastern,
    # shortly after the EOD snapshot.
    _scheduler.add_job(
        breadth_service.compute_and_store_breadth,
        "cron",
        hour=17,
        minute=0,
        timezone="US/Eastern",
        id="breadth_snapshot",
        name="Market Breadth Computation (S5FI/S5TH)",
        replace_existing=True,
    )

    # Run backfill once, 15 seconds after startup (non-blocking)
    from datetime import timedelta
    _scheduler.add_job(
        backfill_historical_prices,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=15),
        id="backfill_historical",
        name="Historical Price Backfill",
        replace_existing=True,
    )

    # Warm the breadth cache once, 30 seconds after startup, if cold
    # (non-blocking; skips the network-heavy computation if already cached).
    _scheduler.add_job(
        breadth_service.ensure_breadth_warm,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=30),
        id="breadth_warm_start",
        name="Market Breadth Startup Warm-up",
        replace_existing=True,
    )

    # Price alert evaluation every 5 minutes.
    _scheduler.add_job(
        evaluate_price_alerts,
        "interval",
        minutes=5,
        id="price_alert_evaluation",
        name="Price Alert Evaluation",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler started with EOD snapshot, breadth, and backfill jobs")


def shutdown_scheduler() -> None:
    """Shut down the scheduler gracefully."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
    _scheduler = None
