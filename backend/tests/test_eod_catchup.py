"""eod_catchup: fills missed EOD days from Yahoo daily bars, never overwrites."""
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price import PriceSnapshot
from app.services import breadth as breadth_service
from app.services.portfolio import PortfolioService
from app.tasks import heartbeat as heartbeat_module
from app.tasks import leader
from app.tasks import scheduler as scheduler_module
from app.tasks.scheduler import _catchup_start, build_scheduler, eod_catchup
from tests.factories import make_asset

NY_META = {"currency": "USD", "exchangeTimezoneName": "America/New_York"}
MON, TUE, WED, THU, FRI = (date(2026, 11, d) for d in (16, 17, 18, 19, 20))
FRI_AFTER_CLOSE = datetime(2026, 11, 20, 22, 0, tzinfo=timezone.utc)


def _utc(d: date, hour=14, minute=30) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc)


def _body(meta, bars):
    """bars: [(datetime, close)] -> Yahoo chart body."""
    return {"chart": {"result": [{
        "meta": meta,
        "timestamp": [int(t.timestamp()) for t, _ in bars],
        "indicators": {"quote": [{"close": [c for _, c in bars]}]},
    }]}}


def _us_bars(days, close=100.0):
    return [(_utc(d), close + i) for i, d in enumerate(days)]


