"""No silent wrong money (spec 6.4 last bullet, 6.5; plan Rulings 1 and 8).

A holding that cannot be valued in the base currency is flagged
(`valueUnavailable` + `unavailableReason`), its base-currency fields are
null, and it is excluded from every total. History paths omit a point
instead of mixing an unconverted amount into a base-currency number.

FX is stubbed at `FxService.get_rate` from a fixed rate table (a missing
pair raises ValueError, like the real service when API/DB/reverse all
fail); quotes are stubbed at `MarketDataService.get_quote`.
"""
import datetime as dt
import logging
import uuid

import pytest

from app.models.asset import Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price import PriceSnapshot
from app.services.fx import FxService
from app.services.market_data import MarketDataService
from app.services.portfolio import PortfolioService
from tests.factories import make_asset

NOW = dt.datetime.now(dt.timezone.utc)


def make_ils_asset(db, symbol="TEVA.TA"):
    return make_asset(
        db, symbol=symbol, currency=Currency.ILS,
        exchange="TLV", market_region=MarketRegion.IL,
    )


def make_lot(db, asset, quantity, avg_cost, currency, purchase_date=None):
    lot = HoldingLot(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        quantity=quantity,
        avg_cost=avg_cost,
        cost_currency=currency,
        account_name="main",
        purchase_date=purchase_date or (NOW - dt.timedelta(days=30)),
    )
    db.add(lot)
    db.commit()
    return lot


def make_snapshot(db, asset, price, currency, when):
    db.add(PriceSnapshot(
        id=str(uuid.uuid4()), asset_id=asset.id, price=price,
        currency=currency, timestamp=when, source="eod_snapshot",
    ))
    db.commit()


@pytest.fixture()
def rates(monkeypatch):
    """Rate table keyed by (from_code, to_code). Missing pair -> ValueError."""
    table: dict = {}

    async def _get_rate(self, from_currency, to_currency):
        f, t = Currency(from_currency).value, Currency(to_currency).value
        if f == t:
            return 1.0
        if (f, t) in table:
            return table[(f, t)]
        raise ValueError(f"Could not get FX rate for {f}/{t}")

    monkeypatch.setattr(FxService, "get_rate", _get_rate)
    return table


@pytest.fixture()
def quotes(monkeypatch):
    """Quote table keyed by symbol. Missing symbol -> the quote call raises."""
    table: dict = {}

    async def _get_quote(self, symbol, asset_type=None):
        if symbol not in table:
            raise ValueError(f"No price data available for {symbol}")
        return dict(table[symbol])

    monkeypatch.setattr(MarketDataService, "get_quote", _get_quote)
    return table


def ils_quote(price=65.12):
    return {"price": price, "change": 0.5, "changePercent": 0.77,
            "currency": "ILS", "unitFactor": 0.01}


def usd_quote(price=100.0, change=2.0, change_percent=2.04):
    return {"price": price, "change": change, "changePercent": change_percent,
            "currency": "USD", "unitFactor": 1.0}


def assert_base_fields_null(h):
    assert h.avg_cost_base is None
    assert h.current_price is None
    assert h.price_change is None
    assert h.market_value is None
    assert h.unrealized_pnl is None
    assert h.unrealized_pnl_percent is None
    assert h.weight is None


# ---------------------------------------------------------------------------
# Per-lot valuation and flags
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ils_lot_valued_in_usd_base(db_session, rates, quotes):
    asset = make_ils_asset(db_session)
    make_lot(db_session, asset, 10, 60.0, Currency.ILS)
    quotes["TEVA.TA"] = ils_quote(65.12)
    rates[("ILS", "USD")] = 0.27

    [h] = await PortfolioService(db_session, base_currency=Currency.USD).get_holdings_with_prices()

    assert h.value_unavailable is False
    assert h.unavailable_reason is None
    assert h.market_value == pytest.approx(10 * 65.12 * 0.27)
    assert h.avg_cost_base == pytest.approx(60 * 0.27)
    assert h.current_price == pytest.approx(65.12 * 0.27)


