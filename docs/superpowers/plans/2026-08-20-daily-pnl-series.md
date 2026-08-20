# Daily P&L + EOD NAV Series Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist an accurate, flow-adjusted daily P&L series and the actual EOD NAV per day, backfilled from history, and chart it as green/red daily bars on the dashboard.

**Architecture:** A new `portfolio_snapshots` table stores one row per day (NAV, net flow, daily P&L, %) in canonical USD. A service method computes/upserts a day's row from the existing at-date NAV valuation; a backfill builds the series from existing per-asset price snapshots. The EOD scheduler job writes today's row; a startup job backfills once. A new cached endpoint serves the series converted to the display currency, and the dashboard Performance card gains a NAV/Daily-P&L toggle with a Recharts bar chart.

**Tech Stack:** FastAPI, SQLAlchemy (sync), APScheduler, pytest/pytest-asyncio, React + TypeScript, @tanstack/react-query, Recharts.

**Spec:** `docs/superpowers/specs/2026-08-20-daily-pnl-series-design.md`

## Global Constraints

- Canonical storage currency is `"USD"`; convert USD→display currency only at read time (current FX via `FxService.convert`), matching the existing `/history` endpoint.
- All money is stored/computed as float (existing convention).
- Backend layering: routers stay thin and call `PortfolioService`; no external API calls outside providers.
- Upserts are keyed on `date` (UTC calendar date) and must be idempotent.
- No AI-attribution trailers in commits (repo convention).
- Daily P&L is flow-adjusted for BUYS only (new lots by `purchase_date`); sells are not tracked — documented limitation, not implemented here.
- Tests use the SQLite in-memory `db_session` fixture from `backend/tests/conftest.py`; model factories live in `backend/tests/factories.py`.

---

### Task 1: `PortfolioSnapshot` model + startup table creation

**Files:**
- Create: `backend/app/models/portfolio_snapshot.py`
- Modify: `backend/app/models/__init__.py` (register model import)
- Modify: `backend/tests/conftest.py:26` (add `portfolio_snapshot` to the model imports so `create_all` builds the table in tests)
- Test: `backend/tests/test_daily_pnl.py`

**Interfaces:**
- Produces: `PortfolioSnapshot` SQLAlchemy model with columns `id: str`, `date: datetime.date` (unique), `nav: float`, `net_flow: float`, `daily_pnl: float`, `daily_pnl_pct: float`, `base_currency: str`, `created_at: datetime`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_daily_pnl.py
import uuid
import datetime as dt

from app.models.portfolio_snapshot import PortfolioSnapshot


def test_portfolio_snapshot_row_roundtrips(db_session):
    row = PortfolioSnapshot(
        id=str(uuid.uuid4()),
        date=dt.date(2026, 8, 20),
        nav=1000.0,
        net_flow=0.0,
        daily_pnl=25.0,
        daily_pnl_pct=2.5,
        base_currency="USD",
    )
    db_session.add(row)
    db_session.commit()

    stored = db_session.query(PortfolioSnapshot).filter_by(date=dt.date(2026, 8, 20)).one()
    assert stored.nav == 1000.0
    assert stored.daily_pnl == 25.0
    assert stored.base_currency == "USD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_daily_pnl.py::test_portfolio_snapshot_row_roundtrips -v` (from `backend/`)
Expected: FAIL — `ModuleNotFoundError: app.models.portfolio_snapshot`.

- [ ] **Step 3: Write the model**

```python
# backend/app/models/portfolio_snapshot.py
from sqlalchemy import Column, String, Float, Date, DateTime, Index
from sqlalchemy.sql import func
from ..database import Base


class PortfolioSnapshot(Base):
    """One row per day: the portfolio's EOD NAV and flow-adjusted daily P&L,
    stored canonically in USD (see the daily-pnl-series design spec)."""
    __tablename__ = "portfolio_snapshots"

    id = Column(String, primary_key=True)
    date = Column(Date, nullable=False, unique=True, index=True)
    nav = Column(Float, nullable=False)
    net_flow = Column(Float, nullable=False, default=0.0)
    daily_pnl = Column(Float, nullable=False, default=0.0)
    daily_pnl_pct = Column(Float, nullable=False, default=0.0)
    base_currency = Column(String, nullable=False, default="USD")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_portfolio_snapshots_date", "date"),
    )
```

