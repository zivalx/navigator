"""Keep the stored daily FX rate history (fx_daily_rates) filled and current."""
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.asset import Currency
from ..models.holding import HoldingLot
from ..models.price import FxDailyRate, PriceSnapshot
from ..providers.yahoo import YahooFinanceProvider
from .log_redaction import redact_secrets

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 400
START_PADDING_DAYS = 7
INCREMENTAL_OVERLAP_DAYS = 5


def _as_date(value) -> date | None:
    # SQLite may return naive datetimes; both columns hold UTC.
    return value.date() if value is not None else None


def _needed_start(db: Session, today: date) -> date:
    candidates = [today - timedelta(days=DEFAULT_LOOKBACK_DAYS)]
    lot = _as_date(
        db.query(func.min(HoldingLot.purchase_date))
        .filter(HoldingLot.purchase_date.isnot(None))
        .scalar()
    )
    snap = _as_date(db.query(func.min(PriceSnapshot.timestamp)).scalar())
    candidates.extend(d for d in (lot, snap) if d is not None)
    return min(candidates) - timedelta(days=START_PADDING_DAYS)


async def refresh_fx_history(
    db: Session,
    provider: YahooFinanceProvider | None = None,
    today: date | None = None,
) -> int:
    """Backfill/refresh daily FX rates for every non-USD currency.

    Returns the number of rows inserted or updated.
    """
    today = today or datetime.now(timezone.utc).date()
    owns_provider = provider is None
    provider = provider or YahooFinanceProvider()
    needed_start = _needed_start(db, today)
    total = 0
    try:
        for currency in Currency:
            if currency == Currency.USD:
                continue
            try:
                earliest, latest = (
                    db.query(func.min(FxDailyRate.date), func.max(FxDailyRate.date))
                    .filter(FxDailyRate.currency == currency)
                    .one()
                )
                if earliest is None or earliest > needed_start + timedelta(days=START_PADDING_DAYS):
                    start = needed_start
                else:
                    start = latest - timedelta(days=INCREMENTAL_OVERLAP_DAYS)

                rows = await provider.get_fx_history(currency.value, start, today)
                existing = {
                    r.date: r
                    for r in db.query(FxDailyRate).filter(
                        FxDailyRate.currency == currency,
                        FxDailyRate.date >= start,
                    )
                }
                now = datetime.now(timezone.utc)
                count = 0
                for day, per_usd in rows:
                    row = existing.get(day)
                    if row is not None:
                        row.per_usd = per_usd
                        row.fetched_at = now
                    else:
                        row = FxDailyRate(
                            id=str(uuid.uuid4()),
                            currency=currency,
                            date=day,
                            per_usd=per_usd,
                            fetched_at=now,
                        )
                        db.add(row)
                        existing[day] = row
                    count += 1
                db.commit()
                total += count
            except Exception as e:
                db.rollback()
                logger.warning("FX history: %s failed (%s)", currency.value, redact_secrets(e))
    finally:
        if owns_provider:
            await provider.close()
    return total
