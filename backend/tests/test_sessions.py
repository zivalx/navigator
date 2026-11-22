from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.asset import AssetType, MarketRegion
from app.services.sessions import (
    classify,
    is_alertable,
    is_in_session,
    latest_session_open,
    session_bounds,
    trade_date_for,
)

NY = ZoneInfo("America/New_York")
IL = ZoneInfo("Asia/Jerusalem")


def local(y, mo, d, h, mi, tz):
    return datetime(y, mo, d, h, mi, tzinfo=tz)


def asset(symbol="AAPL", atype=AssetType.STOCK, region=MarketRegion.US):
    return SimpleNamespace(symbol=symbol, asset_type=atype, market_region=region)


us_stock = asset()
tase_stock = asset("TEVA.TA", region=MarketRegion.IL)
btc = asset("BTC", AssetType.CRYPTO, MarketRegion.US)
fund = asset("VFIAX", AssetType.FUND)
NOW = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)  # Fri 10:00 ET


def yq(mt, **kw):
    q = {"price": 1.0, "source": "yahoo", "marketTime": mt.astimezone(timezone.utc).isoformat()}
    q.update(kw)
    return q


GOOD = yq(NOW)


@pytest.mark.parametrize(
    "quote",
    [
        {**GOOD, "stale": True},
        {**GOOD, "source": "db_cache"},
        {**GOOD, "source": "polygon"},
        {**GOOD, "source": "finnhub"},
        {"price": 1.0, "source": "yahoo"},
        {"error": "boom"},
    ],
)
def test_rejected_sources(quote):
    assert is_alertable(quote, us_stock, NOW)[0] is False


def test_us_monday_before_open_with_friday_close_ok():
    now = local(2026, 11, 23, 9, 0, NY)
    q = yq(local(2026, 11, 20, 16, 0, NY))
    assert is_alertable(q, us_stock, now) == (True, "ok")


def test_us_monday_after_open_with_friday_close_rejected():
    now = local(2026, 11, 23, 10, 0, NY)
    q = yq(local(2026, 11, 20, 16, 0, NY))
    assert is_alertable(q, us_stock, now) == (False, "old_market_time")


def test_us_after_friday_holiday_rejected():
    now = local(2026, 11, 20, 11, 0, NY)
    q = yq(local(2026, 11, 19, 16, 0, NY))
    assert is_alertable(q, us_stock, now) == (False, "old_market_time")


def test_tase_friday_with_thursday_market_time_rejected():
    now = local(2026, 11, 20, 11, 0, IL)
    q = yq(local(2026, 11, 19, 17, 20, IL))
    assert is_alertable(q, tase_stock, now) == (False, "old_market_time")


def test_tase_sunday_with_friday_close_ok():
    now = local(2026, 11, 22, 11, 0, IL)
    q = yq(local(2026, 11, 20, 13, 59, IL))
    assert is_alertable(q, tase_stock, now) == (True, "ok")


def test_crypto_coingecko_ok():
    q = {"price": 1.0, "source": "coingecko"}
    assert is_alertable(q, btc, NOW) == (True, "ok")


def test_crypto_yahoo_needs_market_time():
    assert is_alertable({"price": 1.0, "source": "yahoo"}, btc, NOW) == (False, "no_market_time")
    assert is_alertable(GOOD, btc, NOW) == (True, "ok")


def test_fund_not_monitored():
    assert is_alertable(GOOD, fund, NOW) == (False, "not_monitored")


def test_other_exchange_uses_96h_window():
    other = asset("XYZ", region=MarketRegion.EU)
    assert is_alertable(yq(local(2026, 11, 17, 16, 0, timezone.utc)), other, NOW)[0] is True
    assert is_alertable(yq(local(2026, 11, 10, 16, 0, timezone.utc)), other, NOW) == (
        False,
        "old_market_time",
    )


def test_us_open_tracks_dst():
    assert is_in_session("US", datetime(2026, 3, 6, 14, 0, tzinfo=timezone.utc)) is False
    assert is_in_session("US", datetime(2026, 3, 9, 13, 45, tzinfo=timezone.utc)) is True


def test_tase_friday_close():
    assert is_in_session("TASE", datetime(2026, 11, 20, 11, 59, tzinfo=timezone.utc)) is True
    assert is_in_session("TASE", datetime(2026, 11, 20, 12, 1, tzinfo=timezone.utc)) is False


def test_tase_sunday_closed():
    assert is_in_session("TASE", datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)) is False
    assert session_bounds("TASE", date(2026, 11, 22)) is None


def test_crypto_and_other_always_in_session():
    assert is_in_session("crypto", NOW) and is_in_session("other", NOW)
    assert latest_session_open("crypto", NOW) is None


def test_latest_session_open_walks_back_over_weekend():
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)  # Sunday
    assert latest_session_open("US", now) == local(2026, 11, 20, 9, 30, NY).astimezone(timezone.utc)


def test_classify_crypto_wins_over_region():
    assert classify(btc) == "crypto"
    assert classify(asset("TEVA.TA", region=MarketRegion.US)) == "TASE"
    assert classify(asset("X", region=MarketRegion.EU)) == "other"


def test_trade_date_uses_exchange_timezone():
    q = {"marketTime": "2026-11-19T22:30:00+00:00", "exchangeTimezone": "Asia/Jerusalem"}
    assert trade_date_for(q, tase_stock, NOW) == date(2026, 11, 20)


def test_trade_date_falls_back_to_table_tz_and_crypto_uses_now():
    q = {"marketTime": "2026-11-19T22:30:00+00:00"}
    assert trade_date_for(q, tase_stock, NOW) == date(2026, 11, 20)
    assert trade_date_for(q, btc, NOW) == date(2026, 11, 20)