Then register it for metadata + tests:

```python
# backend/app/models/__init__.py  — add alongside existing model imports
from .portfolio_snapshot import PortfolioSnapshot  # noqa: F401
```

```python
# backend/tests/conftest.py:26 — extend the import so create_all builds the table
from app.models import asset, holding, price, watchlist, news, alert, portfolio_snapshot  # noqa: E402,F401
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_daily_pnl.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/portfolio_snapshot.py backend/app/models/__init__.py backend/tests/conftest.py backend/tests/test_daily_pnl.py
git commit -m "feat(backend): portfolio_snapshots model for the daily P&L series"
```

---

### Task 2: Production migration for the new table

**Files:**
- Modify: `backend/app/database.py` (add a `CREATE TABLE IF NOT EXISTS` step for Postgres, mirroring `_migrate_price_alerts`)

**Interfaces:**
- Consumes: `PortfolioSnapshot` model (Task 1).
- Produces: `_migrate_portfolio_snapshots()` called from `init_db()`.

Note: `Base.metadata.create_all(bind=engine)` already creates the table on a fresh DB (local + tests). This task only covers the Postgres path where the DB already exists; on SQLite/tests it is a no-op (guarded by dialect, same as `_migrate_price_alerts`). Because `create_all` runs first in `init_db()` and creates any missing table, an explicit DDL is defensive/parity-only — keep it minimal.

- [ ] **Step 1: Add the guarded creation call**

```python
# backend/app/database.py — inside init_db(), after _migrate_price_alerts()
def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)
    _migrate_price_alerts()
    # create_all above already builds portfolio_snapshots on any DB; no extra
    # ALTER is needed for a brand-new table (unlike price_alerts, which gained
    # columns). No-op placeholder kept out to avoid dead code.
```

(If a future column is added to `portfolio_snapshots`, add a `_migrate_portfolio_snapshots()` following the `_migrate_price_alerts` probe pattern. For this plan, `create_all` suffices — **no code change required in this task beyond confirming the model is imported so `create_all` sees it**, which Task 1 already did via `models/__init__.py`.)

- [ ] **Step 2: Verify create_all builds it (manual)**

Run (from `backend/`, with Docker Postgres up): `docker compose restart api && sleep 6 && docker exec backend-db-1 psql -U postgres -d navigator -c "\d portfolio_snapshots"`
Expected: table exists with the 8 columns.

- [ ] **Step 3: Commit (only if a change was made)**

This task is a verification checkpoint; if no code changed, skip the commit. Otherwise:

```bash
git add backend/app/database.py
git commit -m "chore(backend): ensure portfolio_snapshots table is created on startup"
```

---

### Task 3: `compute_daily_snapshot(day)` service method (flow-adjusted)

**Files:**
- Modify: `backend/app/services/portfolio.py` (add method to `PortfolioService`)
- Test: `backend/tests/test_daily_pnl.py`

**Interfaces:**
- Consumes: `PortfolioService.__init__(db, base_currency)`, existing `self._get_nav_at_date(target_date, holdings=None) -> Optional[float]`, `self.fx_service.convert(amount, from_ccy, to_ccy)`, models `HoldingLot`, `PortfolioSnapshot`.
- Produces: `async def compute_daily_snapshot(self, day: datetime.date) -> PortfolioSnapshot | None` — computes NAV in USD at end of `day`, net USD flow from lots purchased on `day`, diffs against the most recent stored `portfolio_snapshots` row before `day`, and upserts the row for `day`. Returns the row, or `None` if NAV can't be valued (no price data).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_daily_pnl.py — add these (imports at top of file)
import datetime as dt
import pytest
from app.models.asset import Asset, AssetType, Currency, MarketRegion
from app.models.holding import HoldingLot
from app.models.price import PriceSnapshot
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.services.portfolio import PortfolioService