@pytest.fixture()
def env(db_session, monkeypatch):
    """Patches the clock, the DB session and the Yahoo transport. Returns a
    setter for the handler plus the list of requests seen."""
    state = {"handler": lambda r: httpx.Response(200, json=_body(NY_META, [])), "requests": []}
    real_client = httpx.AsyncClient

    def handler(request):
        state["requests"].append(request)
        return state["handler"](request)

    monkeypatch.setattr(
        scheduler_module.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(scheduler_module, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    monkeypatch.setattr(scheduler_module, "_utcnow", lambda: state.get("now", FRI_AFTER_CLOSE))
    return state


def _hold(db, symbol="AAA", **kw):
    asset = make_asset(db, symbol=symbol, **kw)
    db.add(HoldingLot(
        id=str(uuid.uuid4()), asset_id=asset.id, quantity=1, avg_cost=10,
        cost_currency=asset.currency, account_name="a",
        purchase_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db.commit()
    return asset


def _eod_row(db, asset, day, price=50.0, source="eod_snapshot"):
    db.add(PriceSnapshot(
        id=str(uuid.uuid4()), asset_id=asset.id, price=price, currency=Currency.USD,
        timestamp=_utc(day, 21, 0), source=source, trade_date=day,
    ))
    db.commit()


def _rows(db, asset):
    db.expire_all()
    return (
        db.query(PriceSnapshot)
        .filter(PriceSnapshot.asset_id == asset.id, PriceSnapshot.trade_date.isnot(None))
        .order_by(PriceSnapshot.trade_date)
        .all()
    )


@pytest.mark.asyncio
async def test_catchup_fills_three_day_gap(db_session, env):
    asset = _hold(db_session)
    _eod_row(db_session, asset, MON)
    _eod_row(db_session, asset, TUE)
    env["handler"] = lambda r: httpx.Response(
        200, json=_body(NY_META, _us_bars([MON, TUE, WED, THU, FRI]))
    )
    await eod_catchup()
    assert {r.trade_date for r in _rows(db_session, asset)} >= {WED, THU, FRI}


@pytest.mark.asyncio
async def test_catchup_requests_period_window(db_session, env):
    asset = _hold(db_session)
    _eod_row(db_session, asset, TUE)
    await eod_catchup()
    params = dict(env["requests"][0].url.params)
    assert "range" not in params and params["interval"] == "1d"
    expected = datetime(2026, 11, 12, tzinfo=timezone.utc)  # TUE - 5 days, midnight UTC
    assert int(params["period1"]) == int(expected.timestamp())
    assert int(params["period2"]) == int(FRI_AFTER_CLOSE.timestamp())


@pytest.mark.asyncio
async def test_catchup_does_not_overwrite_existing_row(db_session, env):
    asset = _hold(db_session)
    _eod_row(db_session, asset, THU, price=50.0)
    env["handler"] = lambda r: httpx.Response(200, json=_body(NY_META, _us_bars([THU, FRI], 999.0)))
    await eod_catchup()
    rows = {r.trade_date: r for r in _rows(db_session, asset)}
    assert rows[THU].price == 50.0 and rows[THU].source == "eod_snapshot"
    assert rows[FRI].price == 1000.0 and rows[FRI].source == "eod_backfill"


@pytest.mark.asyncio
async def test_catchup_skips_todays_in_progress_bar(db_session, env):
    asset = _hold(db_session)
    env["now"] = datetime(2026, 11, 20, 20, 0, tzinfo=timezone.utc)  # Fri 15:00 ET
    env["handler"] = lambda r: httpx.Response(200, json=_body(NY_META, _us_bars([THU, FRI])))
    await eod_catchup()
    assert [r.trade_date for r in _rows(db_session, asset)] == [THU]


@pytest.mark.asyncio
async def test_catchup_row_ts_is_session_close(db_session, env):
    asset = _hold(db_session)
    env["handler"] = lambda r: httpx.Response(200, json=_body(NY_META, _us_bars([THU])))
    await eod_catchup()
    [row] = _rows(db_session, asset)
    ts = row.timestamp if row.timestamp.tzinfo else row.timestamp.replace(tzinfo=timezone.utc)
    assert ts == datetime(2026, 11, 19, 21, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_catchup_crypto_and_other_rules(db_session, env):
    btc = _hold(db_session, symbol="BTC")
    btc.asset_type = AssetType.CRYPTO
    fund = _hold(db_session, symbol="FND", market_region=MarketRegion.EU)
    db_session.commit()
    env["now"] = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    utc_meta = {"currency": "USD", "exchangeTimezoneName": "UTC"}
    bars = [(_utc(WED, 0, 0), 1.0), (_utc(THU, 0, 0), 2.0), (_utc(FRI, 0, 0), 3.0)]
    env["handler"] = lambda r: httpx.Response(200, json=_body(utc_meta, bars))
    await eod_catchup()
    assert any(r.url.path.endswith("/BTC-USD") for r in env["requests"])
    crypto = _rows(db_session, btc)
    assert [r.trade_date for r in crypto] == [WED, THU]  # current UTC day skipped
    ts = crypto[0].timestamp
    assert (ts.hour, ts.minute, ts.second) == (23, 59, 59)
    other = _rows(db_session, fund)
    assert [r.trade_date for r in other] == [WED, THU]  # today always skipped
    assert other[0].timestamp.replace(tzinfo=None) == bars[0][0].replace(tzinfo=None)


@pytest.mark.asyncio
async def test_catchup_skips_bar_on_day_without_session(db_session, env):
    asset = _hold(db_session)
    sat = date(2026, 11, 14)
    env["handler"] = lambda r: httpx.Response(200, json=_body(NY_META, [(_utc(sat), 5.0), (_utc(THU), 6.0)]))
    await eod_catchup()
    assert [r.trade_date for r in _rows(db_session, asset)] == [THU]


@pytest.mark.asyncio
async def test_catchup_computes_portfolio_only_for_missing_day(db_session, env, monkeypatch):
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    env["now"] = now
    asset = _hold(db_session)
    d2, d1 = date(2026, 11, 20), date(2026, 11, 21)
    _eod_row(db_session, asset, d2)
    _eod_row(db_session, asset, d1)
    db_session.add(PortfolioSnapshot(id=str(uuid.uuid4()), date=d2, nav=1.0))
    db_session.commit()
    computed = []

    async def fake(self, day):
        computed.append(day)

    monkeypatch.setattr(PortfolioService, "compute_daily_snapshot", fake)
    await eod_catchup()
    assert computed == [d1]


@pytest.mark.asyncio
async def test_catchup_skips_portfolio_phase_while_table_empty(db_session, env, monkeypatch):
    asset = _hold(db_session)
    _eod_row(db_session, asset, THU)
    computed = []

    async def fake(self, day):
        computed.append(day)

    monkeypatch.setattr(PortfolioService, "compute_daily_snapshot", fake)
    await eod_catchup()
    assert computed == []


@pytest.mark.asyncio
async def test_catchup_failure_writes_others_then_raises(db_session, env):
    bad = _hold(db_session, symbol="BAD")
    good = _hold(db_session, symbol="GOOD")

    def handler(request):
        if request.url.path.endswith("/BAD"):
            return httpx.Response(500)
        return httpx.Response(200, json=_body(NY_META, _us_bars([THU])))

    env["handler"] = handler
    with pytest.raises(RuntimeError, match="1 asset"):
        await eod_catchup()
    assert [r.trade_date for r in _rows(db_session, good)] == [THU]
    assert _rows(db_session, bad) == []


@pytest.mark.asyncio
async def test_catchup_closes_normalized(db_session, env):
    asset = Asset(
        id=str(uuid.uuid4()), symbol="TEVA.TA", name="Teva",
        exchange="TLV", currency=Currency.ILS,
        asset_type=AssetType.STOCK, market_region=MarketRegion.US,
    )
    db_session.add(asset)
    db_session.add(HoldingLot(
        id=str(uuid.uuid4()), asset_id=asset.id, quantity=1, avg_cost=60,
        cost_currency=Currency.ILS, account_name="a",
        purchase_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db_session.commit()
    meta = {"currency": "ILA", "exchangeTimezoneName": "Asia/Jerusalem"}
    bars = [(_utc(WED, 7, 0), 6400), (_utc(THU, 7, 0), 6512)]
    env["handler"] = lambda r: httpx.Response(200, json=_body(meta, bars))
    await eod_catchup()
    rows = _rows(db_session, asset)
    assert [r.price for r in rows] == pytest.approx([64.0, 65.12])
    assert all(r.currency == Currency.ILS for r in rows)


def test_catchup_start_rules():
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    assert _catchup_start(date(2026, 11, 19), now) == datetime(2026, 11, 14, tzinfo=timezone.utc)
    assert _catchup_start(None, now) == now - timedelta(days=183)
    assert _catchup_start(date(2020, 1, 1), now) == now - timedelta(days=400)


@pytest.mark.asyncio
async def test_leader_startup_order(db_session, monkeypatch):
    monkeypatch.setattr(leader, "is_leader", lambda: True)
    monkeypatch.setattr(heartbeat_module, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    calls = []

    def recorder(name):
        async def step():
            calls.append(name)
        return step

    monkeypatch.setattr(scheduler_module, "eod_catchup", recorder("eod_catchup"))
    monkeypatch.setattr(scheduler_module, "backfill_daily_snapshots_job", recorder("backfill_daily_pnl"))
    monkeypatch.setattr(breadth_service, "ensure_breadth_warm", recorder("breadth_warm"))
    await scheduler_module.leader_startup()
    assert calls == ["eod_catchup", "backfill_daily_pnl", "breadth_warm"]


def test_eod_catchup_job_registered():
    job = build_scheduler().get_job("eod_catchup")
    assert job is not None and job.misfire_grace_time == 3600
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "17" and fields["minute"] == "15"
    assert str(job.trigger.timezone) == "America/New_York"


def _record_computed(monkeypatch):
    computed = []

    async def fake(self, day):
        computed.append(day)

    monkeypatch.setattr(PortfolioService, "compute_daily_snapshot", fake)
    return computed


def _portfolio_row(db, day):
    db.add(PortfolioSnapshot(id=str(uuid.uuid4()), date=day, nav=1.0))
    db.commit()


@pytest.mark.asyncio
async def test_catchup_waits_for_every_priceable_asset(db_session, env, monkeypatch):
    env["now"] = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    a, b = _hold(db_session, symbol="AAA"), _hold(db_session, symbol="BBB")
    _eod_row(db_session, a, THU)
    _eod_row(db_session, a, FRI)
    _eod_row(db_session, b, THU)  # BBB is priceable but has no Friday row yet
    _portfolio_row(db_session, THU)
    computed = _record_computed(monkeypatch)
    await eod_catchup()
    assert computed == []
    _eod_row(db_session, b, FRI)  # a later run fills the missing bar
    await eod_catchup()
    assert computed == [FRI]


@pytest.mark.asyncio
async def test_catchup_never_priceable_asset_does_not_block(db_session, env, monkeypatch):
    env["now"] = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    a, _unpriceable = _hold(db_session, symbol="AAA"), _hold(db_session, symbol="NEW")
    _eod_row(db_session, a, THU)
    _eod_row(db_session, a, FRI)
    _portfolio_row(db_session, THU)
    computed = _record_computed(monkeypatch)
    await eod_catchup()
    assert computed == [FRI]


@pytest.mark.asyncio
async def test_catchup_failed_asset_blocks_its_days(db_session, env, monkeypatch):
    env["now"] = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    a, b = _hold(db_session, symbol="AAA"), _hold(db_session, symbol="BAD")
    for asset in (a, b):
        _eod_row(db_session, asset, THU)
        _eod_row(db_session, asset, FRI)
    _portfolio_row(db_session, WED)
    env["handler"] = lambda r: httpx.Response(500 if r.url.path.endswith("/BAD") else 200, json=_body(NY_META, []))
    computed = _record_computed(monkeypatch)
    with pytest.raises(RuntimeError, match="1 asset"):
        await eod_catchup()
    assert computed == []


@pytest.mark.asyncio
async def test_catchup_one_bad_day_does_not_stop_later_days(db_session, env, monkeypatch):
    env["now"] = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    a = _hold(db_session)
    _eod_row(db_session, a, THU)
    _eod_row(db_session, a, FRI)
    _portfolio_row(db_session, WED)
    computed = []

    async def flaky(self, day):
        computed.append(day)
        if day == THU:
            raise RuntimeError("boom")

    monkeypatch.setattr(PortfolioService, "compute_daily_snapshot", flaky)
    with pytest.raises(RuntimeError, match="1 portfolio day"):
        await eod_catchup()
    assert computed == [THU, FRI]


@pytest.mark.asyncio
async def test_catchup_conflicting_remapped_row_is_kept(db_session, env):
    """A weekend-stamped row that migration 0002 moved to Friday keeps its price
    and timestamp when the catch-up's Friday bar conflicts with it."""
    asset = _hold(db_session)
    sat = datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc)
    db_session.add(PriceSnapshot(
        id=str(uuid.uuid4()), asset_id=asset.id, price=50.0, currency=Currency.USD,
        timestamp=sat, source="eod_snapshot", trade_date=FRI,
    ))
    db_session.commit()
    env["handler"] = lambda r: httpx.Response(200, json=_body(NY_META, _us_bars([FRI], 999.0)))
    await eod_catchup()
    [row] = _rows(db_session, asset)
    assert row.trade_date == FRI and row.price == 50.0 and row.source == "eod_snapshot"
    assert row.timestamp.replace(tzinfo=None) == sat.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_catchup_us_dst_transition_week(db_session, env):
    asset = _hold(db_session)
    env["now"] = datetime(2026, 3, 13, 22, 0, tzinfo=timezone.utc)
    fri_est, mon_edt = date(2026, 3, 6), date(2026, 3, 9)  # DST began Sun 8 March
    bars = [(datetime(2026, 3, 6, 14, 30, tzinfo=timezone.utc), 1.0),
            (datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc), 2.0)]
    env["handler"] = lambda r: httpx.Response(200, json=_body(NY_META, bars))
    await eod_catchup()
    rows = _rows(db_session, asset)
    assert [r.trade_date for r in rows] == [fri_est, mon_edt]
    stamps = [r.timestamp.replace(tzinfo=timezone.utc) for r in rows]
    assert stamps == [datetime(2026, 3, 6, 21, 0, tzinfo=timezone.utc),
                      datetime(2026, 3, 9, 20, 0, tzinfo=timezone.utc)]


@pytest.mark.asyncio
async def test_catchup_tase_trade_date_and_close(db_session, env):
    asset = _hold(db_session, symbol="TEVA.TA", currency=Currency.ILS,
                  exchange="TLV", market_region=MarketRegion.IL)
    meta = {"currency": "ILS", "exchangeTimezoneName": "Asia/Jerusalem"}
    bars = [(_utc(THU, 7, 0), 10.0), (_utc(FRI, 7, 0), 11.0)]
    env["handler"] = lambda r: httpx.Response(200, json=_body(meta, bars))
    await eod_catchup()
    rows = _rows(db_session, asset)
    assert [r.trade_date for r in rows] == [THU, FRI]
    stamps = [r.timestamp.replace(tzinfo=timezone.utc) for r in rows]
    assert stamps == [datetime(2026, 11, 19, 15, 25, tzinfo=timezone.utc),  # Thu 17:25 IST
                      datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)]   # Fri 14:00 IST
