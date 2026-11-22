import logging
from collections import Counter
from datetime import date, datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

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
from ..services.eod import eod_quote_time, upsert_eod
from ..services.sessions import (
    SESSION_TABLE,
    check_quote_batch,
    classify,
    is_alertable,
    is_in_session,
    session_bounds,
    trade_date_for,
    watched_assets,
)
from ..services.market_data import MarketDataService
from ..services import breadth as breadth_service
from ..services.notifications import NotificationService, format_alert_message
from ..services.log_redaction import redact_secrets
from ..services.portfolio import PortfolioService
from .heartbeat import heartbeat

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


def _utcnow() -> datetime:
    """Current aware-UTC time. Jobs read the clock through this so tests can patch it."""
    return datetime.now(timezone.utc)


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
        skipped: Counter = Counter()
        portfolio_failed = False

        for asset_id in asset_ids:
            asset = db.query(Asset).filter(Asset.id == asset_id).first()
            if not asset:
                continue

            try:
                quote = await market_service.get_quote(
                    asset.symbol, asset.asset_type.value
                )
                # EOD eligibility is its own check (not alert eligibility):
                # skipped quotes are counted by reason, not errors.
                now = _utcnow()
                quote_ts, reason = eod_quote_time(quote)
                if quote_ts is None:
                    skipped[reason] += 1
                    continue
                dated = {**quote, "marketTime": quote_ts}
                upsert_eod(
                    db,
                    asset,
                    price=quote["price"],
                    currency=quote.get("currency", "USD"),
                    ts=now if classify(asset) == "crypto" else quote_ts,
                    trade_date=trade_date_for(dated, asset, now),
                    source="eod_snapshot",
                    overwrite=True,
                )
                db.commit()
                success_count += 1
            except Exception as e:
                db.rollback()
                error_count += 1
                logger.warning(
                    "EOD snapshot failed for %s: %s", asset.symbol, redact_secrets(e)
                )

        logger.info(
            "EOD snapshot complete: %d succeeded, %d failed, %d skipped %s",
            success_count,
            error_count,
            sum(skipped.values()),
            dict(sorted(skipped.items())),
        )

        # After per-asset EOD prices are stored, roll up today's portfolio row.
        try:
            svc = PortfolioService(db, base_currency=Currency.USD)
            await svc.compute_daily_snapshot(_utcnow().date())
        except Exception as e:
            portfolio_failed = True
            logger.warning("Daily portfolio snapshot failed: %s", redact_secrets(e))

        # Every asset was attempted; now fail the job so its heartbeat shows it.
        if error_count or portfolio_failed:
            message = f"EOD snapshot: {error_count} asset(s) failed"
            if portfolio_failed:
                message += ", daily portfolio row failed"
            raise RuntimeError(message)
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
        logger.error("Daily P&L backfill error: %s", redact_secrets(e))
    finally:
        db.close()


CATCHUP_DEFAULT_DAYS = 183  # Yahoo's old 6mo range
CATCHUP_MAX_DAYS = 400
CATCHUP_OVERLAP_DAYS = 5
PORTFOLIO_CATCHUP_DAYS = 30


def _catchup_start(last_trade_date: date | None, now: datetime) -> datetime:
    """Start of the Yahoo window: five days before the asset's latest EOD
    trade date (midnight UTC), else 183 days back; never earlier than 400."""
    if last_trade_date is None:
        start = now - timedelta(days=CATCHUP_DEFAULT_DAYS)
    else:
        start = datetime.combine(
            last_trade_date - timedelta(days=CATCHUP_OVERLAP_DAYS),
            dt_time(0, 0),
            tzinfo=timezone.utc,
        )
    return max(start, now - timedelta(days=CATCHUP_MAX_DAYS))