def _asset(db, symbol="AAA"):
    a = Asset(id=str(uuid.uuid4()), symbol=symbol, name=f"{symbol} Inc.",
              exchange="X", currency=Currency.USD, asset_type=AssetType.STOCK,
              market_region=MarketRegion.US)
    db.add(a); db.commit(); return a


def _lot(db, asset, qty, purchase_date, avg_cost=50.0):
    lot = HoldingLot(id=str(uuid.uuid4()), asset_id=asset.id, quantity=qty,
                     avg_cost=avg_cost, cost_currency=Currency.USD,
                     account_name="m", purchase_date=purchase_date)
    db.add(lot); db.commit(); return lot


def _snap(db, asset, price, day):
    db.add(PriceSnapshot(id=str(uuid.uuid4()), asset_id=asset.id, price=price,
                         currency=Currency.USD,
                         timestamp=dt.datetime.combine(day, dt.time(23, 0), tzinfo=dt.timezone.utc),
                         source="eod_snapshot"))
    db.commit()


@pytest.mark.asyncio
async def test_daily_snapshot_pure_market_move(db_session):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 18))
    _snap(db_session, a, price=110.0, day=dt.date(2026, 8, 19))
    svc = PortfolioService(db_session, base_currency=Currency.USD)

    day18 = await svc.compute_daily_snapshot(dt.date(2026, 8, 18))
    day19 = await svc.compute_daily_snapshot(dt.date(2026, 8, 19))

    assert day18.nav == pytest.approx(1000.0)
    assert day18.daily_pnl == pytest.approx(0.0)      # first day: no baseline
    assert day19.nav == pytest.approx(1100.0)
    assert day19.net_flow == pytest.approx(0.0)
    assert day19.daily_pnl == pytest.approx(100.0)    # pure market move
    assert day19.daily_pnl_pct == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_daily_snapshot_buy_is_not_a_gain(db_session):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 18))
    await PortfolioService(db_session, base_currency=Currency.USD).compute_daily_snapshot(dt.date(2026, 8, 18))
    # On the 19th, buy 5 more shares (a flow) and the price is unchanged.
    _lot(db_session, a, qty=5, purchase_date=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc), avg_cost=100.0)
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 19))

    day19 = await PortfolioService(db_session, base_currency=Currency.USD).compute_daily_snapshot(dt.date(2026, 8, 19))

    assert day19.nav == pytest.approx(1500.0)         # 15 shares * 100
    assert day19.net_flow == pytest.approx(500.0)     # bought 5 * 100
    assert day19.daily_pnl == pytest.approx(0.0)      # 1500 - 1000 - 500 = 0
    assert day19.daily_pnl_pct == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_daily_snapshot_upsert_is_idempotent(db_session):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 18))
    svc = PortfolioService(db_session, base_currency=Currency.USD)
    await svc.compute_daily_snapshot(dt.date(2026, 8, 18))
    await svc.compute_daily_snapshot(dt.date(2026, 8, 18))
    assert db_session.query(PortfolioSnapshot).filter_by(date=dt.date(2026, 8, 18)).count() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_daily_pnl.py -v -k daily_snapshot`
Expected: FAIL — `AttributeError: 'PortfolioService' object has no attribute 'compute_daily_snapshot'`.

- [ ] **Step 3: Implement `compute_daily_snapshot`**

```python
# backend/app/services/portfolio.py — add these imports at top if missing:
#   import uuid; from datetime import date as date_cls, datetime, time as dt_time, timezone
#   from ..models.portfolio_snapshot import PortfolioSnapshot
# (date_cls / datetime / timezone / uuid already imported in this module.)

    async def compute_daily_snapshot(self, day) -> "PortfolioSnapshot | None":
        """Compute and upsert the portfolio_snapshots row for `day` (a date).

        NAV is valued in USD at end of `day`; net_flow is the USD cost of lots
        purchased on `day`; daily_pnl is flow-adjusted against the most recent
        stored row before `day`. Idempotent (upsert on date)."""
        end_of_day = datetime.combine(day, dt_time(23, 59, 59), tzinfo=timezone.utc)
        holdings = self.db.query(HoldingLot).all()

        # NAV in USD at end of `day` (base_currency is USD for the stored series).
        usd = PortfolioService(self.db, base_currency=Currency.USD)
        nav = await usd._get_nav_at_date(end_of_day, holdings=holdings)
        if nav is None:
            return None

        # Net USD flow = cost basis of lots purchased on `day` (buys only).
        net_flow = 0.0
        for lot in holdings:
            pd = lot.purchase_date
            if pd is None:
                continue
            if _ensure_aware(pd).date() == day:
                try:
                    cost_usd = await usd.fx_service.convert(
                        lot.avg_cost, Currency(lot.cost_currency), Currency.USD
                    )
                except Exception:
                    cost_usd = lot.avg_cost
                net_flow += cost_usd * lot.quantity

        # Previous stored row (any earlier date).
        prev = (
            self.db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.date < day)
            .order_by(PortfolioSnapshot.date.desc())
            .first()
        )
        prev_nav = prev.nav if prev else None

        if prev_nav and prev_nav > 0:
            daily_pnl = nav - prev_nav - net_flow
            daily_pnl_pct = daily_pnl / prev_nav * 100
        else:
            daily_pnl = 0.0
            daily_pnl_pct = 0.0

        row = self.db.query(PortfolioSnapshot).filter_by(date=day).first()
        if row is None:
            row = PortfolioSnapshot(id=str(uuid.uuid4()), date=day)
            self.db.add(row)
        row.nav = nav
        row.net_flow = net_flow
        row.daily_pnl = daily_pnl
        row.daily_pnl_pct = daily_pnl_pct
        row.base_currency = "USD"
        self.db.commit()
        self.db.refresh(row)
        return row
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_daily_pnl.py -v -k daily_snapshot`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/portfolio.py backend/tests/test_daily_pnl.py
git commit -m "feat(backend): flow-adjusted compute_daily_snapshot with upsert"
```

