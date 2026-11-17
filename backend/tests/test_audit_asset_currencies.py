"""Audit script: dry run reports, --apply fixes assets and converts pre-cutover prices."""
import io
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.models.alert import PriceAlert
from app.models.asset import Asset, Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.maintenance import ALERTS_CONVERTED_MARKER, CUTOVER_MARKER, get_marker, set_marker
from app.models.watchlist import Watchlist, WatchlistItem
from app.providers.yahoo import YahooFinanceProvider
from scripts.audit_asset_currencies import audit
from tests.factories import make_alert, make_asset

CUTOVER = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
PRE = CUTOVER - timedelta(days=3)
POST = CUTOVER + timedelta(days=1)

META = {
    "TEVA.TA": {"currency": "ILS", "unitFactor": 0.01, "exchangeName": "TLV",
                "fullExchangeName": "Tel Aviv", "instrumentType": "EQUITY"},
    "AAPL": {"currency": "USD", "unitFactor": 1.0, "exchangeName": "NMS",
             "fullExchangeName": "NasdaqGS", "instrumentType": "EQUITY"},
}


@pytest.fixture(autouse=True)
def yahoo(monkeypatch):
    meta = dict(META)

    async def fake(self, symbol):
        m = meta.get(symbol)
        if isinstance(m, Exception):
            raise m
        return m

    monkeypatch.setattr(YahooFinanceProvider, "get_chart_meta", fake)
    return meta


def _teva(db):
    """A TEVA.TA asset mislabelled USD/US with the legacy data around it."""
    asset = make_asset(db, symbol="TEVA.TA", currency=Currency.USD, exchange=None,
                       market_region=MarketRegion.US)
    pre = make_alert(db, asset, threshold=2600.0)
    pre.trail_amount, pre.high_water_mark, pre.triggered_price = 150.0, 2700.0, 2550.0
    pre.created_at = PRE
    post = make_alert(db, asset, threshold=26.0)
    post.created_at = POST
    wl = Watchlist(id=str(uuid.uuid4()), name="w")
    db.add(wl)
    db.flush()
    item = WatchlistItem(id=str(uuid.uuid4()), watchlist_id=wl.id, asset_id=asset.id,
                         target_price=2400.0, created_at=PRE)
    db.add(item)
    lot = HoldingLot(id=str(uuid.uuid4()), asset_id=asset.id, quantity=137.0, avg_cost=61.23,
                     cost_currency=Currency.USD, account_name="acc", purchase_date=PRE)
    db.add(lot)
    db.commit()
    return asset.id, pre.id, post.id, item.id, lot.id


def _setup(db):
    set_marker(db, CUTOVER_MARKER, at=CUTOVER)
    return _teva(db)


def _snapshot(db):
    db.expire_all()
    a = db.query(Asset).one()
    return (a.currency, a.market_region, a.exchange,
            sorted((x.threshold, x.trail_amount or 0, x.high_water_mark or 0,
                    x.triggered_price or 0) for x in db.query(PriceAlert)),
            [i.target_price for i in db.query(WatchlistItem)],
            get_marker(db, ALERTS_CONVERTED_MARKER))


@pytest.mark.asyncio
async def test_dry_run_reports_and_changes_nothing(db_session):
    _setup(db_session)
    before = _snapshot(db_session)
    out = io.StringIO()
    report = await audit(db_session, apply=False, out=out)
    assert _snapshot(db_session) == before
    text = out.getvalue()
    assert "TEVA.TA: currency USD->ILS region US->IL exchange ->Tel Aviv" in text
    assert "TEVA.TA: 1 lot(s) in USD, listing is ILS - fix in the lot edit dialog" in text
    # the post-cutover alert is not counted
    assert "TEVA.TA: 1 alert(s), 1 watch target(s) priced in minor units" in text
    assert report.asset_fixes == ["TEVA.TA"]
    assert report.lot_mismatches == ["TEVA.TA"]
    assert report.minor_unit_symbols == ["TEVA.TA"]
    assert report.converted_alerts == 0 and report.converted_watch_targets == 0


@pytest.mark.asyncio
async def test_output_contains_no_quantities_or_costs(db_session):
    _setup(db_session)
    for apply in (False, True):
        out = io.StringIO()
        await audit(db_session, apply=apply, out=out)
        text = out.getvalue()
        for secret in ("137", "61.23", "2600", "2400", "26.0", "2700", "2550", "150"):
            assert secret not in text


@pytest.mark.asyncio
async def test_apply_fixes_asset_and_converts_pre_cutover_fields(db_session):
    asset_id, pre_id, post_id, item_id, _ = _setup(db_session)
    report = await audit(db_session, apply=True, out=io.StringIO())
    db_session.expire_all()
    a = db_session.get(Asset, asset_id)
    assert (a.currency, a.market_region, a.exchange) == (
        Currency.ILS, MarketRegion.IL, "Tel Aviv")
    pre = db_session.get(PriceAlert, pre_id)
    assert pre.threshold == pytest.approx(26.0)
    assert pre.trail_amount == pytest.approx(1.5)
    assert pre.high_water_mark == pytest.approx(27.0)
    assert pre.triggered_price == pytest.approx(25.5)
    post = db_session.get(PriceAlert, post_id)
    assert post.threshold == 26.0 and post.trail_amount is None
    assert db_session.get(WatchlistItem, item_id).target_price == pytest.approx(24.0)
    assert get_marker(db_session, ALERTS_CONVERTED_MARKER) is not None
    assert report.converted_alerts == 1 and report.converted_watch_targets == 1