@pytest.mark.asyncio
async def test_ils_lot_valued_in_ils_base(db_session, rates, quotes):
    asset = make_ils_asset(db_session)
    make_lot(db_session, asset, 10, 60.0, Currency.ILS)
    quotes["TEVA.TA"] = ils_quote(65.12)

    [h] = await PortfolioService(db_session, base_currency=Currency.ILS).get_holdings_with_prices()

    assert h.value_unavailable is False
    assert h.market_value == pytest.approx(651.2)
    assert h.avg_cost_base == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_fx_failure_flags_holding_in_ils_base(db_session, rates, quotes):
    """Review focus 3: a USD holding under an ILS base with no USD->ILS rate
    must be flagged, never summed as shekels."""
    asset = make_asset(db_session, symbol="AAPL")
    make_lot(db_session, asset, 10, 50.0, Currency.USD)
    quotes["AAPL"] = usd_quote()
    # no ("USD", "ILS") rate -> get_rate raises ValueError

    [h] = await PortfolioService(db_session, base_currency=Currency.ILS).get_holdings_with_prices()

    assert h.value_unavailable is True
    assert h.unavailable_reason == "fx_unavailable"
    assert h.market_value is None
    assert h.avg_cost_base is None
    assert_base_fields_null(h)
    # Native per-lot fields stay for display.
    assert h.avg_cost == 50.0
    assert h.quantity == 10


@pytest.mark.asyncio
async def test_ils_asset_with_usd_lot_is_lot_currency_check(db_session, rates, quotes):
    asset = make_ils_asset(db_session)
    make_lot(db_session, asset, 10, 6000.0, Currency.USD)  # old USD default
    quotes["TEVA.TA"] = ils_quote(65.12)
    rates[("ILS", "USD")] = 0.27

    [h] = await PortfolioService(db_session, base_currency=Currency.USD).get_holdings_with_prices()

    assert h.value_unavailable is True
    assert h.unavailable_reason == "lot_currency_check"
    assert_base_fields_null(h)


@pytest.mark.asyncio
async def test_lot_currency_check_wins_over_fx_failure(db_session, rates, quotes):
    """Rule order: lot_currency_check is checked before any FX conversion."""
    asset = make_ils_asset(db_session)
    make_lot(db_session, asset, 10, 6000.0, Currency.USD)
    quotes["TEVA.TA"] = ils_quote(65.12)
    # no rates at all: base ILS would fail USD->ILS cost conversion

    [h] = await PortfolioService(db_session, base_currency=Currency.ILS).get_holdings_with_prices()

    assert h.unavailable_reason == "lot_currency_check"


@pytest.mark.asyncio
async def test_quote_failure_flags_price_unavailable(db_session, rates, quotes):
    asset = make_asset(db_session, symbol="AAPL")
    make_lot(db_session, asset, 10, 50.0, Currency.USD)
    # no quote for AAPL -> get_quote raises

    [h] = await PortfolioService(db_session, base_currency=Currency.USD).get_holdings_with_prices()

    assert h.value_unavailable is True
    assert h.unavailable_reason == "price_unavailable"
    assert_base_fields_null(h)


@pytest.mark.asyncio
async def test_unknown_quote_currency_flags_fx_unavailable(db_session, rates, quotes):
    """A quote currency Navigator has no Currency member for (e.g. BRL)."""
    asset = make_asset(db_session, symbol="PETR4.SA")
    make_lot(db_session, asset, 10, 50.0, Currency.USD)
    quotes["PETR4.SA"] = {"price": 38.0, "change": 0.1, "changePercent": 0.3,
                          "currency": "BRL", "unitFactor": 1.0}

    [h] = await PortfolioService(db_session, base_currency=Currency.USD).get_holdings_with_prices()

    assert h.value_unavailable is True
    assert h.unavailable_reason == "fx_unavailable"
    assert_base_fields_null(h)


@pytest.mark.asyncio
async def test_price_fx_failure_flags_fx_unavailable(db_session, rates, quotes):
    """Cost converts (USD lot, USD base) but the EUR quote has no EUR->USD rate."""
    asset = make_asset(db_session, symbol="SAP.DE", currency=Currency.EUR)
    make_lot(db_session, asset, 10, 50.0, Currency.USD)
    quotes["SAP.DE"] = {"price": 200.0, "change": 1.0, "changePercent": 0.5,
                        "currency": "EUR", "unitFactor": 1.0}

    [h] = await PortfolioService(db_session, base_currency=Currency.USD).get_holdings_with_prices()

    assert h.value_unavailable is True
    assert h.unavailable_reason == "fx_unavailable"
    assert_base_fields_null(h)