---

### Task 4: `backfill_daily_snapshots()` service method

**Files:**
- Modify: `backend/app/services/portfolio.py`
- Test: `backend/tests/test_daily_pnl.py`

**Interfaces:**
- Consumes: `compute_daily_snapshot` (Task 3), `PriceSnapshot`, `func.date`.
- Produces: `async def backfill_daily_snapshots(self) -> int` — for each distinct calendar date that has any `price_snapshots` row, oldest→newest, calls `compute_daily_snapshot(day)`. Returns the number of rows written. Safe to re-run (upsert).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_daily_pnl.py — add
@pytest.mark.asyncio
async def test_backfill_builds_ordered_series_and_is_idempotent(db_session):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    _snap(db_session, a, price=100.0, day=dt.date(2026, 8, 17))
    _snap(db_session, a, price=105.0, day=dt.date(2026, 8, 18))
    _snap(db_session, a, price=105.0, day=dt.date(2026, 8, 19))
    svc = PortfolioService(db_session, base_currency=Currency.USD)

    written = await svc.backfill_daily_snapshots()
    rows = db_session.query(PortfolioSnapshot).order_by(PortfolioSnapshot.date).all()

    assert written == 3
    assert [r.date for r in rows] == [dt.date(2026, 8, 17), dt.date(2026, 8, 18), dt.date(2026, 8, 19)]
    assert rows[0].daily_pnl == pytest.approx(0.0)        # first day
    assert rows[1].daily_pnl == pytest.approx(50.0)       # +5 * 10 shares
    assert rows[2].daily_pnl == pytest.approx(0.0)        # flat

    # Re-run: no duplicate dates.
    await svc.backfill_daily_snapshots()
    assert db_session.query(PortfolioSnapshot).count() == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_daily_pnl.py -v -k backfill`
Expected: FAIL — `AttributeError: ... 'backfill_daily_snapshots'`.

- [ ] **Step 3: Implement `backfill_daily_snapshots`**

```python
# backend/app/services/portfolio.py — add to PortfolioService.
# `func` is imported in this module already (used by get_nav_history).
    async def backfill_daily_snapshots(self) -> int:
        """Build the daily series from every distinct date that has price
        snapshots, oldest first. Idempotent (compute_daily_snapshot upserts)."""
        date_rows = (
            self.db.query(func.date(PriceSnapshot.timestamp))
            .distinct()
            .order_by(func.date(PriceSnapshot.timestamp))
            .all()
        )
        written = 0
        for (raw_date,) in date_rows:
            if isinstance(raw_date, str):
                day = datetime.strptime(raw_date, "%Y-%m-%d").date()
            elif isinstance(raw_date, datetime):
                day = raw_date.date()
            elif isinstance(raw_date, date_cls):
                day = raw_date
            else:
                continue
            row = await self.compute_daily_snapshot(day)
            if row is not None:
                written += 1
        return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_daily_pnl.py -v -k backfill`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/portfolio.py backend/tests/test_daily_pnl.py
git commit -m "feat(backend): backfill_daily_snapshots builds the series from price history"
```

