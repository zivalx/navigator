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
    _eod_row(db_session, asset, date(2026, 1, 5))  # old history, so the normal overlap rule applies
    _eod_row(db_session, asset, TUE)
    await eod_catchup()
    params = dict(env["requests"][0].url.params)
    assert "range" not in params and params["interval"] == "1d"
    # TUE - 5 days is later than today - 30 days: the 30-day reach wins, midnight UTC
    expected = datetime(2026, 10, 21, tzinfo=timezone.utc)
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
    old = date(2025, 1, 1)  # history older than 183 days
    # a recent last row still reaches 30 days back (the overlap alone is 5)
    assert _catchup_start(old, date(2026, 11, 19), now) == datetime(2026, 10, 23, tzinfo=timezone.utc)
    # a 12-day outage: last row - 5 days is later than today - 30 days
    assert _catchup_start(old, date(2026, 11, 10), now) == datetime(2026, 10, 23, tzinfo=timezone.utc)
    # a longer outage: last row - 5 days wins
    assert _catchup_start(old, date(2026, 8, 1), now) == datetime(2026, 7, 27, tzinfo=timezone.utc)
    # no rows, or history shorter than 183 days: the full 183-day window
    assert _catchup_start(None, None, now) == now - timedelta(days=183)
    assert _catchup_start(date(2026, 11, 20), date(2026, 11, 20), now) == now - timedelta(days=183)
    assert _catchup_start(date(2026, 6, 1), date(2026, 11, 20), now) == now - timedelta(days=183)
    # never earlier than 400 days
    assert _catchup_start(date(2020, 1, 1), date(2020, 1, 1), now) == now - timedelta(days=400)


@pytest.mark.asyncio
async def test_leader_startup_order(db_session, monkeypatch):
    monkeypatch.setattr(leader, "is_leader", lambda: True)
    maker = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(heartbeat_module, "SessionLocal", maker)
    monkeypatch.setattr(scheduler_module, "SessionLocal", maker)
    monkeypatch.setattr(scheduler_module, "_utcnow", lambda: datetime(2026, 11, 18, 16, 0, tzinfo=timezone.utc))
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


def _period1(env, index=0):
    return datetime.fromtimestamp(int(dict(env["requests"][index].url.params)["period1"]), tz=timezone.utc)


@pytest.mark.asyncio
async def test_catchup_new_holding_gets_183_day_window(db_session, env):
    """An asset whose only row is today's (written by the 16:30 snapshot) must
    still fetch its history."""
    asset = _hold(db_session)
    _eod_row(db_session, asset, FRI)
    await eod_catchup()
    assert _period1(env) == FRI_AFTER_CLOSE - timedelta(days=183)