@pytest.mark.asyncio
async def test_usd_holding_unchanged(db_session, rates, quotes):
    """Review focus 2: a US/USD holding in a USD base is valued exactly as
    before, and FX is never consulted (the empty rate table would raise)."""
    asset = make_asset(db_session, symbol="AAPL")
    make_lot(db_session, asset, 10, 50.0, Currency.USD)
    quotes["AAPL"] = usd_quote(price=100.0, change=2.0, change_percent=2.04)

    service = PortfolioService(db_session, base_currency=Currency.USD)
    [h] = await service.get_holdings_with_prices()

    assert h.value_unavailable is False
    assert h.unavailable_reason is None
    assert h.avg_cost == 50.0
    assert h.avg_cost_base == 50.0
    assert h.current_price == 100.0
    assert h.price_change == 2.0
    assert h.price_change_percent == 2.04
    assert h.market_value == 1000.0
    assert h.unrealized_pnl == 500.0
    assert h.unrealized_pnl_percent == 100.0
    assert h.weight is None

    [g] = await service.get_grouped_holdings()
    assert g.value_unavailable is False
    assert g.unavailable_reason is None
    assert g.total_quantity == 10
    assert g.avg_cost == 50.0
    assert g.current_price == 100.0
    assert g.price_change == 2.0
    assert g.price_change_percent == 2.04
    assert g.market_value == 1000.0
    assert g.unrealized_pnl == 500.0
    assert g.unrealized_pnl_percent == 100.0

    summary = await service.get_summary()
    assert summary.total_nav == 1000.0
    assert summary.total_cost == 500.0
    assert summary.total_unrealized_pnl == 500.0
    assert summary.total_unrealized_pnl_percent == 100.0


# ---------------------------------------------------------------------------
# Grouped holdings and summary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grouped_holding_flagged_when_any_lot_flagged(db_session, rates, quotes):
    ils = make_ils_asset(db_session)
    make_lot(db_session, ils, 10, 60.0, Currency.ILS)
    make_lot(db_session, ils, 5, 6000.0, Currency.USD)  # lot_currency_check
    quotes["TEVA.TA"] = ils_quote(65.12)
    rates[("ILS", "USD")] = 0.27

    [g] = await PortfolioService(db_session, base_currency=Currency.USD).get_grouped_holdings()

    assert g.value_unavailable is True
    assert g.unavailable_reason == "lot_currency_check"
    assert g.total_quantity == 15
    assert g.avg_cost is None
    assert g.current_price is None
    assert g.price_change is None
    assert g.market_value is None
    assert g.unrealized_pnl is None
    assert g.unrealized_pnl_percent is None
    assert g.weight is None
    flags = sorted((lot.value_unavailable, lot.unavailable_reason) for lot in g.lots)
    assert flags == [(False, None), (True, "lot_currency_check")]


@pytest.mark.asyncio
async def test_grouped_holdings_do_not_raise_on_fx_failure(db_session, rates, quotes):
    """get_grouped_holdings used to re-convert cost with no except (raised)."""
    asset = make_asset(db_session, symbol="AAPL")
    make_lot(db_session, asset, 10, 50.0, Currency.USD)
    quotes["AAPL"] = usd_quote()
    # no ("USD", "ILS") rate

    [g] = await PortfolioService(db_session, base_currency=Currency.ILS).get_grouped_holdings()

    assert g.value_unavailable is True
    assert g.unavailable_reason == "fx_unavailable"
    assert g.avg_cost is None
    assert g.market_value is None


@pytest.mark.asyncio
async def test_grouped_avg_cost_reuses_lot_avg_cost_base(db_session, rates, quotes):
    ils = make_ils_asset(db_session)
    make_lot(db_session, ils, 10, 60.0, Currency.ILS)
    make_lot(db_session, ils, 30, 64.0, Currency.ILS)
    quotes["TEVA.TA"] = ils_quote(65.12)
    rates[("ILS", "USD")] = 0.27

    [g] = await PortfolioService(db_session, base_currency=Currency.USD).get_grouped_holdings()

    assert g.value_unavailable is False
    assert g.avg_cost == pytest.approx((10 * 60.0 + 30 * 64.0) / 40 * 0.27)
    assert g.market_value == pytest.approx(40 * 65.12 * 0.27)