---

### Task 5: `get_daily_pnl_series(period)` read method (USD→display conversion)

**Files:**
- Modify: `backend/app/services/portfolio.py`
- Test: `backend/tests/test_daily_pnl.py`

**Interfaces:**
- Consumes: `PortfolioSnapshot`, `self.fx_service.convert`, `self.base_currency`, existing `HISTORY_PERIOD_DAYS` map + `DEFAULT_HISTORY_PERIOD` constant in this module.
- Produces: `async def get_daily_pnl_series(self, period: str) -> dict` returning
  `{"base_currency": <ccy>, "period": <period>, "points": [{"date": iso, "nav": float, "dailyPnL": float, "dailyPnLPercent": float, "netFlow": float}]}`. NAV/dailyPnL/netFlow converted USD→`self.base_currency`; `dailyPnLPercent` passed through (currency-invariant).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_daily_pnl.py — add (reuses no_live_fx? use USD to avoid FX)
@pytest.mark.asyncio
async def test_daily_pnl_series_returns_window_in_usd(db_session):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    for day, price in [(dt.date(2026, 8, 18), 100.0), (dt.date(2026, 8, 19), 110.0)]:
        _snap(db_session, a, price=price, day=day)
    svc = PortfolioService(db_session, base_currency=Currency.USD)
    await svc.backfill_daily_snapshots()

    result = await svc.get_daily_pnl_series("1y")

    assert result["base_currency"] == "USD"
    assert result["period"] == "1y"
    assert len(result["points"]) == 2
    last = result["points"][-1]
    assert last["nav"] == pytest.approx(1100.0)
    assert last["dailyPnL"] == pytest.approx(100.0)
    assert last["dailyPnLPercent"] == pytest.approx(10.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_daily_pnl.py -v -k series`
Expected: FAIL — no `get_daily_pnl_series`.

- [ ] **Step 3: Implement `get_daily_pnl_series`**

```python
# backend/app/services/portfolio.py — add to PortfolioService.
    async def get_daily_pnl_series(self, period: str = DEFAULT_HISTORY_PERIOD) -> dict:
        days = HISTORY_PERIOD_DAYS.get(period, HISTORY_PERIOD_DAYS[DEFAULT_HISTORY_PERIOD])
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=days)).date()

        rows = (
            self.db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.date >= start)
            .order_by(PortfolioSnapshot.date)
            .all()
        )

        async def to_display(usd_amount: float) -> float:
            if self.base_currency == Currency.USD:
                return usd_amount
            try:
                return await self.fx_service.convert(usd_amount, Currency.USD, self.base_currency)
            except Exception:
                return usd_amount

        points = []
        for r in rows:
            points.append({
                "date": r.date.isoformat(),
                "nav": await to_display(r.nav),
                "dailyPnL": await to_display(r.daily_pnl),
                "dailyPnLPercent": r.daily_pnl_pct,
                "netFlow": await to_display(r.net_flow),
            })

        return {
            "base_currency": self.base_currency.value,
            "period": period,
            "points": points,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_daily_pnl.py -v -k series`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/portfolio.py backend/tests/test_daily_pnl.py
git commit -m "feat(backend): get_daily_pnl_series reads stored series in display currency"
```

---

### Task 6: `GET /api/portfolio/daily-pnl` endpoint (cached)

**Files:**
- Modify: `backend/app/routers/portfolio.py`
- Test: `backend/tests/test_daily_pnl.py`

**Interfaces:**
- Consumes: `PortfolioService.get_daily_pnl_series`, `cache`, existing `VALID_HISTORY_PERIODS`, `HISTORY_CACHE_TTL_SECONDS`.
- Produces: `GET /api/portfolio/daily-pnl?period=&base_currency=` → the series dict; 400 on invalid period; cached per `(period, currency)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_daily_pnl.py — add
from app.routers.portfolio import get_portfolio_daily_pnl  # will exist after impl


@pytest.mark.asyncio
async def test_daily_pnl_endpoint_invalid_period_400(db_session):
    import pytest as _pytest
    from fastapi import HTTPException
    with _pytest.raises(HTTPException) as exc:
        await get_portfolio_daily_pnl(period="bogus", base_currency=Currency.USD, db=db_session)
    assert exc.value.status_code == 400
```

Add a cache stub fixture (mirror `fake_history_cache` in `test_portfolio_base_currency.py`) at the top of the test for the happy path:

```python
@pytest.fixture()
def stub_cache(monkeypatch):
    store = {}
    async def _get(k): return store.get(k)
    async def _set(k, v, ttl=60): store[k] = v
    monkeypatch.setattr("app.routers.portfolio.cache.get", _get)
    monkeypatch.setattr("app.routers.portfolio.cache.set", _set)
    return store


@pytest.mark.asyncio
async def test_daily_pnl_endpoint_returns_series(db_session, stub_cache):
    a = _asset(db_session)
    _lot(db_session, a, qty=10, purchase_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    for day, price in [(dt.date(2026, 8, 18), 100.0), (dt.date(2026, 8, 19), 110.0)]:
        _snap(db_session, a, price=price, day=day)
    await PortfolioService(db_session, base_currency=Currency.USD).backfill_daily_snapshots()

    result = await get_portfolio_daily_pnl(period="1y", base_currency=Currency.USD, db=db_session)
    assert len(result["points"]) == 2
    assert result["points"][-1]["dailyPnL"] == pytest.approx(100.0)
    assert set(stub_cache.keys()) == {"portfolio:daily-pnl:1y:USD"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_daily_pnl.py -v -k endpoint`
Expected: FAIL — `ImportError: cannot import name 'get_portfolio_daily_pnl'`.

- [ ] **Step 3: Implement the endpoint**

```python
# backend/app/routers/portfolio.py — add after get_portfolio_history
@router.get("/daily-pnl")
async def get_portfolio_daily_pnl(
    period: str = "3m",
    base_currency: Currency = Currency.USD,
    db: Session = Depends(get_db),
):
    """Stored flow-adjusted daily P&L + EOD NAV series for the dashboard bar chart."""
    if period not in VALID_HISTORY_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid period '{period}'. Must be one of {sorted(VALID_HISTORY_PERIODS)}",
        )

    cache_key = f"portfolio:daily-pnl:{period}:{base_currency.value}"
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    portfolio_service = PortfolioService(db, base_currency=base_currency)
    result = await portfolio_service.get_daily_pnl_series(period)

    await cache.set(cache_key, result, ttl=HISTORY_CACHE_TTL_SECONDS)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_daily_pnl.py -v -k endpoint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/portfolio.py backend/tests/test_daily_pnl.py
git commit -m "feat(backend): GET /api/portfolio/daily-pnl endpoint"
```

---

### Task 7: Scheduler — write today's row at EOD + backfill on startup

**Files:**
- Modify: `backend/app/tasks/scheduler.py`

**Interfaces:**
- Consumes: `PortfolioService.compute_daily_snapshot`, `PortfolioService.backfill_daily_snapshots`, `PortfolioSnapshot`, existing `SessionLocal`, `start_scheduler` job-registration block.
- Produces: appended call in `snapshot_eod_prices`; new `backfill_daily_snapshots_job()`; a `date`-run job registered in `start_scheduler`.

- [ ] **Step 1: Append daily-snapshot write to the EOD job**

In `snapshot_eod_prices`, after the per-asset snapshot loop completes and before the `finally`, add:

```python
        # After per-asset EOD prices are stored, roll up today's portfolio row.
        try:
            from datetime import date as _date
            svc = PortfolioService(db, base_currency=Currency.USD)
            await svc.compute_daily_snapshot(datetime.now(timezone.utc).date())
        except Exception as e:
            logger.warning("Daily portfolio snapshot failed: %s", e)
```

Add the import at the top of the file: `from ..services.portfolio import PortfolioService`.

- [ ] **Step 2: Add the startup backfill job function**

```python
# backend/app/tasks/scheduler.py
async def backfill_daily_snapshots_job() -> None:
    """One-off: populate portfolio_snapshots from price history if empty."""
    from ..models.portfolio_snapshot import PortfolioSnapshot
    db = SessionLocal()
    try:
        if db.query(PortfolioSnapshot).first() is not None:
            return  # already populated
        svc = PortfolioService(db, base_currency=Currency.USD)
        written = await svc.backfill_daily_snapshots()
        logger.info("Daily P&L backfill wrote %d rows", written)
    except Exception as e:
        logger.error("Daily P&L backfill error: %s", e)
    finally:
        db.close()
```

- [ ] **Step 3: Register the backfill job in `start_scheduler`**

```python
    # Backfill the daily P&L series once, 45s after startup (after the price
    # backfill at 15s has had time to populate price_snapshots).
    _scheduler.add_job(
        backfill_daily_snapshots_job,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=45),
        id="backfill_daily_pnl",
        name="Daily P&L Series Backfill",
        replace_existing=True,
    )
```

- [ ] **Step 4: Verify the whole backend suite still passes**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (existing + new daily-pnl tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/tasks/scheduler.py
git commit -m "feat(backend): write daily portfolio snapshot at EOD + startup backfill"
```

---

### Task 8: Frontend types + API client

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces: `DailyPnLPoint { date: string; nav: number; dailyPnL: number; dailyPnLPercent: number; netFlow: number }`, `DailyPnLResponse { base_currency: Currency; period: PortfolioHistoryPeriod; points: DailyPnLPoint[] }`, and `api.getDailyPnL(period, baseCurrency): Promise<DailyPnLResponse>`.

- [ ] **Step 1: Add the types**

```typescript
// frontend/src/lib/types.ts — after PortfolioHistoryResponse
export interface DailyPnLPoint {
  date: string;
  nav: number;
  dailyPnL: number;
  dailyPnLPercent: number;
  netFlow: number;
}

export interface DailyPnLResponse {
  base_currency: Currency;
  period: PortfolioHistoryPeriod;
  points: DailyPnLPoint[];
}
```

- [ ] **Step 2: Add the API method**

```typescript
// frontend/src/lib/api.ts — import DailyPnLResponse at top; add near getPortfolioHistory
  getDailyPnL: (period: PortfolioHistoryPeriod = "3m", baseCurrency: Currency = "USD") =>
    request<DailyPnLResponse>(`/portfolio/daily-pnl?period=${period}&base_currency=${baseCurrency}`),
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.app.json`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts
git commit -m "feat(frontend): daily-pnl API types and client method"
```

---

### Task 9: Daily P&L bar chart + NAV/Daily toggle on the Performance card

**Files:**
- Create: `frontend/src/components/dashboard/DailyPnLChart.tsx`
- Modify: `frontend/src/components/dashboard/PerformanceChart.tsx` (add a NAV / Daily P&L toggle that swaps in the new chart)

**Interfaces:**
- Consumes: `api.getDailyPnL`, `DailyPnLResponse`, `usePortfolio()` for `baseCurrency`, `formatMoney` from `@/lib/utils`, Recharts `BarChart`/`Bar`/`Cell`/`XAxis`/`YAxis`/`Tooltip`/`ResponsiveContainer`, existing `PortfolioHistoryPeriod` + the period-selector UI already in `PerformanceChart`.
- Produces: `DailyPnLChart({ period }: { period: PortfolioHistoryPeriod })` rendering green/red bars.

- [ ] **Step 1: Create the bar chart component**

```tsx
// frontend/src/components/dashboard/DailyPnLChart.tsx
import { useQuery } from '@tanstack/react-query';
import { BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { format } from 'date-fns';
import { api } from '@/lib/api';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { formatMoney } from '@/lib/utils';
import type { PortfolioHistoryPeriod } from '@/lib/types';

export function DailyPnLChart({ period }: { period: PortfolioHistoryPeriod }) {
  const { baseCurrency } = usePortfolio();
  const { data, isLoading, isError } = useQuery({
    queryKey: ['portfolio', 'daily-pnl', period, baseCurrency],
    queryFn: () => api.getDailyPnL(period, baseCurrency),
    staleTime: 60_000,
  });

  if (isLoading) return <div className="h-[320px] animate-pulse rounded-lg bg-muted/30" />;
  if (isError || !data || data.points.length === 0) {
    return <div className="flex h-[320px] items-center justify-center text-sm text-muted-foreground">No daily P&L data yet.</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart data={data.points} margin={{ top: 8, right: 8, left: 8, bottom: 8 }}>
        <XAxis
          dataKey="date"
          tickFormatter={(d) => format(new Date(d), 'MMM d')}
          tick={{ fontSize: 12 }}
          minTickGap={24}
        />
        <YAxis
          tickFormatter={(v) => formatMoney(v, baseCurrency)}
          tick={{ fontSize: 12 }}
          width={72}
        />
        <Tooltip
          formatter={(v: number) => formatMoney(v, baseCurrency, true)}
          labelFormatter={(d) => format(new Date(d as string), 'PP')}
        />
        <Bar dataKey="dailyPnL" radius={[2, 2, 0, 0]}>
          {data.points.map((p) => (
            <Cell key={p.date} fill={p.dailyPnL >= 0 ? 'hsl(var(--success))' : 'hsl(var(--destructive))'} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
```

- [ ] **Step 2: Add the NAV / Daily P&L toggle to `PerformanceChart`**

In `PerformanceChart.tsx`: add local `const [view, setView] = useState<'nav' | 'daily'>('nav');`, render a small two-button toggle next to the existing period selector, and when `view === 'daily'` render `<DailyPnLChart period={period} />` in place of the NAV line chart body (keep the period selector shared). Import `DailyPnLChart`. Update the info tooltip to note: "Daily P&L is flow-adjusted (buying isn't counted as a gain); sales aren't tracked yet."

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.app.json`
Expected: clean.

- [ ] **Step 4: Verify live**

Backend up + `npm run dev`; open the dashboard, toggle to Daily P&L, confirm green/red bars render and the axis shows the base-currency symbol. Screenshot to confirm.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/dashboard/DailyPnLChart.tsx frontend/src/components/dashboard/PerformanceChart.tsx
git commit -m "feat(frontend): daily P&L bar chart with NAV/Daily toggle"
```

---

## Self-Review

**Spec coverage:**
- EOD NAV stored per day → Task 1 (model) + Task 3 (`nav` written) ✓
- Flow-adjusted daily P&L → Task 3 (`net_flow`, `daily_pnl`) ✓
- Backfill from history → Task 4 + Task 7 startup job ✓
- Canonical USD + display conversion → Task 3 (store USD) + Task 5 (convert on read) ✓
- API endpoint → Task 6 ✓
- Bar chart + toggle → Task 9 ✓
- EOD job writes daily row → Task 7 ✓
- Sells-not-tracked limitation surfaced → Task 9 tooltip ✓
- Testing (flow math, idempotency, period, NAV persisted) → Tasks 3–6 ✓

**Placeholder scan:** No TBD/TODO; every code step has concrete content.

**Type consistency:** `compute_daily_snapshot(day: date) -> PortfolioSnapshot | None`, `backfill_daily_snapshots() -> int`, `get_daily_pnl_series(period) -> dict` with point keys `date/nav/dailyPnL/dailyPnLPercent/netFlow`; the endpoint and frontend `DailyPnLPoint`/`api.getDailyPnL` match those keys exactly. Cache-key format `portfolio:daily-pnl:{period}:{ccy}` matches the endpoint test.

**Note on FX in tests:** the service tests use `base_currency=USD` so `convert` short-circuits and no live FX/network is needed (the existing `no_real_notifications` autouse fixture doesn't touch FX; USD-only keeps tests offline).
