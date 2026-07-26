"""Unit tests for the S5FI/S5TH breadth computation (app/services/breadth.py).

Uses a small synthetic price DataFrame — no network calls, no yfinance,
no Wikipedia fetch. See compute_and_store_breadth() for the (network-heavy)
pipeline that feeds this function in production; that pipeline is exercised
only by the background scheduler, not by tests.
"""
import numpy as np
import pandas as pd
import pytest

from app.services.breadth import compute_breadth_from_closes


def _build_synthetic_closes(n=205):
    """
    Four synthetic symbols over `n` trading days:
    - SYM_A: flat at 100, then jumps to 200 for the last two days -> above
      its 50-day and 200-day SMA on both the latest and previous day.
    - SYM_B: flat at 100 the whole time -> exactly AT its SMA, never
      strictly above (tests that equality doesn't count as "above").
    - SYM_C: flat at 100, spikes to 300 two days ago, then crashes to 10 on
      the latest day -> above its SMA on the previous day, below on the
      latest day (tests that latest/previous are computed independently).
    - SYM_D: only has 30 days of real history (rest NaN) -> insufficient
      for either the 50-day or 200-day window, so it must be excluded from
      both indicators' denominators entirely (not counted as "below").
    """
    idx = pd.date_range("2025-01-01", periods=n, freq="D")

    a = np.full(n, 100.0)
    a[-2:] = 200.0

    b = np.full(n, 100.0)

    c = np.full(n, 100.0)
    c[-2] = 300.0
    c[-1] = 10.0

    d = np.full(n, np.nan)
    d[-30:] = 100.0

    return pd.DataFrame({"SYM_A": a, "SYM_B": b, "SYM_C": c, "SYM_D": d}, index=idx)


def test_compute_breadth_percentages_exclude_insufficient_history():
    closes = _build_synthetic_closes()
    result = compute_breadth_from_closes(closes)

    assert set(result.keys()) == {"s5fi", "s5th"}

    # Denominator is 3 (SYM_A, SYM_B, SYM_C) — SYM_D is excluded, not "below".
    # Latest day: only SYM_A is above -> 1/3.
    assert result["s5fi"]["value"] == pytest.approx(33.33, abs=0.01)
    assert result["s5th"]["value"] == pytest.approx(33.33, abs=0.01)

    # Previous day: SYM_A and SYM_C (pre-crash) are above -> 2/3.
    assert result["s5fi"]["previous"] == pytest.approx(66.67, abs=0.01)
    assert result["s5th"]["previous"] == pytest.approx(66.67, abs=0.01)


def test_compute_breadth_equal_to_sma_does_not_count_as_above():
    """A symbol sitting exactly on its moving average (SYM_B) must never be
    counted as "above" — this is implicitly covered above, but assert it
    directly with a single-symbol, all-constant series."""
    n = 205
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    flat = pd.DataFrame({"SYM_B": np.full(n, 100.0)}, index=idx)

    result = compute_breadth_from_closes(flat)

    assert result["s5fi"]["value"] == 0.0
    assert result["s5th"]["value"] == 0.0


def test_compute_breadth_raises_on_insufficient_rows():
    closes = pd.DataFrame({"SYM_A": [100.0]})
    with pytest.raises(ValueError):
        compute_breadth_from_closes(closes)


def test_compute_breadth_raises_on_empty_dataframe():
    with pytest.raises(ValueError):
        compute_breadth_from_closes(pd.DataFrame())
