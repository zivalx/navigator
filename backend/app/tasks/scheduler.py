import logging
import uuid
from datetime import datetime, timezone

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func

from ..database import SessionLocal
from ..models.asset import Asset, Currency
from ..models.alert import AlertRule, PriceAlert
from ..models.holding import HoldingLot
from ..models.maintenance import ALERTS_CONVERTED_MARKER, CUTOVER_MARKER, get_marker
from ..models.price import PriceSnapshot
from ..models.portfolio_snapshot import PortfolioSnapshot
from ..services.currency_units import normalize_quote_currency
from ..services.market_data import MarketDataService
from ..services import breadth as breadth_service
from ..services.notifications import NotificationService, format_alert_message
from ..services.log_redaction import redact_secrets
from ..services.portfolio import PortfolioService

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
}

NEW_YORK = "America/New_York"
# Cron jobs run in New York time; everything else in UTC. A missed run within
# misfire_grace_time still runs (once: coalesce); later than that it is skipped.
JOB_DEFAULTS = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 120}
DAILY_MISFIRE_SECONDS = 3600
LEADER_STARTUP_MISFIRE_SECONDS = 300


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
                # Don't record a stale db_cache fallback as a fresh EOD close —
                # that would fabricate today's history from an old price.
                if quote.get("stale"):
                    logger.info(
                        "EOD snapshot: skipping %s, only a stale price available",
                        asset.symbol,
                    )
                    continue
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

        # After per-asset EOD prices are stored, roll up today's portfolio row.
        try:
            svc = PortfolioService(db, base_currency=Currency.USD)
            await svc.compute_daily_snapshot(datetime.now(timezone.utc).date())
        except Exception as e:
            logger.warning("Daily portfolio snapshot failed: %s", e)
    except Exception as e:
        logger.error("EOD snapshot job error: %s", e)
    finally:
        db.close()