@pytest.mark.asyncio
async def test_apply_twice_is_noop_for_prices(db_session):
    _setup(db_session)
    await audit(db_session, apply=True, out=io.StringIO())
    first = _snapshot(db_session)
    report = await audit(db_session, apply=True, out=io.StringIO())
    assert _snapshot(db_session) == first
    assert report.converted_alerts == 0 and report.converted_watch_targets == 0


@pytest.mark.asyncio
async def test_apply_never_changes_lots(db_session):
    *_, lot_id = _setup(db_session)
    await audit(db_session, apply=True, out=io.StringIO())
    db_session.expire_all()
    lot = db_session.get(HoldingLot, lot_id)
    assert (lot.quantity, lot.avg_cost, lot.cost_currency) == (137.0, 61.23, Currency.USD)


@pytest.mark.asyncio
async def test_null_created_at_target_converted(db_session):
    _, _, _, item_id, _ = _setup(db_session)
    db_session.get(WatchlistItem, item_id).created_at = None
    db_session.commit()
    await audit(db_session, apply=True, out=io.StringIO())
    db_session.expire_all()
    assert db_session.get(WatchlistItem, item_id).target_price == pytest.approx(24.0)


@pytest.mark.asyncio
async def test_matching_asset_is_silent(db_session):
    set_marker(db_session, CUTOVER_MARKER, at=CUTOVER)
    make_asset(db_session, symbol="AAPL", exchange="NasdaqGS")
    out = io.StringIO()
    report = await audit(db_session, apply=True, out=out)
    assert "AAPL:" not in out.getvalue()
    assert report.asset_fixes == [] and report.minor_unit_symbols == []


@pytest.mark.asyncio
async def test_yahoo_failure_is_skipped_and_blocks_apply(db_session, yahoo):
    set_marker(db_session, CUTOVER_MARKER, at=CUTOVER)
    make_asset(db_session, symbol="DOWN")
    yahoo["DOWN"] = httpx.ConnectError("down")
    out = io.StringIO()
    report = await audit(db_session, apply=True, out=out)
    assert "DOWN: skipped (" in out.getvalue()
    assert report.skipped == ["DOWN"] and report.aborted
    assert get_marker(db_session, ALERTS_CONVERTED_MARKER) is None


def _legacy_alert(db, symbol):
    asset = make_asset(db, symbol=symbol, currency=Currency.USD)
    alert = make_alert(db, asset, threshold=2600.0)
    alert.created_at = PRE
    db.commit()
    return alert.id


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, {"currency": "BRL", "exchangeName": "SAO"}])
async def test_skipped_asset_with_legacy_alert_blocks_apply(db_session, yahoo, bad):
    asset_id, pre_id, _, item_id, _ = _setup(db_session)
    yahoo["OLD.TA"] = bad
    other_id = _legacy_alert(db_session, "OLD.TA")
    out = io.StringIO()
    report = await audit(db_session, apply=True, out=out)
    text = out.getvalue()
    assert "OLD.TA: 1 alert(s), 0 watch target(s) not converted (listing unknown)" in text
    assert "delete or edit them, then re-run" in text
    assert report.aborted and report.converted_alerts == 0
    db_session.expire_all()
    assert get_marker(db_session, ALERTS_CONVERTED_MARKER) is None
    assert db_session.get(PriceAlert, other_id).threshold == 2600.0
    assert db_session.get(PriceAlert, pre_id).threshold == 2600.0
    assert db_session.get(Asset, asset_id).currency == Currency.USD  # one transaction: nothing applied


@pytest.mark.asyncio
@pytest.mark.parametrize("bad,reason", [
    (None, "symbol not found"),
    ({"currency": "BRL", "exchangeName": "SAO"}, "unsupported currency BRL"),
])
async def test_skip_without_legacy_data_still_applies_and_completes(db_session, yahoo, bad, reason):
    """A skipped listing with nothing to convert doesn't block or fail the
    apply; the output still names it."""
    _setup(db_session)
    yahoo["GONE"] = bad
    make_asset(db_session, symbol="GONE")
    out = io.StringIO()
    report = await audit(db_session, apply=True, out=out)
    text = out.getvalue()
    assert f"GONE: skipped ({reason})" in text
    assert "skipped (not verified, not changed): GONE" in text
    assert not report.aborted and not report.incomplete and report.skipped == ["GONE"]
    assert get_marker(db_session, ALERTS_CONVERTED_MARKER) is not None


@pytest.mark.asyncio
async def test_no_cutover_marker_notes_and_is_incomplete(db_session):
    _teva(db_session)
    out = io.StringIO()
    report = await audit(db_session, apply=True, out=out)
    assert "no cutover marker found: price conversion is skipped" in out.getvalue()
    assert report.incomplete and report.converted_alerts == 0
    assert get_marker(db_session, ALERTS_CONVERTED_MARKER) is None


def test_main_exit_codes(monkeypatch):
    import scripts.audit_asset_currencies as m
    from app.database import SessionLocal  # noqa: F401

    for report, code in [(m.AuditReport(), 0), (m.AuditReport(skipped=["GONE"]), 0),
                         (m.AuditReport(incomplete=True), 1),
                         (m.AuditReport(aborted=True), 1)]:
        async def fake(db, *, apply, out, r=report):
            return r
        monkeypatch.setattr(m, "audit", fake)
        assert m.main(["--apply"]) == code