@pytest.mark.asyncio
async def test_catchup_twelve_day_outage_reaches_thirty_days_back(db_session, env):
    asset = _hold(db_session)
    _eod_row(db_session, asset, date(2026, 1, 5))
    _eod_row(db_session, asset, date(2026, 11, 6))  # last row 14 days ago
    await eod_catchup()
    assert _period1(env) == datetime(2026, 10, 21, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_catchup_404_is_no_data_not_failure(db_session, env, caplog):
    gone = _hold(db_session, symbol="GONE")
    good = _hold(db_session, symbol="GOOD")
    env["now"] = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    for asset in (gone, good):
        _eod_row(db_session, asset, THU)
    _portfolio_row(db_session, THU)

    def handler(request):
        if request.url.path.endswith("/GONE"):
            return httpx.Response(404, json={"chart": {"result": None, "error": {"code": "Not Found"}}})
        return httpx.Response(200, json=_body(NY_META, _us_bars([FRI])))

    env["handler"] = handler
    with caplog.at_level("INFO", logger=scheduler_module.logger.name):
        await eod_catchup()  # must not raise
    assert [r.trade_date for r in _rows(db_session, good)] == [THU, FRI]
    assert "GONE" in caplog.text and gone.id in caplog.text  # symbol and asset id are both logged


@pytest.mark.asyncio
async def test_catchup_chart_error_body_is_no_data(db_session, env):
    _hold(db_session, symbol="PEPE24478-USD")
    env["handler"] = lambda r: httpx.Response(
        200, json={"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}
    )
    await eod_catchup()  # must not raise


@pytest.mark.asyncio
async def test_catchup_404_asset_does_not_block_portfolio_days(db_session, env, monkeypatch):
    env["now"] = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    gone, good = _hold(db_session, symbol="GONE"), _hold(db_session, symbol="GOOD")
    for asset in (gone, good):
        _eod_row(db_session, asset, THU)
    _eod_row(db_session, good, FRI)  # the delisted asset never gets a Friday row
    _portfolio_row(db_session, THU)
    env["handler"] = lambda r: (
        httpx.Response(404) if r.url.path.endswith("/GONE")
        else httpx.Response(200, json=_body(NY_META, []))
    )
    computed = _record_computed(monkeypatch)
    await eod_catchup()
    assert computed == [FRI]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_catchup_throttle_and_server_errors_still_fail(db_session, env, status):
    _hold(db_session)
    env["handler"] = lambda r: httpx.Response(status)
    with pytest.raises(RuntimeError, match="1 asset"):
        await eod_catchup()


@pytest.mark.asyncio
async def test_catchup_network_error_still_fails(db_session, env):
    _hold(db_session)

    def boom(request):
        raise httpx.ConnectError("down")

    env["handler"] = boom
    with pytest.raises(RuntimeError, match="1 asset"):
        await eod_catchup()


@pytest.mark.asyncio
async def test_catchup_fills_earlier_portfolio_days_insert_only(db_session, env):
    env["now"] = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    asset = _hold(db_session)
    lot = db_session.query(HoldingLot).one()
    lot.purchase_date = datetime(2026, 11, 16, tzinfo=timezone.utc)
    db_session.commit()
    for day in (MON, TUE, WED, THU):
        _eod_row(db_session, asset, day, price=100.0)
    db_session.add(PortfolioSnapshot(
        id=str(uuid.uuid4()), date=WED, nav=999.0, net_flow=7.0, daily_pnl=5.0, daily_pnl_pct=1.0,
    ))
    db_session.commit()
    await eod_catchup()
    db_session.expire_all()
    rows = {r.date: r for r in db_session.query(PortfolioSnapshot)}
    assert {MON, TUE}.issubset(rows)  # earlier days filled
    assert rows[WED].nav == 999.0 and rows[WED].net_flow == 7.0 and rows[WED].daily_pnl == 5.0  # untouched
    assert min(rows) == MON


@pytest.mark.asyncio
async def test_backfill_with_bounds_never_rewrites_existing_rows(db_session):
    from app.tasks.scheduler import PortfolioService as Svc  # the service the job uses

    asset = _hold(db_session)
    db_session.query(HoldingLot).one().purchase_date = datetime(2026, 11, 16, tzinfo=timezone.utc)
    for day in (MON, TUE, WED):
        _eod_row(db_session, asset, day, price=100.0)
    db_session.add(PortfolioSnapshot(id=str(uuid.uuid4()), date=TUE, nav=999.0))
    db_session.commit()
    written = await Svc(db_session, base_currency=Currency.USD).backfill_daily_snapshots(before=TUE, since=MON)
    db_session.expire_all()
    rows = {r.date: r for r in db_session.query(PortfolioSnapshot)}
    assert written == 1 and set(rows) == {MON, TUE}  # WED is outside the bounds
    assert rows[TUE].nav == 999.0


# ---------------------------------------------------------------------------
# eod_snapshot's portfolio row follows the same coverage rule (I4)
# ---------------------------------------------------------------------------

def _quotes(monkeypatch, failing=()):
    from app.services.market_data import MarketDataService

    async def get_quote(self, symbol, asset_type=None):
        if symbol in failing:
            raise RuntimeError("provider down")
        return {"price": 10.0, "currency": "USD", "source": "yahoo",
                "marketTime": FRI_AFTER_CLOSE.replace(hour=21, minute=0).isoformat()}

    monkeypatch.setattr(MarketDataService, "get_quote", get_quote)


def _bought(db, asset, when):
    db.query(HoldingLot).filter_by(asset_id=asset.id).one().purchase_date = when
    db.commit()


@pytest.mark.asyncio
async def test_eod_snapshot_skips_portfolio_row_when_a_held_asset_failed(db_session, env, monkeypatch):
    from app.tasks.scheduler import snapshot_eod_prices

    a, b = _hold(db_session, symbol="AAA"), _hold(db_session, symbol="BAD")
    for asset in (a, b):
        _eod_row(db_session, asset, THU)  # both priceable
    env["now"] = FRI_AFTER_CLOSE + timedelta(minutes=30)
    _quotes(monkeypatch, failing={"BAD"})
    computed = _record_computed(monkeypatch)
    with pytest.raises(RuntimeError, match="1 asset"):
        await snapshot_eod_prices()
    assert computed == []


@pytest.mark.asyncio
async def test_eod_snapshot_computes_portfolio_row_when_all_priced(db_session, env, monkeypatch):
    from app.tasks.scheduler import snapshot_eod_prices

    _hold(db_session, symbol="AAA"), _hold(db_session, symbol="BBB")
    env["now"] = FRI_AFTER_CLOSE + timedelta(minutes=30)
    _quotes(monkeypatch)
    computed = _record_computed(monkeypatch)
    await snapshot_eod_prices()
    assert computed == [FRI]


@pytest.mark.asyncio
async def test_failed_snapshot_day_is_filled_by_next_catchup(db_session, env, monkeypatch):
    from app.tasks.scheduler import snapshot_eod_prices

    a, b = _hold(db_session, symbol="AAA"), _hold(db_session, symbol="BAD")
    for asset in (a, b):
        _eod_row(db_session, asset, THU)
    _portfolio_row(db_session, THU)
    env["now"] = FRI_AFTER_CLOSE + timedelta(minutes=30)
    _quotes(monkeypatch, failing={"BAD"})
    computed = _record_computed(monkeypatch)
    with pytest.raises(RuntimeError):
        await snapshot_eod_prices()
    assert computed == []
    # the 17:15 catch-up still has no price for BAD: the day stays open
    env["handler"] = lambda r: httpx.Response(200, json=_body(NY_META, []))
    await eod_catchup()
    assert computed == []
    # a later run brings the Friday bar in: now the day is filled
    env["now"] = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    env["handler"] = lambda r: httpx.Response(200, json=_body(NY_META, _us_bars([THU, FRI])))
    await eod_catchup()
    assert computed == [FRI]


@pytest.mark.asyncio
async def test_asset_bought_after_the_day_does_not_block_it(db_session, env, monkeypatch):
    from app.tasks.scheduler import snapshot_eod_prices

    a, late = _hold(db_session, symbol="AAA"), _hold(db_session, symbol="NEW")
    _eod_row(db_session, late, THU)  # priceable, but bought after Friday
    _bought(db_session, late, datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc))
    env["now"] = FRI_AFTER_CLOSE + timedelta(minutes=30)
    _quotes(monkeypatch, failing={"NEW"})
    computed = _record_computed(monkeypatch)
    with pytest.raises(RuntimeError, match="1 asset"):  # NEW's own failure is still reported
        await snapshot_eod_prices()
    assert computed == [FRI]

    # and the catch-up ignores it for Friday too
    env["now"] = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    _portfolio_row(db_session, THU)
    computed.clear()
    _eod_row(db_session, a, THU)  # Friday's row came from the snapshot above
    env["handler"] = lambda r: httpx.Response(200, json=_body(NY_META, []))
    await eod_catchup()
    assert computed == [FRI]


# ---------------------------------------------------------------------------
# leader_startup runs a missed 16:30 eod_snapshot (I3)
# ---------------------------------------------------------------------------

class _Calls(list):
    modes: list

    def __init__(self):
        super().__init__()
        self.modes = []


def _startup(monkeypatch, db_session, now, last_success):
    maker = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(leader, "is_leader", lambda: True)
    monkeypatch.setattr(heartbeat_module, "SessionLocal", maker)
    monkeypatch.setattr(scheduler_module, "SessionLocal", maker)
    monkeypatch.setattr(scheduler_module, "_utcnow", lambda: now)
    if last_success is not None:
        heartbeat_module.upsert_heartbeat("eod_snapshot", last_success_at=last_success)
    calls = _Calls()

    async def snapshot(only_closed=False):
        calls.append("eod_snapshot")
        calls.modes.append(only_closed)

    async def noop():
        return None

    monkeypatch.setattr(scheduler_module, "snapshot_eod_prices", snapshot)
    monkeypatch.setattr(scheduler_module, "eod_catchup", noop)
    monkeypatch.setattr(scheduler_module, "backfill_daily_snapshots_job", noop)
    monkeypatch.setattr(breadth_service, "ensure_breadth_warm", noop)
    return calls


def _ny(day, hour, minute=0):  # November: EST, UTC-5
    return datetime(2026, 11, day, hour + 5, minute, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_startup_runs_missed_eod_snapshot_after_close(db_session, monkeypatch):
    calls = _startup(monkeypatch, db_session, _ny(18, 18), last_success=_ny(17, 16, 35))
    await scheduler_module.leader_startup()
    assert calls == ["eod_snapshot"]
    assert calls.modes == [True]  # the startup run only writes closed exchanges
    db_session.expire_all()
    from app.models.job_heartbeat import JobHeartbeat
    assert db_session.query(JobHeartbeat).filter_by(job_name="eod_snapshot").one().last_success_at is not None


@pytest.mark.asyncio
async def test_startup_never_runs_eod_snapshot_while_us_session_open(db_session, monkeypatch):
    calls = _startup(monkeypatch, db_session, _ny(18, 11), last_success=_ny(16, 16, 35))
    await scheduler_module.leader_startup()
    assert calls == []


@pytest.mark.asyncio
async def test_startup_skips_eod_snapshot_when_success_is_after_the_slot(db_session, monkeypatch):
    calls = _startup(monkeypatch, db_session, _ny(18, 18), last_success=_ny(18, 16, 40))
    await scheduler_module.leader_startup()
    assert calls == []


@pytest.mark.asyncio
async def test_startup_before_todays_slot_checks_yesterdays(db_session, monkeypatch):
    # 16:10 NY: closed, but today's slot is still ahead; yesterday's succeeded
    calls = _startup(monkeypatch, db_session, _ny(18, 16, 10), last_success=_ny(17, 16, 35))
    await scheduler_module.leader_startup()
    assert calls == []


@pytest.mark.asyncio
async def test_startup_weekend_with_friday_missed_runs_it(db_session, monkeypatch):
    calls = _startup(monkeypatch, db_session, datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc),
                     last_success=_ny(19, 16, 35))  # Thursday's run; Friday's slot was missed
    await scheduler_module.leader_startup()
    assert calls == ["eod_snapshot"]


@pytest.mark.asyncio
async def test_startup_weekend_with_friday_done_does_not_run(db_session, monkeypatch):
    calls = _startup(monkeypatch, db_session, datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc),
                     last_success=_ny(20, 16, 35))
    await scheduler_module.leader_startup()
    assert calls == []


