"""End-of-day price rows: one per (asset, trade_date), keyed by the partial
unique index uq_price_snapshots_eod. Intraday quote snapshots (trade_date
NULL) are never touched here."""
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from ..db_upsert import insert_for
from ..models.asset import Asset, Currency
from ..models.price import PriceSnapshot
from .sessions import parse_market_time

EOD_SOURCES = ("eod_snapshot", "eod_backfill")


def eod_quote_time(quote: dict) -> tuple[Optional[datetime], str]:
    """Is `quote` usable as an EOD close? Returns (timestamp, "ok") or
    (None, reason). Unlike alert eligibility this does not care whether the
    asset is monitored or which provider answered: a fund, or a Polygon or
    Finnhub fallback bar, is still that day's close, on its own trade date.
    The timestamp is marketTime, else the provider's own timestamp (never
    Yahoo's, which is only the fetch time)."""
    if quote.get("price") is None:
        return None, "no_price"
    if quote.get("error"):
        return None, "error"
    if quote.get("stale"):
        return None, "stale"
    if quote.get("source") == "db_cache":
        return None, "db_cache"
    ts = parse_market_time(quote.get("marketTime"))
    if ts is None and quote.get("source") != "yahoo":
        ts = parse_market_time(quote.get("timestamp"))
    if ts is None:
        return None, "no_timestamp"
    return ts, "ok"


def upsert_eod(
    db: Session,
    asset: Asset,
    price: float,
    currency: Currency | str,
    ts: datetime,
    trade_date: date,
    source: str,
    overwrite: bool,
) -> None:
    """Write the EOD row for (asset, trade_date). overwrite=True refreshes price,
    currency and source of an existing row and keeps the EARLIER of the existing
    and new timestamp (reruns never move it later); False leaves the row as is.
    Does not commit."""
    if source not in EOD_SOURCES:
        raise ValueError(f"not an EOD source: {source!r}")
    stmt = insert_for(db)(PriceSnapshot.__table__).values(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        price=price,
        currency=currency,
        timestamp=ts,
        source=source,
        trade_date=trade_date,
    )
    conflict = {
        "index_elements": ["asset_id", "trade_date"],
        "index_where": text("trade_date IS NOT NULL"),
    }
    if overwrite:
        earlier = func.least if db.get_bind().dialect.name == "postgresql" else func.min
        table = PriceSnapshot.__table__
        stmt = stmt.on_conflict_do_update(
            **conflict,
            set_={
                **{col: stmt.excluded[col] for col in ("price", "currency", "source")},
                "timestamp": earlier(table.c.timestamp, stmt.excluded.timestamp),
            },
        )
    else:
        stmt = stmt.on_conflict_do_nothing(**conflict)
    db.execute(stmt)
