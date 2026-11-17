import os
import threading
import uuid
from datetime import datetime, timezone

import pytest

from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.maintenance import MaintenanceRun, get_marker, set_marker
from app.models.price import PriceSnapshot
from app.schemas import asset as asset_schemas
from app.schemas import market as market_schemas

NEW_CODES = ["ILS", "HKD", "CNY", "KRW", "TWD", "SGD", "INR",
             "SEK", "DKK", "NOK", "AUD", "CAD", "ZAR"]


def test_enums_include_ils_and_il():
    assert Currency("ILS") is Currency.ILS
    assert MarketRegion("IL") is MarketRegion.IL
    assert asset_schemas.Currency("ILS")
    assert asset_schemas.MarketRegion("IL")
    assert market_schemas.MarketRegion("IL")
    for code in NEW_CODES:
        assert Currency(code).name == code
        assert asset_schemas.Currency(code).value == code


def test_cutover_marker_written_once(db_session):
    first = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)
    set_marker(db_session, "m", first)
    set_marker(db_session, "m", datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert db_session.query(MaintenanceRun).filter_by(name="m").count() == 1
    assert get_marker(db_session, "m").replace(tzinfo=timezone.utc) == first
    assert get_marker(db_session, "missing") is None


def test_snapshot_stores_ils(db_session):
    asset = Asset(id=str(uuid.uuid4()), symbol="TEVA.TA", name="Teva", exchange="TLV",
                  currency=Currency.ILS, asset_type=AssetType.STOCK,
                  market_region=MarketRegion.IL)
    db_session.add(asset)
    db_session.add(PriceSnapshot(id=str(uuid.uuid4()), asset_id=asset.id, price=64.0,
                                 currency=Currency.ILS,
                                 timestamp=datetime.now(timezone.utc), source="t"))
    db_session.commit()
    assert db_session.query(PriceSnapshot).one().currency == Currency.ILS


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL not set")
def test_pg_migrate_enum_values_on_old_db():
    from sqlalchemy import create_engine
    from app.database import _migrate_enum_values

    url = os.environ["TEST_POSTGRES_URL"]
    schema = f"enum_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.exec_driver_sql(f"CREATE SCHEMA {schema}")
        c.exec_driver_sql(
            f"CREATE TYPE {schema}.currency AS ENUM ('USD','EUR','GBP','JPY','CHF')")
        c.exec_driver_sql(
            f"CREATE TYPE {schema}.marketregion AS ENUM ('US','EU','ASIA')")
        c.exec_driver_sql(
            f"CREATE TABLE {schema}.assets (id text primary key, "
            f"currency {schema}.currency, region {schema}.marketregion)")
    scoped = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    errors = []

    def run():
        try:
            _migrate_enum_values(scoped)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    try:
        threads = [threading.Thread(target=run) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors, errors
        with admin.connect() as c:
            cur = [r[0] for r in c.exec_driver_sql(
                f"SELECT unnest(enum_range(NULL::{schema}.currency))::text")]
            reg = [r[0] for r in c.exec_driver_sql(
                f"SELECT unnest(enum_range(NULL::{schema}.marketregion))::text")]
        for code in NEW_CODES:
            assert cur.count(code) == 1
        assert len(cur) == len(set(cur)) == 5 + len(NEW_CODES)
        assert reg.count("IL") == 1
    finally:
        scoped.dispose()
        with admin.connect() as c:
            c.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
        admin.dispose()