@pytest.mark.asyncio
async def test_startup_runs_eod_snapshot_when_it_never_succeeded(db_session, monkeypatch):
    calls = _startup(monkeypatch, db_session, _ny(18, 18), last_success=None)
    await scheduler_module.leader_startup()
    assert calls == ["eod_snapshot"]


# ---------------------------------------------------------------------------
# Task 14: startup snapshot writes only exchanges that are closed
# ---------------------------------------------------------------------------

TUE_0500_NY = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)   # TASE 12:00 IST: open
FRI_1200_NY = datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc)   # US open, TASE closed since 14:00 IST
FRI_1800_NY = datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc)   # both closed
MON_CLOSE_US = datetime(2026, 11, 16, 21, 0, tzinfo=timezone.utc)
FRI_CLOSE_TASE = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _tase(db, symbol="TEVA.TA"):
    return _hold(db, symbol=symbol, currency=Currency.ILS, market_region=MarketRegion.IL, exchange="TASE")


def _quotes_at(monkeypatch, times):
    """symbol -> marketTime of that symbol's (otherwise healthy) quote."""
    from app.services.market_data import MarketDataService

    async def get_quote(self, symbol, asset_type=None):
        return {"price": 10.0, "currency": "ILS" if symbol.endswith(".TA") else "USD",
                "source": "yahoo", "marketTime": times[symbol].isoformat()}

    monkeypatch.setattr(MarketDataService, "get_quote", get_quote)
    _record_computed(monkeypatch)  # no FX needed for the portfolio roll-up


