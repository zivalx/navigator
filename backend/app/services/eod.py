"""End-of-day price rows: one per (asset, trade_date), keyed by the partial
unique index uq_price_snapshots_eod. Intraday quote snapshots (trade_date
NULL) are never touched here."""
import uuid
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db_upsert import insert_for
from ..models.asset import Asset, Currency
from ..models.price import PriceSnapshot

EOD_SOURCES = ("eod_snapshot", "eod_backfill")


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
    currency, timestamp and source of an existing row; False leaves it as is.
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
        stmt = stmt.on_conflict_do_update(
            **conflict,
            set_={col: stmt.excluded[col] for col in ("price", "currency", "timestamp", "source")},
        )
    else:
        stmt = stmt.on_conflict_do_nothing(**conflict)
    db.execute(stmt)