def _bar_zone(meta: dict, exchange: str) -> ZoneInfo:
    name = meta.get("exchangeTimezoneName") or SESSION_TABLE.get(exchange, ("UTC",))[0]
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def _bar_row_time(
    exchange: str, trade_date: date, bar_ts: datetime, local_today: date, now: datetime
) -> datetime | None:
    """The `ts` to store for a daily bar, or None if the bar is not a finished
    session day (today's in-progress bar, a day with no session)."""
    if trade_date > local_today:
        return None
    if exchange == "crypto":
        if trade_date >= now.astimezone(timezone.utc).date():
            return None
        return datetime.combine(trade_date, dt_time(23, 59, 59), tzinfo=timezone.utc)
    if exchange == "other":
        return None if trade_date >= local_today else bar_ts
    bounds = session_bounds(exchange, trade_date)
    if bounds is None:  # no session that day: nothing to record
        return None
    close = bounds[1]
    if trade_date == local_today and now < close:
        return None
    return close


async def _catchup_asset(db, client: httpx.AsyncClient, asset: Asset, now: datetime) -> int:
    """Insert the missing EOD rows for one asset from Yahoo daily bars; never
    overwrites. Returns the number of bars offered to the insert."""
    last = (
        db.query(func.max(PriceSnapshot.trade_date))
        .filter(PriceSnapshot.asset_id == asset.id, PriceSnapshot.trade_date.isnot(None))
        .scalar()
    )
    start = _catchup_start(last, now)
    # Crypto trades on Yahoo as "<SYM>-USD" (e.g. BTC-USD). The bare ticker
    # ("BTC") is an unrelated ~$35 equity that would poison NAV history.
    yahoo_symbol = asset.symbol.upper()
    if asset.asset_type.value == "crypto" and "-" not in yahoo_symbol:
        yahoo_symbol = f"{yahoo_symbol}-USD"
    response = await client.get(
        YAHOO_CHART_URL.format(symbol=yahoo_symbol),
        params={
            "interval": "1d",
            "period1": int(start.timestamp()),
            "period2": int(now.timestamp()),
        },
        headers=YAHOO_HEADERS,
    )
    response.raise_for_status()
    result = response.json().get("chart", {}).get("result")
    if not result:
        logger.warning("EOD catch-up: no Yahoo data for %s", asset.symbol)
        return 0

    meta = result[0].get("meta", {})
    timestamps = result[0].get("timestamp") or []
    closes = (result[0].get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    currency, factor = normalize_quote_currency(meta.get("currency"))
    exchange = classify(asset)
    zone = ZoneInfo("UTC") if exchange == "crypto" else _bar_zone(meta, exchange)
    local_today = now.astimezone(zone).date()

    written = 0
    for raw_ts, close in zip(timestamps, closes):
        if close is None:
            continue
        bar_ts = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
        trade_date = bar_ts.astimezone(zone).date()
        row_ts = _bar_row_time(exchange, trade_date, bar_ts, local_today, now)
        if row_ts is None:
            continue
        upsert_eod(
            db,
            asset,
            price=round(close * factor, 6) if factor != 1.0 else close,
            currency=currency,
            ts=row_ts,
            trade_date=trade_date,
            source="eod_backfill",
            overwrite=False,
        )
        written += 1
    db.commit()
    return written


def _expected_on(asset: Asset, day: date) -> bool:
    """Should `asset` have an EOD row for `day`? Crypto trades daily; US/TASE
    only on session days (no session -> no row expected, F12); `other` has no
    calendar, so a row is never required."""
    exchange = classify(asset)
    if exchange == "crypto":
        return True
    if exchange == "other":
        return False
    return session_bounds(exchange, day) is not None


async def _portfolio_catchup(db, asset_ids: list[str], failed_ids: set[str], now: datetime) -> int:
    """Compute portfolio rows for recent UTC days, oldest first. A stored row is
    permanent, so a day qualifies only when it is fully priced: some held asset
    has an EOD row on D, every EOD-priceable held asset in session on D has one,
    and no asset whose fetch failed this run is expected on D. Never-priceable
    assets (no EOD rows at all) don't block. Existing rows are never recomputed.
    Skipped while the table is empty (`backfill_daily_pnl` owns the initial
    fill). One try per day; returns the number of failed days."""
    if db.query(PortfolioSnapshot.id).first() is None:
        return 0
    today = now.astimezone(timezone.utc).date()
    first = today - timedelta(days=PORTFOLIO_CATCHUP_DAYS)
    have_rows: dict[date, set[str]] = {}
    for asset_id, day in (
        db.query(PriceSnapshot.asset_id, PriceSnapshot.trade_date)
        .filter(
            PriceSnapshot.asset_id.in_(asset_ids),
            PriceSnapshot.trade_date >= first,
            PriceSnapshot.trade_date < today,
        )
        .distinct()
    ):
        have_rows.setdefault(day, set()).add(asset_id)
    have_portfolio = {
        row[0]
        for row in db.query(PortfolioSnapshot.date).filter(
            PortfolioSnapshot.date >= first, PortfolioSnapshot.date < today
        )
    }
    priceable = {
        row[0]
        for row in db.query(PriceSnapshot.asset_id)
        .filter(PriceSnapshot.asset_id.in_(asset_ids), PriceSnapshot.trade_date.isnot(None))
        .distinct()
    }
    assets = {a.id: a for a in db.query(Asset).filter(Asset.id.in_(asset_ids))}
    svc = PortfolioService(db, base_currency=Currency.USD)
    failed_days = 0
    for day in sorted(set(have_rows) - have_portfolio):
        present = have_rows[day]
        blocked = any(
            _expected_on(asset, day)
            and (aid in failed_ids or (aid in priceable and aid not in present))
            for aid, asset in assets.items()
        )
        if blocked:
            logger.info("Portfolio catch-up: skipping %s, prices incomplete", day)
            continue
        try:
            await svc.compute_daily_snapshot(day)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            failed_days += 1
            logger.warning("Portfolio catch-up failed for %s: %s", day, redact_secrets(e))
    return failed_days


async def eod_catchup() -> None:
    """Fill missed end-of-day days from Yahoo daily bars (never overwriting),
    then the portfolio rows they enable. Everything possible is written first;
    the job raises at the end if any asset or the portfolio phase failed."""
    now = _utcnow()
    db = SessionLocal()
    try:
        asset_ids = [row[0] for row in db.query(HoldingLot.asset_id).distinct().all()]
        if not asset_ids:
            logger.info("EOD catch-up: no holdings found, skipping")
            return

        failed = 0
        failed_ids: set[str] = set()
        inserted = 0
        async with httpx.AsyncClient(timeout=15.0) as client:
            for asset_id in asset_ids:
                asset = db.query(Asset).filter(Asset.id == asset_id).first()
                if not asset:
                    continue
                try:
                    inserted += await _catchup_asset(db, client, asset, now)
                except Exception as e:  # noqa: BLE001
                    db.rollback()
                    failed += 1
                    failed_ids.add(asset_id)
                    logger.warning(
                        "EOD catch-up failed for %s: %s", asset.symbol, redact_secrets(e)
                    )

        portfolio_failed = 0
        try:
            portfolio_failed = await _portfolio_catchup(db, asset_ids, failed_ids, now)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            portfolio_failed = 1
            logger.warning("Portfolio catch-up failed: %s", redact_secrets(e))

        logger.info("EOD catch-up complete: %d bars offered, %d asset(s) failed", inserted, failed)
        if failed or portfolio_failed:
            message = f"EOD catch-up: {failed} asset(s) failed"
            if portfolio_failed:
                message += f", {portfolio_failed} portfolio day(s) failed"
            raise RuntimeError(message)
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
            logger.error("Price alert evaluation: quote fetch failed: %s", redact_secrets(e))
            await notify_triggered_alerts(db)  # retries still go out
            raise

        paused_symbols: set[str] = set()
        now = _utcnow()
        # symbol -> is_alertable reason: the unit of the batch failure rule.
        reasons: dict[str, str] = {}

        triggered_count = 0
        dirty = False
        for alert in alerts:
            if not alert.asset:
                continue

            symbol = alert.asset.symbol
            quote = quotes.get(symbol) or {"error": "no quote"}
            alertable, reason = is_alertable(quote, alert.asset, now)
            if alertable and quote.get("price") is None:
                alertable, reason = False, "error"
            reasons[symbol] = reason
            if not alertable:
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
        skipped = Counter(r for r in reasons.values() if r != "ok")
        if skipped:
            logger.warning(
                "non-alertable quotes skipped: %s",
                " ".join(f"{k}={skipped[k]}" for k in sorted(skipped)),
            )
        if dirty:
            db.commit()
        if triggered_count:
            logger.info(
                "Price alert evaluation: %d alert(s) triggered", triggered_count
            )

        await notify_triggered_alerts(db)
        check_quote_batch(reasons)  # after the notify pass, so retries still go out
    except Exception:
        db.rollback()
        raise  # the heartbeat wrapper logs and records it
    finally:
        db.close()


async def quote_refresh() -> None:
    """Keep quotes (and the intraday snapshots the quote path writes) fresh for
    held and alerted assets whose exchange is in session. Writes nothing itself;
    raises `QuoteBatchError` under the price-job failure rule."""
    now = _utcnow()
    db = SessionLocal()
    try:
        assets = [a for a in watched_assets(db) if is_in_session(classify(a), now)]
        if not assets:
            logger.info("quote_refresh: no watched assets in session")
            return
        quotes = await MarketDataService(db).get_quotes([a.symbol for a in assets])
        reasons = {}
        for asset in assets:
            quote = quotes.get(asset.symbol) or {"error": "no quote"}
            reasons[asset.symbol] = is_alertable(quote, asset, now)[1]
        logger.info(
            "quote_refresh: %d symbol(s), %d ok",
            len(reasons),
            sum(1 for r in reasons.values() if r == "ok"),
        )
        check_quote_batch(reasons)
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
        heartbeat("eod_catchup")(eod_catchup),
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
        heartbeat("eod_snapshot")(snapshot_eod_prices),
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
        heartbeat("breadth_snapshot")(breadth_service.compute_and_store_breadth),
        "cron",
        hour=17,
        minute=0,
        timezone=NEW_YORK,
        id="breadth_snapshot",
        name="Market Breadth Computation (S5FI/S5TH)",
        misfire_grace_time=DAILY_MISFIRE_SECONDS,
    )

    # Daily catch-up of missed EOD days at 17:15 New York.
    scheduler.add_job(
        heartbeat("eod_catchup")(eod_catchup),
        "cron",
        hour=17,
        minute=15,
        timezone=NEW_YORK,
        id="eod_catchup",
        name="EOD Catch-up",
        misfire_grace_time=DAILY_MISFIRE_SECONDS,
    )

    # Quote refresh every 5 minutes (default misfire grace, 120 s).
    scheduler.add_job(
        heartbeat("quote_refresh")(quote_refresh),
        "interval",
        minutes=5,
        id="quote_refresh",
        name="Quote Refresh",
    )

    # Price alert evaluation every 5 minutes, 30 s after the refresh so it
    # rides the 60 s quote cache.
    scheduler.add_job(
        heartbeat("price_alert_evaluation")(evaluate_price_alerts),
        "interval",
        minutes=5,
        start_date=_utcnow() + timedelta(seconds=30),
        id="price_alert_evaluation",
        name="Price Alert Evaluation",
    )

    if startup_at is not None:
        scheduler.add_job(
            heartbeat("leader_startup")(leader_startup),
            "date",
            run_date=startup_at,
            id="leader_startup",
            name="Leader Startup (backfills, breadth warm-up)",
            misfire_grace_time=LEADER_STARTUP_MISFIRE_SECONDS,
        )

    return scheduler