def _written(db):
    db.expire_all()
    rows = db.query(PriceSnapshot).filter(PriceSnapshot.trade_date.isnot(None)).all()
    return {r.asset.symbol: r.trade_date for r in rows}


@pytest.mark.asyncio
async def test_startup_snapshot_skips_tase_during_tase_hours_but_writes_us(db_session, env, monkeypatch):
    from app.tasks.scheduler import snapshot_eod_prices

    _hold(db_session, symbol="AAA"), _tase(db_session)
    env["now"] = TUE_0500_NY
    _quotes_at(monkeypatch, {"AAA": MON_CLOSE_US, "TEVA.TA": TUE_0500_NY})  # TASE: intraday quote
    await snapshot_eod_prices(only_closed=True)
    assert _written(db_session) == {"AAA": MON}


@pytest.mark.asyncio
async def test_startup_snapshot_writes_tase_once_it_has_closed(db_session, env, monkeypatch):
    from app.tasks.scheduler import snapshot_eod_prices

    _hold(db_session, symbol="AAA"), _tase(db_session), _tase(db_session, "EARLY.TA")
    env["now"] = FRI_1800_NY
    _quotes_at(monkeypatch, {
        "AAA": FRI_AFTER_CLOSE.replace(hour=21), "TEVA.TA": FRI_CLOSE_TASE,
        "EARLY.TA": FRI_CLOSE_TASE - timedelta(hours=1),  # quote predates the close
    })
    await snapshot_eod_prices(only_closed=True)
    assert _written(db_session) == {"AAA": FRI, "TEVA.TA": FRI}