async def backfill_daily_snapshots_job() -> None:
    """One-off: populate portfolio_snapshots from price history if empty."""
    db = SessionLocal()
    try:
        if db.query(PortfolioSnapshot).first() is not None:
            return  # already populated
        svc = PortfolioService(db, base_currency=Currency.USD)
        written = await svc.backfill_daily_snapshots()
        logger.info("Daily P&L backfill wrote %d rows", written)
    except Exception as e:
        logger.error("Daily P&L backfill error: %s", e)
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
                    # Crypto trades on Yahoo as "<SYM>-USD" (e.g. BTC-USD).
                    # Using the bare ticker ("BTC") returns an unrelated ~$35
                    # equity, which poisons NAV/daily-P&L history — so map
                    # crypto symbols to the -USD pair.
                    yahoo_symbol = asset.symbol.upper()
                    if asset.asset_type.value == "crypto" and "-" not in yahoo_symbol:
                        yahoo_symbol = f"{yahoo_symbol}-USD"
                    url = YAHOO_CHART_URL.format(symbol=yahoo_symbol)
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
                    currency, factor = normalize_quote_currency(
                        result[0].get("meta", {}).get("currency")
                    )

                    snapshots = []
                    for ts, close in zip(timestamps, closes):
                        if close is None:
                            continue
                        snapshots.append(
                            PriceSnapshot(
                                id=str(uuid.uuid4()),
                                asset_id=asset.id,
                                price=round(close * factor, 6) if factor != 1.0 else close,
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

    The notification pass runs even when no alerts are active, so deliveries
    that failed on a previous cycle are still retried.
    """
    db = SessionLocal()
    try:
        # Alerts created before the minor-unit cutover hold thresholds in the
        # old unit (agorot/pence); until the audit converts them, a quote with
        # a unit factor or a stale cache fallback (no unit info) can't be
        # compared safely, so those alerts are paused. The markers are read
        # BEFORE the alerts are loaded: if the audit commits its conversion
        # while this run awaits quotes, the rows loaded here are still in the
        # old unit, so the pause must hold for the whole run.
        cutover = get_marker(db, CUTOVER_MARKER)
        pause_active = (
            cutover is not None
            and get_marker(db, ALERTS_CONVERTED_MARKER) is None
        )
        if pause_active and cutover.tzinfo is None:
            cutover = cutover.replace(tzinfo=timezone.utc)

        alerts = (
            db.query(PriceAlert)
            .filter(PriceAlert.is_active.is_(True))
            .all()
        )
        if not alerts:
            await notify_triggered_alerts(db)
            return

        symbols = sorted({a.asset.symbol for a in alerts if a.asset})
        if not symbols:
            await notify_triggered_alerts(db)
            return

        market_service = MarketDataService(db)
        try:
            quotes = await market_service.get_quotes(symbols)
        except Exception as e:
            logger.error("Price alert evaluation: quote fetch failed: %s", e)
            await notify_triggered_alerts(db)
            return

        paused_symbols: set[str] = set()

        triggered_count = 0
        dirty = False
        for alert in alerts:
            if not alert.asset:
                continue

            quote = quotes.get(alert.asset.symbol)
            if not quote or quote.get("error") or quote.get("price") is None:
                continue

            if pause_active and (
                quote.get("unitFactor", 1.0) != 1.0 or quote.get("stale")
            ):
                created = alert.created_at
                if created is not None and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                # NULL created_at counts as pre-cutover (same as the audit).
                if created is None or created < cutover:
                    paused_symbols.add(alert.asset.symbol)
                    continue

            price = quote["price"]

            if alert.rule == AlertRule.TRAILING_STOP:
                # Ratchet the high-water mark up, never down. Legacy alerts
                # without a mark start tracking from the current price.
                if alert.high_water_mark is None or price > alert.high_water_mark:
                    alert.high_water_mark = price
                    dirty = True
                stop = alert.stop_price
                hit = stop is not None and price <= stop
            else:
                hit = (
                    (alert.rule == AlertRule.PRICE_BELOW and price <= alert.threshold)
                    or (alert.rule == AlertRule.PRICE_ABOVE and price >= alert.threshold)
                )
            if hit:
                alert.is_active = False
                alert.triggered_at = datetime.now(timezone.utc)
                alert.triggered_price = price
                triggered_count += 1
                dirty = True

        if paused_symbols:
            logger.warning(
                "paused pre-cutover alerts until audit --apply: %s",
                sorted(paused_symbols),
            )
        if dirty:
            db.commit()
        if triggered_count:
            logger.info(
                "Price alert evaluation: %d alert(s) triggered", triggered_count
            )

        await notify_triggered_alerts(db)
    except Exception as e:
        db.rollback()
        logger.error("Price alert evaluation job error: %s", e)
    finally:
        db.close()


async def notify_triggered_alerts(db) -> None:
    """Send Telegram messages for triggered-but-unnotified alerts.

    Picks up alerts whose delivery failed on a previous cycle too, so a
    network blip delays a message by one cycle instead of losing it. When no
    Telegram channel is configured, alerts are stamped notification_skipped_at
    — distinct from notified_at (delivered) so a transient config gap is
    auditable, while still keeping the backlog from blasting out if a bot is
    configured later. Per-alert commits are intentional: a delivered message
    has its notified_at persisted even if a later alert's send fails, keeping
    notifications at-most-once. Errors never propagate into the evaluation
    loop.
    """
    pending_ids = [
        row[0]
        for row in db.query(PriceAlert.id)
        .filter(
            PriceAlert.triggered_at.isnot(None),
            PriceAlert.notified_at.is_(None),
            PriceAlert.notification_skipped_at.is_(None),
        )
        .order_by(PriceAlert.triggered_at, PriceAlert.id)
        .all()
    ]
    if not pending_ids:
        db.rollback()  # end the read transaction
        return

    service = NotificationService()
    for alert_id in pending_ids:
        try:
            # Re-select under a row lock: a concurrent pass (e.g. two
            # processes that both believe they lead) either holds the row,
            # or already stamped it. Either way we skip instead of resending.
            alert = (
                db.query(PriceAlert)
                .filter(
                    PriceAlert.id == alert_id,
                    PriceAlert.notified_at.is_(None),
                    PriceAlert.notification_skipped_at.is_(None),
                )
                .with_for_update(skip_locked=True)
                .first()
            )
            if alert is None:
                db.rollback()
                continue
            if service.enabled:
                sent = await service.send(format_alert_message(alert))
                if not sent:
                    db.rollback()  # release the row lock; retry next cycle
                    continue
                alert.notified_at = datetime.now(timezone.utc)
            else:
                alert.notification_skipped_at = datetime.now(timezone.utc)
                logger.warning(
                    "Alert %s triggered but no notification channel is "
                    "configured — marked skipped, will NOT be delivered later",
                    alert.id,
                )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(
                "Alert notification failed for %s: %s", alert_id, redact_secrets(e)
            )


async def leader_startup() -> None:
    """Runs once per new leadership, shortly after the lock is taken. Each
    step runs even if an earlier one failed; every step is idempotent."""
    for step in (
        backfill_historical_prices,
        backfill_daily_snapshots_job,  # after prices: it needs price history
        breadth_service.ensure_breadth_warm,
    ):
        try:
            await step()
        except Exception as e:
            logger.error("leader_startup: %s failed: %s", step.__name__, redact_secrets(e))


def build_scheduler(startup_at: datetime | None = None) -> AsyncIOScheduler:
    """The leader's scheduler with every job added, not started. With
    `startup_at`, `leader_startup` is added as a date job at that time."""
    scheduler = AsyncIOScheduler(timezone="UTC", job_defaults=JOB_DEFAULTS)

    # Daily EOD snapshot at 16:30 New York (after market close)
    scheduler.add_job(
        snapshot_eod_prices,
        "cron",
        hour=16,
        minute=30,
        timezone=NEW_YORK,
        id="eod_snapshot",
        name="EOD Price Snapshot",
        misfire_grace_time=DAILY_MISFIRE_SECONDS,
    )

    # Daily market breadth computation (S5FI/S5TH) at 17:00 New York,
    # shortly after the EOD snapshot.
    scheduler.add_job(
        breadth_service.compute_and_store_breadth,
        "cron",
        hour=17,
        minute=0,
        timezone=NEW_YORK,
        id="breadth_snapshot",
        name="Market Breadth Computation (S5FI/S5TH)",
        misfire_grace_time=DAILY_MISFIRE_SECONDS,
    )

    # Price alert evaluation every 5 minutes (default misfire grace, 120 s).
    scheduler.add_job(
        evaluate_price_alerts,
        "interval",
        minutes=5,
        id="price_alert_evaluation",
        name="Price Alert Evaluation",
    )

    if startup_at is not None:
        scheduler.add_job(
            leader_startup,
            "date",
            run_date=startup_at,
            id="leader_startup",
            name="Leader Startup (backfills, breadth warm-up)",
            misfire_grace_time=LEADER_STARTUP_MISFIRE_SECONDS,
        )

    return scheduler