@pytest.mark.asyncio
async def test_summary_excludes_flagged(db_session, rates, quotes):
    usd = make_asset(db_session, symbol="AAPL")
    make_lot(db_session, usd, 10, 50.0, Currency.USD)
    quotes["AAPL"] = usd_quote(price=100.0)

    ils = make_ils_asset(db_session)
    make_lot(db_session, ils, 10, 60.0, Currency.ILS)  # no ILS->USD rate
    quotes["TEVA.TA"] = ils_quote(65.12)

    bad = make_ils_asset(db_session, symbol="LUMI.TA")
    make_lot(db_session, bad, 10, 6000.0, Currency.USD)  # lot_currency_check
    quotes["LUMI.TA"] = ils_quote(30.0)

    missing = make_asset(db_session, symbol="MSFT")
    make_lot(db_session, missing, 3, 300.0, Currency.USD)  # quote fails

    summary = await PortfolioService(db_session, base_currency=Currency.USD).get_summary()

    assert summary.total_nav == pytest.approx(1000.0)
    assert summary.total_cost == pytest.approx(500.0)
    assert summary.total_unrealized_pnl == pytest.approx(500.0)
    assert summary.total_unrealized_pnl_percent == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_summary_daily_pnl_ignores_flagged_lot_history(db_session, rates, quotes):
    """A flagged lot is out of the current NAV, so it must not be in the
    historical NAV either, or its whole value shows up as a fake daily loss."""
    usd = make_asset(db_session, symbol="AAPL")
    make_lot(db_session, usd, 10, 50.0, Currency.USD,
             purchase_date=NOW - dt.timedelta(days=40))
    quotes["AAPL"] = usd_quote(price=100.0)
    make_snapshot(db_session, usd, 100.0, Currency.USD, NOW - dt.timedelta(days=1, hours=1))

    bad = make_ils_asset(db_session, symbol="LUMI.TA")
    make_lot(db_session, bad, 10, 6000.0, Currency.USD,
             purchase_date=NOW - dt.timedelta(days=40))  # lot_currency_check
    quotes["LUMI.TA"] = ils_quote(30.0)
    rates[("ILS", "USD")] = 0.27
    make_snapshot(db_session, bad, 30.0, Currency.ILS, NOW - dt.timedelta(days=1, hours=1))

    summary = await PortfolioService(db_session, base_currency=Currency.USD).get_summary()

    assert summary.total_nav == pytest.approx(1000.0)
    assert summary.daily_pnl == pytest.approx(0.0)
    assert summary.daily_pnl_percent == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# History (Ruling 8)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nav_history_omits_point_without_fx(db_session, rates, quotes):
    ils = make_ils_asset(db_session)
    make_lot(db_session, ils, 10, 60.0, Currency.ILS,
             purchase_date=NOW - dt.timedelta(days=20))
    make_snapshot(db_session, ils, 65.0, Currency.ILS, NOW - dt.timedelta(days=5))
    # no ILS->USD rate

    result = await PortfolioService(db_session, base_currency=Currency.USD).get_nav_history("1m")

    assert result["points"] == []


@pytest.mark.asyncio
async def test_asset_usd_price_at_none_without_fx(db_session, rates, quotes):
    ils = make_ils_asset(db_session)
    make_snapshot(db_session, ils, 65.0, Currency.ILS, NOW - dt.timedelta(days=1))

    service = PortfolioService(db_session, base_currency=Currency.USD)
    assert await service._asset_usd_price_at(ils.id, NOW) is None


@pytest.mark.asyncio
async def test_daily_snapshot_skipped_when_rate_missing(db_session, rates, quotes, caplog):
    day = (NOW - dt.timedelta(days=2)).date()
    eod = dt.datetime.combine(day, dt.time(23, 0), tzinfo=dt.timezone.utc)

    usd = make_asset(db_session, symbol="AAPL")
    make_lot(db_session, usd, 10, 50.0, Currency.USD, purchase_date=eod - dt.timedelta(days=10))
    make_snapshot(db_session, usd, 100.0, Currency.USD, eod)

    ils = make_ils_asset(db_session)
    make_lot(db_session, ils, 10, 60.0, Currency.ILS, purchase_date=eod - dt.timedelta(days=10))
    make_snapshot(db_session, ils, 65.0, Currency.ILS, eod)
    # no ILS->USD rate

    with caplog.at_level(logging.WARNING, logger="app.services.portfolio"):
        row = await PortfolioService(db_session, base_currency=Currency.USD).compute_daily_snapshot(day)

    assert row is None
    assert db_session.query(PortfolioSnapshot).count() == 0
    assert any(
        "skipping portfolio snapshot for" in r.getMessage() and "ILS" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_daily_pnl_series_empty_when_display_rate_missing(db_session, rates, quotes, caplog):
    db_session.add(PortfolioSnapshot(
        id=str(uuid.uuid4()), date=(NOW - dt.timedelta(days=1)).date(),
        nav=1000.0, net_flow=0.0, daily_pnl=10.0, daily_pnl_pct=1.0,
        base_currency="USD",
    ))
    db_session.commit()
    # no USD->ILS rate

    with caplog.at_level(logging.WARNING, logger="app.services.portfolio"):
        result = await PortfolioService(db_session, base_currency=Currency.ILS).get_daily_pnl_series("1m")

    assert result["points"] == []
    assert result["base_currency"] == "ILS"
    assert any(r.levelno == logging.WARNING for r in caplog.records)