@pytest.mark.asyncio
async def test_startup_snapshot_with_us_open_writes_the_closed_tase_row_only(db_session, env, monkeypatch):
    from app.tasks.scheduler import snapshot_eod_prices

    _hold(db_session, symbol="AAA"), _tase(db_session)
    env["now"] = FRI_1200_NY
    _quotes_at(monkeypatch, {"AAA": FRI_1200_NY, "TEVA.TA": FRI_CLOSE_TASE})
    await snapshot_eod_prices(only_closed=True)
    assert _written(db_session) == {"TEVA.TA": FRI}


@pytest.mark.asyncio
async def test_startup_snapshot_still_writes_crypto_and_other(db_session, env, monkeypatch):
    from app.models.asset import AssetType
    from app.services.market_data import MarketDataService
    from app.tasks.scheduler import snapshot_eod_prices

    btc = _hold(db_session, symbol="BTC")
    btc.asset_type = AssetType.CRYPTO
    _hold(db_session, symbol="XYZ", market_region=MarketRegion.EU)
    db_session.commit()
    env["now"] = TUE_0500_NY

    async def get_quote(self, symbol, asset_type=None):
        return {"price": 10.0, "currency": "USD", "source": "yahoo",
                "marketTime": TUE_0500_NY.isoformat()}

    monkeypatch.setattr(MarketDataService, "get_quote", get_quote)
    _record_computed(monkeypatch)
    await snapshot_eod_prices(only_closed=True)
    assert set(_written(db_session)) == {"BTC", "XYZ"}


@pytest.mark.asyncio
async def test_cron_snapshot_still_writes_tase_as_before(db_session, env, monkeypatch):
    from app.tasks.scheduler import snapshot_eod_prices

    _hold(db_session, symbol="AAA"), _tase(db_session)
    env["now"] = TUE_0500_NY
    _quotes_at(monkeypatch, {"AAA": MON_CLOSE_US, "TEVA.TA": TUE_0500_NY})
    await snapshot_eod_prices()
    assert _written(db_session) == {"AAA": MON, "TEVA.TA": TUE}


