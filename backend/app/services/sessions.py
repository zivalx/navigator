"""Trading sessions, alertable-price rule and trade-date helper.

Pure functions, except `watched_assets` (one read query). Holidays are
deliberately not modelled: the consumers are built to be holiday-safe without
a calendar.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Mapping, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models.alert import PriceAlert
from ..models.asset import Asset, AssetType, MarketRegion
from ..models.holding import HoldingLot

Exchange = Literal["US", "TASE", "crypto", "other"]

_US_DAYS = {d: (time(9, 30), time(16, 0)) for d in range(5)}
# TASE trades Monday-Friday since 5 January 2026 (no Sunday session).
# UNVERIFIED against tase.co.il: the exact close times below are data constants
# taken from the design spec; adjust here if TASE's schedule differs.
_TASE_DAYS = {
    **{d: (time(9, 59), time(17, 25)) for d in range(4)},
    4: (time(9, 59), time(14, 0)),
}

SESSION_TABLE: dict[str, tuple[str, dict[int, tuple[time, time]]]] = {
    "US": ("America/New_York", _US_DAYS),
    "TASE": ("Asia/Jerusalem", _TASE_DAYS),
}

ALERTABLE_SOURCES = {
    "stock": {"yahoo"},
    "etf": {"yahoo"},
    "index": {"yahoo"},
    "crypto": {"coingecko", "yahoo"},
}

OTHER_MAX_AGE = timedelta(hours=96)


def classify(asset: Asset) -> Exchange:
    if asset.asset_type == AssetType.CRYPTO:
        return "crypto"
    if asset.market_region == MarketRegion.IL or (asset.symbol or "").upper().endswith(".TA"):
        return "TASE"
    if asset.market_region == MarketRegion.US:
        return "US"
    return "other"


def session_bounds(exchange: str, day: date) -> Optional[tuple[datetime, datetime]]:
    """Aware-UTC (open, close) of the session on local date `day`, or None."""
    entry = SESSION_TABLE.get(exchange)
    if entry is None:
        return None
    tzname, days = entry
    hours = days.get(day.weekday())
    if hours is None:
        return None
    tz = ZoneInfo(tzname)
    o, c = hours
    return (
        datetime.combine(day, o, tzinfo=tz).astimezone(timezone.utc),
        datetime.combine(day, c, tzinfo=tz).astimezone(timezone.utc),
    )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_in_session(exchange: str, now: datetime) -> bool:
    entry = SESSION_TABLE.get(exchange)
    if entry is None:
        return True  # crypto and other
    now = _aware(now)
    bounds = session_bounds(exchange, now.astimezone(ZoneInfo(entry[0])).date())
    if bounds is None:  # no session that weekday: not in session
        return False
    return bounds[0] <= now < bounds[1]


def latest_session_open(exchange: str, now: datetime) -> Optional[datetime]:
    """Open of the most recent session that has started (walks back <= 7 days)."""
    entry = SESSION_TABLE.get(exchange)
    if entry is None:
        return None
    now = _aware(now)
    local_day = now.astimezone(ZoneInfo(entry[0])).date()
    for back in range(8):
        bounds = session_bounds(exchange, local_day - timedelta(days=back))
        if bounds is not None and bounds[0] <= now:
            return bounds[0]
    return None


def latest_session_close(exchange: str, now: datetime) -> Optional[datetime]:
    """Close of the most recent session that has already ended at `now` (walks
    back <= 7 days); None for exchanges without a table entry."""
    entry = SESSION_TABLE.get(exchange)
    if entry is None:
        return None
    now = _aware(now)
    local_day = now.astimezone(ZoneInfo(entry[0])).date()
    for back in range(8):
        bounds = session_bounds(exchange, local_day - timedelta(days=back))
        if bounds is not None and bounds[1] <= now:
            return bounds[1]
    return None


def parse_market_time(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return _aware(value)
        return _aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def is_alertable(quote: dict, asset: Asset, now: datetime) -> tuple[bool, str]:
    atype = asset.asset_type.value if hasattr(asset.asset_type, "value") else asset.asset_type
    allowed = ALERTABLE_SOURCES.get(atype)
    if allowed is None:  # no live source (fund/other): never evaluated, never failing
        return False, "not_monitored"
    if quote.get("error"):
        return False, "error"
    if quote.get("stale"):
        return False, "stale"
    source = quote.get("source")
    if source == "db_cache":
        return False, "db_cache"
    if source not in allowed:
        return False, "source"
    exchange = classify(asset)
    market_time = parse_market_time(quote.get("marketTime"))
    if exchange == "crypto":
        if source == "yahoo" and market_time is None:
            return False, "no_market_time"
        return True, "ok"
    if market_time is None:
        return False, "no_market_time"
    now = _aware(now)
    if exchange == "other":
        return (True, "ok") if now - market_time <= OTHER_MAX_AGE else (False, "old_market_time")
    opened = latest_session_open(exchange, now)
    if opened is None or market_time < opened:
        return False, "old_market_time"
    return True, "ok"


def trade_date_for(quote: dict, asset: Asset, now: datetime) -> date:
    now = _aware(now)
    exchange = classify(asset)
    if exchange == "crypto":
        return now.astimezone(timezone.utc).date()
    market_time = parse_market_time(quote.get("marketTime")) or now
    tzname = quote.get("exchangeTimezone")
    if not tzname:
        tzname = SESSION_TABLE.get(exchange, ("UTC",))[0]
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        tz = ZoneInfo(SESSION_TABLE.get(exchange, ("UTC",))[0])
    return market_time.astimezone(tz).date()


# Neutral outcomes never count for or against a price job: funds have no live
# source, and `old_market_time` is what a holiday looks like (the quote is the
# previous session's), which must pause alerts, not page anyone.
NEUTRAL_REASONS = frozenset({"not_monitored", "old_market_time"})


class QuoteBatchError(RuntimeError):
    """Too many watched symbols came back unusable for a price job to count as healthy."""


def watched_assets(db: Session) -> list[Asset]:
    """Assets that are held or have an active alert, each once."""
    held = db.query(HoldingLot.asset_id)
    alerted = db.query(PriceAlert.asset_id).filter(PriceAlert.is_active.is_(True))
    return (
        db.query(Asset)
        .filter(or_(Asset.id.in_(held), Asset.id.in_(alerted)))
        .order_by(Asset.symbol)
        .all()
    )


def check_quote_batch(reasons: Mapping[str, str]) -> None:
    """Raise `QuoteBatchError` when a batch of per-symbol `is_alertable` reasons
    is unhealthy: at least one counted symbol and either none is "ok" or more
    than half are not. Neutral reasons are left out of the count. The message
    holds counts per reason only (no symbols)."""
    counted = [r for r in reasons.values() if r not in NEUTRAL_REASONS]
    if not counted:
        return
    bad = sum(1 for r in counted if r != "ok")
    if bad == len(counted) or bad * 2 > len(counted):
        counts = Counter(counted)
        detail = " ".join(f"{k}={counts[k]}" for k in sorted(counts))
        raise QuoteBatchError(
            f"{bad} of {len(counted)} watched symbols unusable ({detail})"
        )
