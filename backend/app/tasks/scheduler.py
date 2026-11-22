import logging
from collections import Counter
from datetime import date, datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..database import SessionLocal
from ..models.asset import Asset, Currency
from ..models.alert import AlertRule, PriceAlert
from ..models.holding import HoldingLot
from ..models.job_heartbeat import JobHeartbeat
from ..models.maintenance import ALERTS_CONVERTED_MARKER, CUTOVER_MARKER, get_marker
from ..models.price import PriceSnapshot
from ..models.portfolio_snapshot import PortfolioSnapshot
from ..services.currency_units import normalize_quote_currency
from ..services.eod import eod_quote_time, upsert_eod
from ..services.sessions import (
    ALERTABLE_SOURCES,
    NEUTRAL_REASONS,
    SESSION_TABLE,
    check_quote_batch,
    classify,
    is_alertable,
    is_in_session,
    latest_session_close,
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


async def snapshot_eod_prices(only_closed: bool = False) -> None:
    """Take end-of-day price snapshots for all held assets. With `only_closed`
    (the startup run, which can fall anywhere in the day) an asset is written
    only if its own exchange is closed and the quote is from at/after that
    exchange's latest close; always-open exchanges (crypto, other) are
    written as usual."""
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
        failed_ids: set[str] = set()
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
                if only_closed:
                    exchange = classify(asset)
                    if exchange in SESSION_TABLE:
                        if is_in_session(exchange, now):
                            skipped["exchange_open"] += 1
                            continue
                        close = latest_session_close(exchange, now)
                        if close is None or quote_ts < close:
                            skipped["before_close"] += 1
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
                failed_ids.add(asset_id)
                logger.warning(
                    "EOD snapshot failed for %s (asset %s): %s", asset.symbol, asset_id, redact_secrets(e)
                )

        logger.info(
            "EOD snapshot complete: %d succeeded, %d failed, %d skipped %s",
            success_count,
            error_count,
            sum(skipped.values()),
            dict(sorted(skipped.items())),
        )

        # After per-asset EOD prices are stored, roll up today's portfolio row,
        # but only when every held asset that should be priced today is: a
        # stored row is permanent, a stale carried-forward price would stick.
        # A skipped day is filled by the next eod_catchup.
        try:
            now = _utcnow()
            today = now.astimezone(timezone.utc).date()
            unpriced = _unpriced_for_day(db, asset_ids, today, failed_ids)
            if unpriced is None:
                logger.info("EOD snapshot: no asset has a price for %s, no portfolio row", today)
            elif unpriced:
                logger.info(
                    "EOD snapshot: portfolio row for %s skipped, %d asset(s) not priced yet",
                    today, unpriced,
                )
            else:
                svc = PortfolioService(db, base_currency=Currency.USD)
                try:
                    await svc.compute_daily_snapshot(today)
                except IntegrityError:
                    # the startup run and the 16:30 cron overlapped: the other
                    # one stored today's row first
                    db.rollback()
                    logger.info("EOD snapshot: portfolio row for %s already written", today)
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
CATCHUP_RECENT_DAYS = 30  # the window always reaches at least this far back
PORTFOLIO_CATCHUP_DAYS = 30


def _catchup_start(
    first_trade_date: date | None, last_trade_date: date | None, now: datetime
) -> datetime:
    """Start of the Yahoo window. Normally five days before the asset's latest
    EOD trade date, but never later than 30 days back (an outage longer than
    the overlap must not leave a permanent gap). An asset with no rows, or whose
    earliest row is newer than 183 days (a new holding, or one the snapshot job
    only just started pricing), is fetched 183 days back so its history fills.
    Never earlier than 400 days."""
    today = now.astimezone(timezone.utc).date()
    full = now - timedelta(days=CATCHUP_DEFAULT_DAYS)
    if (
        last_trade_date is None
        or first_trade_date is None
        or first_trade_date > today - timedelta(days=CATCHUP_DEFAULT_DAYS)
    ):
        start = full
    else:
        anchor = min(
            last_trade_date - timedelta(days=CATCHUP_OVERLAP_DAYS),
            today - timedelta(days=CATCHUP_RECENT_DAYS),
        )
        start = datetime.combine(anchor, dt_time(0, 0), tzinfo=timezone.utc)
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


async def _catchup_asset(
    db, client: httpx.AsyncClient, asset: Asset, now: datetime
) -> int | None:
    """Insert the missing EOD rows for one asset from Yahoo daily bars; never
    overwrites. Returns the number of bars offered to the insert, or None when
    Yahoo has no data for the asset (HTTP 404 or a chart.error body: delisted,
    or listed under another id). Other HTTP errors raise."""
    first, last = (
        db.query(func.min(PriceSnapshot.trade_date), func.max(PriceSnapshot.trade_date))
        .filter(PriceSnapshot.asset_id == asset.id, PriceSnapshot.trade_date.isnot(None))
        .one()
    )
    start = _catchup_start(first, last, now)
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
    if response.status_code == 404:
        return None
    if 400 <= response.status_code < 500:
        try:
            error_body = response.json()
        except ValueError:
            error_body = None
        if isinstance(error_body, dict) and (error_body.get("chart") or {}).get("error"):
            return None  # Yahoo says it has no such symbol
    response.raise_for_status()
    chart = response.json().get("chart") or {}
    result = chart.get("result")
    if chart.get("error") or not result:
        return None

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


def _first_purchase_dates(db, asset_ids: list[str]) -> dict[str, date | None]:
    """Earliest lot purchase date (UTC) per asset; None = held since forever."""
    first: dict[str, date | None] = {}
    for asset_id, purchased in db.query(HoldingLot.asset_id, HoldingLot.purchase_date).filter(
        HoldingLot.asset_id.in_(asset_ids)
    ):
        day = None
        if purchased is not None:
            if purchased.tzinfo is None:
                purchased = purchased.replace(tzinfo=timezone.utc)
            day = purchased.astimezone(timezone.utc).date()
        if asset_id not in first or day is None or (first[asset_id] is not None and day < first[asset_id]):
            first[asset_id] = day
    return first


def _day_blocked(
    day: date,
    assets: dict[str, Asset],
    first_bought: dict[str, date | None],
    priceable: set[str],
    present: set[str],
    failed_ids: set[str],
    no_data_ids: frozenset[str] = frozenset(),
) -> bool:
    """Is a portfolio row for `day` not yet safe to store? True when a held
    asset (one with a lot bought on or before `day`) that should have an EOD
    row that day is missing it: its fetch failed this run, or it is EOD-priceable
    (has rows) but has none on `day`. Never-priceable assets and assets Yahoo has
    no data for don't block."""
    for aid, asset in assets.items():
        bought = first_bought.get(aid)
        if bought is not None and bought > day:
            continue  # not held on `day`
        if not _expected_on(asset, day):
            continue
        if aid in failed_ids:
            return True
        if aid in priceable and aid not in present and aid not in no_data_ids:
            return True
    return False


def _unpriced_for_day(db, asset_ids: list[str], day: date, failed_ids: set[str]) -> int | None:
    """For `eod_snapshot`: how many held assets block today's portfolio row
    (0 = go ahead), or None when no held asset has a row for `day` at all."""
    present = {
        row[0]
        for row in db.query(PriceSnapshot.asset_id).filter(
            PriceSnapshot.asset_id.in_(asset_ids), PriceSnapshot.trade_date == day
        )
    }
    if not present:
        return None
    priceable = {
        row[0]
        for row in db.query(PriceSnapshot.asset_id)
        .filter(PriceSnapshot.asset_id.in_(asset_ids), PriceSnapshot.trade_date.isnot(None))
        .distinct()
    }
    assets = {a.id: a for a in db.query(Asset).filter(Asset.id.in_(asset_ids))}
    first_bought = _first_purchase_dates(db, asset_ids)
    return sum(
        1
        for aid, asset in assets.items()
        if _day_blocked(day, {aid: asset}, first_bought, priceable, present, failed_ids)
    )


async def _portfolio_catchup(
    db,
    asset_ids: list[str],
    failed_ids: set[str],
    now: datetime,
    no_data_ids: frozenset[str] = frozenset(),
) -> int:
    """Compute portfolio rows for recent UTC days, oldest first. A stored row is
    permanent, so a day qualifies only when it is fully priced: some held asset
    has an EOD row on D and no held asset is blocking it (see `_day_blocked`).
    Existing rows are never recomputed. Skipped while the table is empty
    (`backfill_daily_pnl` owns the initial fill). One try per day; returns the
    number of failed days."""
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
    first_bought = _first_purchase_dates(db, asset_ids)
    svc = PortfolioService(db, base_currency=Currency.USD)
    failed_days = 0
    for day in sorted(set(have_rows) - have_portfolio):
        if _day_blocked(
            day, assets, first_bought, priceable, have_rows[day], failed_ids, no_data_ids
        ):
            logger.info("Portfolio catch-up: skipping %s, prices incomplete", day)
            continue
        try:
            await svc.compute_daily_snapshot(day)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            failed_days += 1
            logger.warning("Portfolio catch-up failed for %s: %s", day, redact_secrets(e))
    return failed_days


async def _portfolio_fill_earlier(db, asset_ids: list[str]) -> int:
    """Insert the portfolio days before the earliest stored row, back to the
    earliest lot purchase (a holding added after the first fill, or an outage
    longer than the catch-up window). Insert-only: no stored row is touched.
    Nothing to do while the table is empty (`backfill_daily_pnl` owns that).
    Returns the number of rows written."""
    earliest_row = db.query(func.min(PortfolioSnapshot.date)).scalar()
    first_bought = [d for d in _first_purchase_dates(db, asset_ids).values() if d is not None]
    if earliest_row is None or not first_bought or min(first_bought) >= earliest_row:
        return 0
    svc = PortfolioService(db, base_currency=Currency.USD)
    return await svc.backfill_daily_snapshots(before=earliest_row, since=min(first_bought))


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
        no_data_ids: set[str] = set()
        inserted = 0
        async with httpx.AsyncClient(timeout=15.0) as client:
            for asset_id in asset_ids:
                asset = db.query(Asset).filter(Asset.id == asset_id).first()
                if not asset:
                    continue
                try:
                    offered = await _catchup_asset(db, client, asset, now)
                except Exception as e:  # noqa: BLE001
                    db.rollback()
                    failed += 1
                    failed_ids.add(asset_id)
                    logger.warning(
                        "EOD catch-up failed for %s (asset %s): %s", asset.symbol, asset_id, redact_secrets(e)
                    )
                    continue
                if offered is None:
                    no_data_ids.add(asset_id)
                    logger.info("EOD catch-up: no Yahoo data for %s (asset %s)", asset.symbol, asset_id)
                else:
                    inserted += offered

        portfolio_failed = 0
        try:
            portfolio_failed = await _portfolio_catchup(
                db, asset_ids, failed_ids, now, frozenset(no_data_ids)
            )
        except Exception as e:  # noqa: BLE001
            db.rollback()
            portfolio_failed = 1
            logger.warning("Portfolio catch-up failed: %s", redact_secrets(e))
        try:
            filled = await _portfolio_fill_earlier(db, asset_ids)
            if filled:
                logger.info("Portfolio catch-up: filled %d earlier day(s)", filled)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            portfolio_failed += 1
            logger.warning("Earlier portfolio days failed: %s", redact_secrets(e))

        logger.info(
            "EOD catch-up complete: %d bars offered, %d asset(s) failed, %d without data",
            inserted, failed, len(no_data_ids),
        )
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

            if pause_active and quote.get("unitFactor", 1.0) != 1.0:
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
        # Neutral outcomes (a holiday's previous-session quote, a fund) are
        # expected: INFO, not part of the warning count.
        skipped = Counter(r for r in reasons.values() if r != "ok" and r not in NEUTRAL_REASONS)
        neutral = Counter(r for r in reasons.values() if r in NEUTRAL_REASONS)
        if skipped:
            logger.warning(
                "non-alertable quotes skipped: %s",
                " ".join(f"{k}={skipped[k]}" for k in sorted(skipped)),
            )
        if neutral:
            logger.info(
                "quotes not evaluated (neutral): %s",
                " ".join(f"{k}={neutral[k]}" for k in sorted(neutral)),
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
        # Only types with a live alert source: funds have none (not_monitored),
        # so fetching them every poll would be wasted calls.
        assets = [
            a for a in watched_assets(db)
            if a.asset_type.value in ALERTABLE_SOURCES and is_in_session(classify(a), now)
        ]
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


EOD_SNAPSHOT_HOUR, EOD_SNAPSHOT_MINUTE = 16, 30


def _latest_eod_slot(now: datetime) -> datetime:
    """The most recent 16:30 New York slot on a weekday that is not after `now`
    (aware UTC). Holidays are not modelled."""
    zone = ZoneInfo(NEW_YORK)
    local_day = now.astimezone(zone).date()
    for back in range(8):
        day = local_day - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        slot = datetime.combine(
            day, dt_time(EOD_SNAPSHOT_HOUR, EOD_SNAPSHOT_MINUTE), tzinfo=zone
        ).astimezone(timezone.utc)
        if slot <= now:
            return slot
    raise AssertionError("unreachable: a weekday slot exists within 8 days")


def _eod_snapshot_missed(db, now: datetime) -> bool:
    """True when the US session is closed and eod_snapshot has not succeeded
    since the latest 16:30 New York slot that is already past (a leader change
    rebuilds the in-memory jobstore, so a slot that fell in the gap never
    fires). Never true while the session is open: the quotes would be
    intraday prices, not a close."""
    if is_in_session("US", now):
        return False
    last = (
        db.query(JobHeartbeat.last_success_at)
        .filter(JobHeartbeat.job_name == "eod_snapshot")
        .scalar()
    )
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last < _latest_eod_slot(now)


async def eod_snapshot_if_missed() -> None:
    """leader_startup step: run the heartbeat-wrapped eod_snapshot now when its
    16:30 slot was missed (see `_eod_snapshot_missed`)."""
    db = SessionLocal()
    try:
        missed = _eod_snapshot_missed(db, _utcnow())
    finally:
        db.close()
    if missed:
        logger.info("leader_startup: eod_snapshot slot was missed, running it now")
        async def startup_snapshot() -> None:
            await snapshot_eod_prices(only_closed=True)

        await heartbeat("eod_snapshot")(startup_snapshot)()


async def leader_startup() -> None:
    """Runs once per new leadership, shortly after the lock is taken. Each
    step runs even if an earlier one failed; every step is idempotent."""
    for step in (
        eod_snapshot_if_missed,  # before the catch-up, which would otherwise mis-fill today
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
        hour=EOD_SNAPSHOT_HOUR,
        minute=EOD_SNAPSHOT_MINUTE,
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

    # Quote refresh every quote_refresh_interval_minutes (default misfire grace, 120 s).
    scheduler.add_job(
        heartbeat("quote_refresh")(quote_refresh),
        "interval",
        minutes=settings.quote_refresh_interval_minutes,
        id="quote_refresh",
        name="Quote Refresh",
    )

    # Price alert evaluation every alert_evaluation_interval_minutes, 30 s after
    # the refresh so it rides the 60 s quote cache.
    scheduler.add_job(
        heartbeat("price_alert_evaluation")(evaluate_price_alerts),
        "interval",
        minutes=settings.alert_evaluation_interval_minutes,
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
