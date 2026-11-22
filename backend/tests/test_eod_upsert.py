"""upsert_eod: one EOD row per (asset, trade_date); quote snapshots untouched.

The check_* helpers take any session so the Postgres lane
(test_pg_migrations.test_pg_upsert_eod) runs the same semantics at head.
"""
import uuid
from datetime import date, datetime, timezone

from app.models.price import PriceSnapshot
from app.services.eod import upsert_eod
from tests.factories import make_asset

D = date(2026, 11, 20)
TS1 = datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc)
TS2 = datetime(2026, 11, 20, 21, 5, tzinfo=timezone.utc)


def _utc(dt: datetime) -> datetime:
    # SQLite drops tzinfo on round-trip; Postgres returns it.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _rows(db, asset):
    db.expire_all()
    return db.query(PriceSnapshot).filter_by(asset_id=asset.id).all()


def check_overwrite_updates_price_and_ts(db):
    asset = make_asset(db, symbol="OVR")
    upsert_eod(db, asset, 100.0, "USD", TS1, D, "eod_backfill", overwrite=True)
    upsert_eod(db, asset, 101.0, "USD", TS2, D, "eod_snapshot", overwrite=True)
    db.commit()
    rows = _rows(db, asset)
    assert len(rows) == 1
    row = rows[0]
    assert row.price == 101.0 and _utc(row.timestamp) == TS2
    assert row.source == "eod_snapshot" and row.trade_date == D


def check_do_nothing_keeps_existing(db):
    asset = make_asset(db, symbol="DNO")
    upsert_eod(db, asset, 100.0, "USD", TS1, D, "eod_snapshot", overwrite=True)
    upsert_eod(db, asset, 101.0, "USD", TS2, D, "eod_backfill", overwrite=False)
    db.commit()
    rows = _rows(db, asset)
    assert len(rows) == 1
    assert rows[0].price == 100.0 and _utc(rows[0].timestamp) == TS1
    assert rows[0].source == "eod_snapshot"


def check_quote_snapshots_keep_null_trade_date(db):
    asset = make_asset(db, symbol="QTE")
    for price in (100.0, 100.5):
        db.add(PriceSnapshot(id=str(uuid.uuid4()), asset_id=asset.id, price=price,
                             currency="USD", timestamp=TS1, source="yahoo"))
    db.commit()
    rows = _rows(db, asset)
    assert len(rows) == 2 and all(r.trade_date is None for r in rows)


def test_upsert_eod_overwrite_updates_price_and_ts(db_session):
    check_overwrite_updates_price_and_ts(db_session)


def test_upsert_eod_do_nothing_keeps_existing(db_session):
    check_do_nothing_keeps_existing(db_session)


def test_quote_snapshots_keep_null_trade_date(db_session):
    check_quote_snapshots_keep_null_trade_date(db_session)


def test_upsert_eod_keys_on_asset_and_date(db_session):
    a, b = make_asset(db_session, symbol="AAA"), make_asset(db_session, symbol="BBB")
    upsert_eod(db_session, a, 1.0, "USD", TS1, D, "eod_snapshot", overwrite=True)
    upsert_eod(db_session, a, 2.0, "USD", TS1, date(2026, 11, 19), "eod_snapshot", overwrite=True)
    upsert_eod(db_session, b, 3.0, "USD", TS1, D, "eod_snapshot", overwrite=True)
    db_session.commit()
    assert db_session.query(PriceSnapshot).count() == 3