@pytest.mark.asyncio
async def test_startup_runs_snapshot_in_only_closed_mode(db_session, monkeypatch):
    calls = _startup(monkeypatch, db_session, _ny(18, 18), last_success=None)
    await scheduler_module.leader_startup()
    assert calls == ["eod_snapshot"] and calls.modes == [True]


# ---------------------------------------------------------------------------
# Task 14: residual minors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fill_earlier_creates_a_day_the_30_day_window_cannot_reach(db_session, env):
    from app.tasks.scheduler import _portfolio_catchup, _portfolio_fill_earlier

    env["now"] = FRI_AFTER_CLOSE
    asset = _hold(db_session)
    OCT5, OCT6 = date(2026, 10, 5), date(2026, 10, 6)  # > 30 days before the stored row below
    db_session.query(HoldingLot).one().purchase_date = datetime(2026, 10, 5, tzinfo=timezone.utc)
    for day in (OCT5, OCT6, MON):
        _eod_row(db_session, asset, day, price=100.0)
    db_session.add(PortfolioSnapshot(
        id=str(uuid.uuid4()), date=MON, nav=999.0, net_flow=7.0, daily_pnl=5.0, daily_pnl_pct=1.0,
    ))
    db_session.commit()

    # the recent-window catch-up cannot create the October days ...
    await _portfolio_catchup(db_session, [asset.id], set(), FRI_AFTER_CLOSE)
    assert {r.date for r in db_session.query(PortfolioSnapshot)} == {MON}
    # ... only the earlier-days fill can
    assert await _portfolio_fill_earlier(db_session, [asset.id]) == 2
    db_session.expire_all()
    rows = {r.date: r for r in db_session.query(PortfolioSnapshot)}
    assert set(rows) == {OCT5, OCT6, MON}
    assert (rows[MON].nav, rows[MON].net_flow, rows[MON].daily_pnl) == (999.0, 7.0, 5.0)


@pytest.mark.asyncio
async def test_backfill_with_since_alone_is_insert_only(db_session):
    from app.tasks.scheduler import PortfolioService as Svc

    asset = _hold(db_session)
    db_session.query(HoldingLot).one().purchase_date = datetime(2026, 11, 16, tzinfo=timezone.utc)
    for day in (MON, TUE, WED):
        _eod_row(db_session, asset, day, price=100.0)
    db_session.add(PortfolioSnapshot(id=str(uuid.uuid4()), date=TUE, nav=999.0))
    db_session.commit()
    written = await Svc(db_session, base_currency=Currency.USD).backfill_daily_snapshots(since=MON)
    db_session.expire_all()
    rows = {r.date: r for r in db_session.query(PortfolioSnapshot)}
    assert written == 2 and set(rows) == {MON, TUE, WED}
    assert rows[TUE].nav == 999.0


@pytest.mark.asyncio
async def test_catchup_http_400_with_chart_error_is_no_data(db_session, env):
    _hold(db_session)
    env["handler"] = lambda r: httpx.Response(
        400, json={"chart": {"result": None, "error": {"code": "Bad Request", "description": "Invalid input"}}}
    )
    await eod_catchup()  # must not raise


@pytest.mark.asyncio
async def test_catchup_http_400_without_chart_error_still_fails(db_session, env):
    _hold(db_session)
    env["handler"] = lambda r: httpx.Response(400, json={"message": "nope"})
    with pytest.raises(RuntimeError, match="1 asset"):
        await eod_catchup()


@pytest.mark.asyncio
async def test_portfolio_row_conflict_with_overlapping_run_is_not_a_failure(
    db_session, env, monkeypatch, caplog
):
    import logging

    from sqlalchemy.exc import IntegrityError

    from app.tasks.scheduler import snapshot_eod_prices

    _hold(db_session, symbol="AAA")
    env["now"] = FRI_AFTER_CLOSE + timedelta(minutes=30)
    _quotes(monkeypatch)

    async def conflict(self, day):
        raise IntegrityError("INSERT INTO portfolio_snapshots", {}, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(PortfolioService, "compute_daily_snapshot", conflict)
    with caplog.at_level(logging.INFO, logger=scheduler_module.logger.name):
        await snapshot_eod_prices()  # must not raise
    assert "already written" in caplog.text
    assert "Daily portfolio snapshot failed" not in caplog.text
