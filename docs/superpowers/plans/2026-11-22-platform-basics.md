# Navigator Platform Basics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One scheduler across all gunicorn workers, idempotent EOD with catch-up, alerts that never fire on stale prices, Alembic-owned schema, heartbeats plus `/health/deep`, and a stack that sits behind the shared `edge` Caddy.

**Architecture:** A Postgres session advisory lock (`7400418`) elects one `SchedulerLeader` per database; only the leader builds and runs the APScheduler. Alembic (`0001` baseline, `0002` trade_date + heartbeats) runs once per container from `docker-entrypoint.sh` via `python -m app.migrate` under lock `7400417`; `init_db()` does no DDL on Postgres. A pure `services/sessions.py` decides session hours, alertability and EOD `trade_date`; jobs record `job_heartbeats`, which `/health/deep` reads.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0.36, Alembic 1.14.0, APScheduler 3.10.4, psycopg2, pytest + pytest-asyncio, Docker Compose, nginx 1.25.

**Spec:** `docs/superpowers/specs/2026-11-22-platform-basics-design.md` (read it alongside this plan; "Ruling" and "Decision" text there is binding).

## Global Constraints

- Work only in `/Users/ziv.a/repos_/_wt/navigator-basics` on branch `chore/platform-basics`. Never touch `/Users/ziv.a/repos_/navigator`.
- Never open, cat, grep or otherwise read `.env`, `.env.prod` or any `.env*` file. The one spec-mandated edit to `.env.prod.example` (Task 12) is done blind with the exact `sed` given there.
- Never log or return secrets, quantities or costs. Exception text goes through `services/log_redaction.redact_secrets` before it is logged or stored.
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit per task. Never push.
- No new dependencies. `zoneinfo` is backed by the `tzdata` wheel pandas already installs.
- Advisory-lock keys, one module (`app/locks.py`): `MIGRATION_LOCK_KEY = 7400417`, `SCHEDULER_LOCK_KEY = 7400418`. Never reuse `7400419`/`7400420` (trender).
- Alembic revision ids are readable: `0001` (`0001_baseline.py`, down `None`), `0002` (`0002_eod_trade_date_and_heartbeats.py`, down `0001`).
- No DDL at app boot on Postgres; no `create_all` against Postgres outside `app/migrate.py`'s legacy adopt path. SQLite (`init_db` and tests) keeps `create_all`.
- Production keeps `--workers 2`. Nothing may assume a single worker.
- `/health/deep` never shows quantities, costs, symbols, error text or secrets; `detail` holds counts and ages only.
- Navigator gets no auth of its own; the edge does basic auth.
- Don't change what history reads (`PortfolioService._latest_snapshot`) and keep writing intraday quote snapshots.
- Cron jobs use `America/New_York`; scheduler `timezone="UTC"`; `job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120}`.
- TASE hours are data constants in `sessions.py`, not logic.
- **Python env (once, Task 1 Step 1):** `cd backend && python3.11 -m venv .venv && .venv/bin/pip install -q -r requirements.txt`. Default suite: `cd backend && .venv/bin/python -m pytest -q` (SQLite, must stay green after every task).
- **Postgres tests** are opt-in exactly like `tests/test_enum_migration.py` (`@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), ...)`, one scratch schema per test, dropped `CASCADE` at the end). Run them against a throwaway container on a free port:
  ```bash
  lsof -nP -iTCP:55432 -sTCP:LISTEN && echo "busy: pick another port"
  docker run -d --rm --name navpg -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:15
  for i in $(seq 30); do docker exec navpg pg_isready -U postgres -q && break; sleep 1; done
  cd backend && TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:55432/postgres .venv/bin/python -m pytest -q tests/test_pg_*.py tests/test_enum_migration.py
  docker stop navpg
  ```

## Review Focus

1. **Fresh install, nothing held, no job has succeeded yet.** Right after the first deploy `/health/deep` must be `ok` (200), not `down`: jobs fall back to `scheduler_leader.last_started_at`, price checks over 0 watched assets pass. Test: Task 11 `test_fresh_install_is_ok`.
2. **Naive timestamps.** SQLite returns naive datetimes, and pre-existing Polygon/Finnhub snapshot rows were stored naive. Every comparison with an aware `now` treats naive as UTC (reuse `services/portfolio._ensure_aware`); no `TypeError`. Test: Task 11 `test_naive_times_treated_as_utc`.
3. **Postgres restarts under the leader loop.** `try_acquire`/`ping` raising (`OperationalError`) must not kill the loop task; the leader demotes and the next tick reconnects. Test: Task 4 `test_loop_survives_lock_errors`.
4. **Quotes cached in Redis before this deploy** have no `marketTime`. For up to 60 s they are skipped (no trigger, no high-water ratchet), never evaluated. Test: Task 10 `test_cached_quote_without_market_time_is_skipped`.
5. **Crypto rows stored with `market_region=US` or symbol already `BTC-USD`.** Classified `crypto` (asset type wins), and the Yahoo fallback never asks for `BTC-USD-USD`. Tests: Task 7 `test_classify_crypto_wins_over_region`, `test_yahoo_crypto_fallback_does_not_double_suffix`.

---

### Task 1: Alembic scaffold, baseline `0001`, legacy fixtures

**Files:**
- Create: `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`, `backend/alembic/versions/0001_baseline.py`
- Create: `backend/app/locks.py`, `backend/app/schema_version.py`
- Create: `backend/tests/pg_helpers.py`, `backend/tests/test_pg_migrations.py`
- Create: `backend/tests/fixtures/legacy_tase.sql`, `backend/tests/fixtures/legacy_pre_tase.sql`

**Interfaces:**
- Produces: `app.locks.MIGRATION_LOCK_KEY = 7400417`, `SCHEDULER_LOCK_KEY = 7400418`.
- Produces: `app.schema_version.ALEMBIC_INI: Path` (`backend/alembic.ini`); `alembic_config(engine: Engine | None = None, *, lock_held: bool = False) -> Config` (sets `cfg.attributes["engine"]`, `["lock_held"]`); `head_revision() -> str`; `db_revision(conn: Connection) -> str | None` (None when `alembic_version` is absent).
- Produces: `tests.pg_helpers.requires_pg` (the skipif marker), `scratch_schema() -> ContextManager[Engine]` (creates `t_<hex8>` schema, yields an engine with `options=-csearch_path=<schema>`, drops it `CASCADE` on exit), `load_sql(engine, path)`.
- Produces: `env.py` contract: uses `config.attributes["engine"]` or else `create_engine(settings.database_url, poolclass=NullPool)`; takes `pg_advisory_lock(MIGRATION_LOCK_KEY)` on its connection and commits (session lock survives) unless `config.attributes["lock_held"]`; `target_metadata = Base.metadata` after `import app.models`; `compare_type=True`; offline mode raises.

- [ ] **Step 1: Create the venv and confirm the default suite is green before any change** (see Global Constraints). Expected: all pass, PG tests skipped.

- [ ] **Step 2: Capture the legacy fixtures before touching `init_db`.** Start `navpg` (Global Constraints). In a scratch schema run today's `app.database.init_db()` (monkeypatch `database.engine`/`SessionLocal` to the scoped engine, as `test_pg_full_init_db_concurrent_on_old_db` does). Dump with `docker exec navpg pg_dump -U postgres -d postgres --schema-only --no-owner --no-privileges -n <schema>`. Strip every schema qualifier, `SET` line and `set_config` call so the file loads under any `search_path`. Append `INSERT INTO maintenance_runs (name, ran_at) VALUES ('minor_units_cutover', '2026-11-17 08:00:00+00');`. Save as `legacy_tase.sql`. Derive `legacy_pre_tase.sql` from it by hand: `currency` has only `USD,EUR,GBP,JPY,CHF`; `marketregion` has no `IL`; `assettype` has no `INDEX`; `alertrule` has no `TRAILING_STOP`; no `alertintent` type; `price_alerts` lacks `intent, trail_percent, trail_amount, high_water_mark, notified_at, notification_skipped_at` and has `threshold NOT NULL`; no `maintenance_runs`, no `portfolio_snapshots`, no marker insert.

- [ ] **Step 3: Write the failing test** in `tests/test_pg_migrations.py`:

```python
@requires_pg
def test_pg_upgrade_empty_matches_models():
    with scratch_schema() as eng:
        command.upgrade(alembic_config(eng), "head")
        with eng.connect() as c:
            assert compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata) == []
            assert db_revision(c) == head_revision()
            assert enum_labels(c, "currency") == [m.name for m in Currency]          # 18, model order
            assert enum_labels(c, "assettype") == ["STOCK","ETF","CRYPTO","FUND","INDEX","OTHER"]
            assert enum_labels(c, "marketregion") == ["US","EU","ASIA","IL"]
            assert enum_labels(c, "alertrule") == ["PRICE_BELOW","PRICE_ABOVE","TRAILING_STOP"]
            assert enum_labels(c, "alertintent") == ["BUY","SELL"]
            assert c.exec_driver_sql("SELECT count(*) FROM maintenance_runs WHERE name='minor_units_cutover'").scalar() == 1
```
(`enum_labels` reads `pg_enum` ordered by `enumsortorder` for the type in `current_schema()`; put it in `pg_helpers`.)

- [ ] **Step 4: Run it to verify it fails.** `TEST_POSTGRES_URL=... .venv/bin/python -m pytest -q tests/test_pg_migrations.py` → FAIL (`ModuleNotFoundError: app.schema_version`).

- [ ] **Step 5: Scaffold Alembic and generate `0001`.** Write `alembic.ini` (`script_location = %(here)s/alembic`, `prepend_sys_path = .`, `[logger_alembic] level = WARN`), `env.py` per the contract above, `app/locks.py`, `app/schema_version.py`. In `navpg` create an empty database `baseline_gen`, then `DATABASE_URL=postgresql://postgres:postgres@localhost:55432/baseline_gen .venv/bin/alembic revision --autogenerate -m baseline --rev-id 0001`. Hand-check the result against the `legacy_tase.sql` dump: 11 tables, `price_alerts.threshold` nullable, trailing columns present, both `ix_news_items_published_at` and `ix_news_published_at`, unique `ix_portfolio_snapshots_date`. Add the data step `INSERT INTO maintenance_runs (name, ran_at) VALUES ('minor_units_cutover', now()) ON CONFLICT (name) DO NOTHING`. `downgrade()` raises `RuntimeError("baseline cannot be downgraded")`.

- [ ] **Step 6: Run the PG test and the default suite.** Expected: `test_pg_upgrade_empty_matches_models` PASS; default suite unchanged and green.

- [ ] **Step 7: Commit.** `git add backend/alembic.ini backend/alembic backend/app/locks.py backend/app/schema_version.py backend/tests/pg_helpers.py backend/tests/test_pg_migrations.py backend/tests/fixtures && git commit -m "feat(db): alembic baseline 0001 and legacy schema fixtures" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`

---

### Task 2: `python -m app.migrate`, legacy adopt path, no-DDL `init_db`, entrypoint

**Files:**
- Create: `backend/app/migrate.py`, `backend/app/legacy_schema.py`, `backend/docker-entrypoint.sh` (mode 755)
- Modify: `backend/app/database.py:31-166` (init_db; move enum/price-alert DDL out; delete `_ENUM_LOCK_KEY`, `_pg_init_lock`, `_migrate_enum_values`)
- Modify: `backend/Dockerfile`, `backend/Dockerfile.prod` (add `ENTRYPOINT ["/app/docker-entrypoint.sh"]`; CMD unchanged, 2 workers)
- Modify: `backend/docker-compose.yml:32-48` (mount `./alembic:/app/alembic`; pass `SCHEDULER_ENABLED: ${SCHEDULER_ENABLED:-true}`; keep `uvicorn --reload` command, the entrypoint migrates first)
- Modify: `backend/tests/test_enum_migration.py:85-186`, `backend/tests/test_pg_migrations.py`

**Interfaces:**
- Consumes: Task 1 `alembic_config`, `head_revision`, `db_revision`, `MIGRATION_LOCK_KEY`, `scratch_schema`, `load_sql`.
- Produces: `app.locks.advisory_lock(engine: Engine, key: int) -> ContextManager[Connection]` (dedicated AUTOCOMMIT connection; lock/unlock; replaces `_pg_init_lock`).
- Produces: `app.legacy_schema`: `BASELINE_TABLES: tuple[str, ...]` (the 11 names), `EXPECTED_ENUM_LABELS: dict[str, frozenset[str]]`, `REQUIRED_PRICE_ALERT_COLUMNS: frozenset[str]` (every `PriceAlert` column), `apply_enum_values(engine)`, `migrate_price_alerts(engine)` (body moved unchanged except it takes `engine` and the probe adds `AND table_schema = current_schema()`), `converge(engine) -> None`, `verify(engine) -> list[str]` (problem strings; `[]` = ok).
- Produces: `app.migrate.AdoptionError(RuntimeError)`; `run_migrations(engine: Engine | None = None) -> tuple[str | None, str | None]` (before, after; resolves `app.database.engine` at call time when None); `main() -> int`.
- Produces: `app.database.ensure_cutover_marker(target_engine: Engine | None = None) -> None` (stays in `database.py`: SQLite `init_db` and three existing tests use it); `init_db()` raises `RuntimeError(f"DB at {rev}, code expects {head}; run python -m app.migrate")` on Postgres when they differ.

- [ ] **Step 1: Write the failing tests.** In `tests/test_pg_migrations.py`:

```python
@requires_pg
@pytest.mark.parametrize("fixture", ["legacy_pre_tase.sql", "legacy_tase.sql"])
def test_pg_adopt_legacy(fixture):          # load fixture, run_migrations(eng)
    assert before is None and after == head_revision()
    assert compare_metadata(...) == []
    # legacy_tase: marker ran_at == 2026-11-17 08:00 UTC (unchanged); pre_tase: marker row exists once

@requires_pg
def test_pg_adopt_refuses_partial(monkeypatch, capsys):   # legacy_tase + ALTER TABLE price_alerts DROP COLUMN acknowledged_at
    assert migrate.main() == 1                             # with app.database.engine patched to the scoped engine
    assert db_revision(c) is None

@requires_pg
def test_pg_migrate_concurrent():           # two threads + Barrier on one empty schema
    assert not errors and c.exec_driver_sql("SELECT count(*) FROM alembic_version").scalar() == 1

@requires_pg
def test_pg_init_db_raises_when_not_at_head(monkeypatch):  # upgrade head, then UPDATE alembic_version SET version_num='0000'
    with pytest.raises(RuntimeError, match="run python -m app.migrate"): database.init_db()

@requires_pg
def test_pg_init_db_does_no_ddl_at_head(monkeypatch):      # table list identical before/after init_db()

@requires_pg
def test_pg_migrate_main_prints_only_revisions(monkeypatch, capsys):
    assert migrate.main() == 0 and capsys.readouterr().out.strip().splitlines() == [f"schema: none -> {head_revision()}"]
```
In `test_enum_migration.py`: point `test_pg_migrate_enum_values_on_old_db` at `legacy_schema.apply_enum_values` under `locks.advisory_lock(scoped, MIGRATION_LOCK_KEY)`; delete `test_pg_full_init_db_concurrent_on_old_db` (superseded by `test_pg_migrate_concurrent`). Default suite keeps `test_ensure_cutover_marker_*` unchanged.

- [ ] **Step 2: Run to verify failure.** PG run → FAIL (`ModuleNotFoundError: app.migrate`).

- [ ] **Step 3: Implement `legacy_schema.converge(engine)`** in this order: `apply_enum_values` (13 currencies, `IL`, `INDEX`) → `Base.metadata.create_all(engine, tables=[baseline tables except price_snapshots])` → `migrate_price_alerts` → `ensure_cutover_marker(engine)`. `verify` checks the 11 tables exist in `current_schema()`, each enum's label *set* equals `EXPECTED_ENUM_LABELS` (order differs on adopted DBs), and `price_alerts` has every `REQUIRED_PRICE_ALERT_COLUMNS` column.

- [ ] **Step 4: Implement `run_migrations`** under `advisory_lock(engine, MIGRATION_LOCK_KEY)` with `alembic_config(engine, lock_held=True)`: no `alembic_version` and no `assets` → `upgrade head`; `assets` without `alembic_version` → `converge`, `verify` (non-empty → `AdoptionError`, nothing stamped), `stamp 0001`, `upgrade head`; `alembic_version` present → `upgrade head`. `main()` prints `schema: {before or 'none'} -> {after}` and returns 0; on `AdoptionError` prints the problems to stderr and returns 1. `if __name__ == "__main__": sys.exit(main())`.

- [ ] **Step 5: Rewrite `init_db()`.** SQLite: `create_all` + `ensure_cutover_marker()`. Postgres: compare `db_revision` with `head_revision()`, raise as above, no DDL. Write `docker-entrypoint.sh` (`#!/bin/sh`, `set -e`, `python -m app.migrate`, `exec "$@"`), `chmod +x`, add `ENTRYPOINT` to both Dockerfiles.

- [ ] **Step 6: Run tests.** PG suite PASS; default suite green. Also `docker build -f backend/Dockerfile.prod backend -t navigator-api:test` succeeds.

- [ ] **Step 7: Commit.** `feat(db): app.migrate with legacy adopt path; init_db does no DDL on Postgres` (+ trailer).

---

### Task 3: Revision `0002`, `trade_date`, `JobHeartbeat`, `upsert_eod`

**Files:**
- Create: `backend/alembic/versions/0002_eod_trade_date_and_heartbeats.py`, `backend/app/models/job_heartbeat.py`, `backend/app/db_upsert.py`, `backend/app/services/eod.py`
- Modify: `backend/app/models/price.py:8-24`, `backend/app/models/__init__.py`, `backend/tests/conftest.py:27` (import `job_heartbeat`)
- Test: `backend/tests/test_eod_upsert.py`, `backend/tests/test_pg_migrations.py`

**Interfaces:**
- Consumes: Task 1/2 helpers; `legacy_schema.BASELINE_TABLES`.
- Produces: `PriceSnapshot.trade_date = Column(Date, nullable=True)` and `Index("uq_price_snapshots_eod", "asset_id", "trade_date", unique=True, postgresql_where=text("trade_date IS NOT NULL"), sqlite_where=text("trade_date IS NOT NULL"))`.
- Produces: `JobHeartbeat` (`__tablename__ = "job_heartbeats"`; `job_name = Column(Text, primary_key=True)`; `last_started_at`, `last_success_at`, `last_error_at` = `DateTime(timezone=True)`; `last_error = Column(Text)`).
- Produces: `app.db_upsert.insert_for(bind: Session | Connection) -> Callable[[Table], Insert]` (postgresql or sqlite `insert`; anything else raises).
- Produces: `app.services.eod.EOD_SOURCES = ("eod_snapshot", "eod_backfill")`; `upsert_eod(db: Session, asset: Asset, price: float, currency: Currency | str, ts: datetime, trade_date: date, source: str, overwrite: bool) -> None` (executes `on_conflict_do_update` (price, currency, timestamp, source) or `on_conflict_do_nothing` with `index_elements=["asset_id","trade_date"], index_where=text("trade_date IS NOT NULL")`; does not commit).

- [ ] **Step 1: Write the failing tests.** `tests/test_eod_upsert.py` (SQLite):

```python
def test_upsert_eod_overwrite_updates_price_and_ts(db_session):   # two calls same (asset, date), overwrite=True
    assert rows == 1 and row.price == 101.0 and row.timestamp == ts2
def test_upsert_eod_do_nothing_keeps_existing(db_session):        # second call overwrite=False
    assert rows == 1 and row.price == 100.0
def test_quote_snapshots_keep_null_trade_date(db_session):        # two plain PriceSnapshot rows, same asset/time
    assert count == 2 and all(r.trade_date is None for r in rows)
```
`tests/test_pg_migrations.py`:
```python
@requires_pg
def test_pg_upsert_eod():               # same three semantics at head on Postgres
@requires_pg
def test_pg_baseline_tables_match_models():   # upgrade to "0001"; compare_metadata filtered to BASELINE_TABLES minus price_snapshots == []
@requires_pg
def test_pg_0002_dedupe():
    # at 0001 seed: asset US, asset crypto; 3 eod_snapshot rows for one NY date (16:30, 16:31, 16:35 ET);
    # Fri eod_snapshot + Sat eod_snapshot (same price) for the US asset; eod_snapshot+eod_backfill pair on one date; 3 'yahoo' intraday rows
    # upgrade head:
    assert one row per (asset_id, trade_date) and the kept id is the latest timestamp per key
    assert no US row has a Saturday/Sunday trade_date
    assert intraday count == 3 and their trade_date IS NULL
    with pytest.raises(IntegrityError): duplicate (asset_id, trade_date) insert
    command.downgrade(cfg, "0001")    # column, index, job_heartbeats gone
```

- [ ] **Step 2: Run to verify failure.** Default: FAIL (`ImportError: upsert_eod`); PG: FAIL.

- [ ] **Step 3: Implement models, helpers and `0002`.** `0002.upgrade()`: add column; set `trade_date` (`eod_snapshot`: `(timestamp AT TIME ZONE 'America/New_York')::date`; `eod_backfill`: `(timestamp AT TIME ZONE 'UTC')::date`); Ruling remap: for `eod_snapshot` rows of non-`CRYPTO` assets with `market_region IN ('US','IL')` or symbol `LIKE '%.TA'`, an ISO weekday 6 date moves back 1 day and 7 moves back 2 (both exchanges have a Mon–Fri week); de-duplicate with the spec's `row_number()` SQL and log only the deleted count; create `uq_price_snapshots_eod`; create `job_heartbeats`. `downgrade()` drops table, index, column.

- [ ] **Step 4: Run tests.** Default suite green; PG suite PASS (incl. `test_pg_upgrade_empty_matches_models` now at `0002`).

- [ ] **Step 5: Commit.** `feat(db): 0002 adds EOD trade_date unique key and job_heartbeats` (+ trailer).

---

### Task 4: Scheduler leader and `build_scheduler()`

**Files:**
- Create: `backend/app/tasks/leader.py`, `backend/app/tasks/heartbeat.py` (writer only)
- Modify: `backend/app/config.py:8-11` (`scheduler_enabled: bool = True`)
- Modify: `backend/app/tasks/scheduler.py:29, 396-479` (replace `start_scheduler`/`shutdown_scheduler`/`_scheduler`)
- Modify: `backend/app/main.py:7, 24-30`
- Test: `backend/tests/test_scheduler_leader.py`, `backend/tests/test_build_scheduler.py`, `backend/tests/test_pg_leader.py`

**Interfaces:**
- Consumes: `SCHEDULER_LOCK_KEY`, `JobHeartbeat`, `db_upsert.insert_for`.
- Produces: `app.tasks.heartbeat.upsert_heartbeat(job_name: str, **fields) -> None` (fields ⊆ `last_started_at, last_success_at, last_error_at, last_error`; own `SessionLocal()` session, `INSERT … ON CONFLICT (job_name) DO UPDATE` of only the given fields, commit, close).
- Produces: `scheduler.build_scheduler(startup_at: datetime | None = None) -> AsyncIOScheduler` (not started; adds `leader_startup` date job at `startup_at` when given, `misfire_grace_time=300`); `scheduler.leader_startup() -> None` (async; runs in order `backfill_historical_prices` (Task 9 swaps in `eod_catchup`), `backfill_daily_snapshots_job`, `breadth_service.ensure_breadth_warm`, each in its own try/except so one failure doesn't stop the next).
- Produces: `app.tasks.leader`: `SCHEDULER_LEADER_INTERVAL_SECONDS = 30`; `class LeaderLock(Protocol)`: `try_acquire() -> bool`, `ping() -> bool`, `release() -> None`; `PgAdvisoryLock(database_url: str, key: int = SCHEDULER_LOCK_KEY)` (own `create_engine(..., poolclass=NullPool, connect_args={"keepalives":1,"keepalives_idle":30,"keepalives_interval":10,"keepalives_count":3})`, one AUTOCOMMIT connection, never shared; `backend_pid() -> int | None`); `AlwaysLeaderLock`; `SchedulerLeader(lock: LeaderLock, scheduler_factory=build_scheduler, interval: float = 30, startup_delay: float = 15)` with `is_leader: bool` property, `async tick()`, `async start()`, `async stop()`; `make_leader() -> SchedulerLeader | None` (None when `settings.scheduler_enabled` is false; `AlwaysLeaderLock` for non-Postgres URLs); `set_current(leader | None)`; `is_leader() -> bool` (False when no current leader).

- [ ] **Step 1: Write the failing tests.**

```python
# test_scheduler_leader.py (FakeLock, FakeScheduler with start()/shutdown(wait)), heartbeat.SessionLocal -> sessions on db_session's engine
async def test_follower_never_builds_scheduler():      # try_acquire False x3
    assert factory_calls == 0 and not leader.is_leader
async def test_acquire_starts_scheduler_and_stamps_heartbeat(db_session):
    assert factory_calls == 1 and fake.started and row("scheduler_leader").last_started_at is not None
async def test_leader_tick_stamps_success(db_session):  # second tick, ping True
    assert row("scheduler_leader").last_success_at is not None and factory_calls == 1
async def test_ping_failure_shuts_scheduler_down():
    assert fake.shutdown_calls == [False] and not leader.is_leader
async def test_loop_survives_lock_errors():            # try_acquire raises OperationalError, then returns True
    assert leader.is_leader                            # after 2 ticks, no exception escaped
async def test_stop_shuts_down_then_releases():
    assert events == ["shutdown", "release"]
def test_scheduler_disabled_gives_no_leader(monkeypatch):   # settings.scheduler_enabled=False
    assert make_leader() is None
def test_sqlite_url_is_always_leader(monkeypatch):
    assert isinstance(make_leader()._lock, AlwaysLeaderLock)

# test_build_scheduler.py
def test_jobs_have_defaults_and_misfire():   # get_jobs() on an unstarted scheduler
    assert all(j.coalesce and j.max_instances == 1 for j in jobs)
    assert {j.id: j.misfire_grace_time for j in jobs}.items() >= {"eod_snapshot": 3600, "breadth_snapshot": 3600, "price_alert_evaluation": 120}.items()  # Tasks 9-10 add jobs
def test_cron_jobs_use_new_york():   assert str(job.trigger.timezone) == "America/New_York"
def test_leader_startup_scheduled_at_startup_at():   assert job.next_run_time == startup_at and job.misfire_grace_time == 300

# test_pg_leader.py
@requires_pg
async def test_pg_leader_failover(monkeypatch):   # two PgAdvisoryLock leaders, interval 1, upsert_heartbeat -> no-op
    assert [a.is_leader, b.is_leader].count(True) == 1
    # admin: SELECT pg_terminate_backend(<leader lock backend_pid>); tick both up to 2x
    assert other.is_leader and not old.is_leader and old_fake.shutdown_calls == [False]
```

- [ ] **Step 2: Run to verify failure.** FAIL (`ModuleNotFoundError: app.tasks.leader`).

- [ ] **Step 3: Implement.** Blocking lock/heartbeat calls go through `asyncio.to_thread`. Follower: `try_acquire` true → `build_scheduler(startup_at=now+startup_delay)`, `start()`, heartbeat `last_started_at`. Leader: `ping()` then heartbeat `last_success_at`; ping false/raises → `shutdown(wait=False)`, demote, drop the connection. Any exception in a tick is logged (redacted) and the loop continues. `stop()`: shutdown scheduler, `release()` (unlock + close). `main.py` lifespan: `init_db()`, Redis, `leader = make_leader()`; if set → `set_current(leader)`, `await leader.start()`; on exit `await leader.stop()`.

- [ ] **Step 4: Run tests.** Default suite green; `tests/test_pg_leader.py` PASS against `navpg`.

- [ ] **Step 5: Commit.** `feat(scheduler): single leader via advisory lock 7400418` (+ trailer).

---

### Task 5: Notification de-duplication with `SKIP LOCKED`

**Files:**
- Modify: `backend/app/tasks/scheduler.py:348-393` (`notify_triggered_alerts`)
- Test: `backend/tests/test_alerts.py` (add), `backend/tests/test_pg_notify.py`

**Interfaces:**
- Consumes: existing `NotificationService`, `format_alert_message`.
- Produces: `notify_triggered_alerts(db) -> None` unchanged signature; selects pending ids, then per id re-selects with `.with_for_update(skip_locked=True)` filtered on `notified_at IS NULL AND notification_skipped_at IS NULL`, sends, stamps, commits one alert at a time; failed send still retries next cycle.

- [ ] **Step 1: Write the failing tests.**

```python
async def test_notify_skips_alert_stamped_by_other_pass(eval_db, monkeypatch):
    # two pending alerts; fake sender stamps the *second* alert's notified_at in the DB during the first send
    assert sender.sent_ids == [first_id]
@requires_pg
def test_pg_notify_skip_locked(monkeypatch):
    # scratch schema + Base.metadata.create_all(scoped); 3 pending alerts; fake sender sleeps 0.2s and records ids
    # two threads, each asyncio.run(notify_triggered_alerts(Session(bind=scoped)))
    assert sorted(sender.sent_ids) == sorted(alert_ids)        # exactly one send per alert
```

- [ ] **Step 2: Run to verify failure.** `test_notify_skips_alert_stamped_by_other_pass` FAILS (two sends).

- [ ] **Step 3: Implement** the id-first, lock-per-row loop.

- [ ] **Step 4: Run tests.** Default suite green (existing notification tests unchanged); PG PASS.

- [ ] **Step 5: Commit.** `fix(alerts): one notification per alert across concurrent passes` (+ trailer).

---

### Task 6: `@heartbeat` wrapper and jobs that raise

**Files:**
- Modify: `backend/app/tasks/heartbeat.py`, `backend/app/tasks/scheduler.py:32-101, 231-345` and `build_scheduler`
- Modify: `backend/app/services/breadth.py:157-200`
- Test: `backend/tests/test_heartbeat.py`, `backend/tests/test_build_scheduler.py`, `backend/tests/test_alerts.py:381-397`

**Interfaces:**
- Consumes: Task 4 `upsert_heartbeat`, `leader.is_leader()`.
- Produces: `heartbeat(job_name: str) -> Callable[[AsyncJob], AsyncJob]` (wrapper sets `__heartbeat_name__ = job_name`; returns early without running when `leader.is_leader()` is false; upserts `last_started_at`, awaits the job, upserts `last_success_at`; on exception upserts `last_error_at` and `last_error = redact_secrets(str(e))[:500]`, logs the redacted text, and swallows). Wrapping happens at registration in `build_scheduler`, not on the module functions, so direct calls in tests bypass it.
- Produces: jobs raise to fail: `snapshot_eod_prices` raises `RuntimeError("EOD snapshot: N asset(s) failed")` if any asset errored or the portfolio row raised (after finishing all assets); `evaluate_price_alerts` runs the notify pass and then re-raises a quote-fetch exception; `compute_and_store_breadth` raises `RuntimeError` when no constituents or no data downloaded.

- [ ] **Step 1: Write the failing tests.**

```python
async def test_success_stamps_last_success_at(hb_db):        # leader.is_leader -> True
    assert row.last_started_at and row.last_success_at and row.last_error_at is None
async def test_error_is_redacted_and_truncated(hb_db):       # job raises ValueError("token=SECRETKEY " + "x"*1000)
    assert "SECRETKEY" not in row.last_error and "<redacted>" in row.last_error and len(row.last_error) <= 500
async def test_job_rollback_keeps_heartbeat(hb_db):          # job adds a row in its own session, rolls back, raises
    assert row.last_started_at and row.last_error_at and job_rows == 0
async def test_non_leader_returns_without_running(hb_db):    # is_leader False
    assert calls == 0 and no heartbeat row
def test_job_ids_equal_heartbeat_names():
    assert all(j.func.__heartbeat_name__ == j.id for j in build_scheduler(startup_at=T).get_jobs())
async def test_evaluate_raises_when_quote_fetch_fails(...):  # replaces test_evaluate_leaves_alerts_untouched_when_quote_fetch_fails
    with pytest.raises(RuntimeError): ...;  assert alert untouched
async def test_eod_snapshot_raises_when_an_asset_fails(...): # 2 held assets, one get_quote raises
    with pytest.raises(RuntimeError, match="1 asset"): ...; assert the other asset's EOD row exists
async def test_breadth_raises_when_no_data(monkeypatch):     # _download_chunk_closes -> empty frames
    with pytest.raises(RuntimeError): await compute_and_store_breadth()
```
(`hb_db`: `heartbeat.SessionLocal` → a `sessionmaker` on `db_session`'s engine, so heartbeat and job use separate sessions.)

- [ ] **Step 2: Run to verify failure.** FAIL (`ImportError: heartbeat`).

- [ ] **Step 3: Implement** the wrapper, register every job as `heartbeat(<id>)(fn)` with `id=<same name>`, and convert the swallowing `except` blocks listed in the spec (§5 "Jobs must raise to fail"), keeping per-item `try/except`.

- [ ] **Step 4: Run tests.** Default suite green.

- [ ] **Step 5: Commit.** `feat(scheduler): job heartbeats; jobs raise to record failure` (+ trailer).

---

### Task 7: Session table, alertability, quote fields

Moved ahead of EOD: `eod_snapshot` writes only alertable quotes, so EOD needs this first.

**Files:**
- Create: `backend/app/services/sessions.py`, `backend/tests/test_sessions.py`
- Modify: `backend/app/providers/yahoo.py:61-97`, `coingecko.py:65,105`, `polygon.py:43`, `finnhub.py:41`
- Modify: `backend/app/services/market_data.py:66-80` (Yahoo crypto fallback after CoinGecko: Yahoo symbol `<SYM>-USD` unless the symbol already contains `-`; returned quote keeps the caller's symbol; `source="yahoo"`)
- Test: `backend/tests/test_yahoo_normalization.py`, `backend/tests/test_market_data_fallback.py`

**Interfaces:**
- Produces (Yahoo quote): `marketTime` = aware UTC ISO from `meta.regularMarketTime`; `exchangeTimezone` = `meta.exchangeTimezoneName`. All providers' `timestamp` are aware UTC (`datetime.now(timezone.utc)` / `fromtimestamp(t, tz=timezone.utc)`). Cache key stays `quote:v2:`.
- Produces (`app/services/sessions.py`):
  - `Exchange = Literal["US", "TASE", "crypto", "other"]`
  - `SESSION_TABLE: dict[str, tuple[str, dict[int, tuple[time, time]]]]` = `{"US": ("America/New_York", {0..4: (09:30, 16:00)}), "TASE": ("Asia/Jerusalem", {0..3: (09:59, 17:25), 4: (09:59, 14:00)})}` (weekday 0 = Monday). Check tase.co.il for the close times if you have web access and adjust the constants; if you cannot, keep these values, add `# UNVERIFIED against tase.co.il` and say so in your report.
  - `classify(asset: Asset) -> Exchange` (`crypto` if `asset_type == CRYPTO`; `TASE` if `market_region == IL` or symbol ends `.TA`; `US` if `market_region == US`; else `other`)
  - `session_bounds(exchange: str, day: date) -> tuple[datetime, datetime] | None` (aware UTC open/close; None when no session that weekday or no table)
  - `is_in_session(exchange: str, now: datetime) -> bool` (`crypto` and `other` always True)
  - `latest_session_open(exchange: str, now: datetime) -> datetime | None` (open of the most recent session that has started; walks back ≤ 7 days)
  - `ALERTABLE_SOURCES = {"stock": {"yahoo"}, "etf": {"yahoo"}, "index": {"yahoo"}, "crypto": {"coingecko", "yahoo"}}`
  - `is_alertable(quote: dict, asset: Asset, now: datetime) -> tuple[bool, str]`; reasons: `"ok"`, `"error"`, `"stale"`, `"db_cache"`, `"source"`, `"no_market_time"`, `"old_market_time"`, `"not_monitored"` (fund/other asset types). Crypto via Yahoo needs `marketTime`; crypto via CoinGecko has no time check; US/TASE need `marketTime >= latest_session_open`; `other` exchange needs `marketTime` ≤ 96 h old.
  - `trade_date_for(quote: dict, asset: Asset, now: datetime) -> date` (crypto: `now` UTC date; else `marketTime` in `exchangeTimezone`, falling back to the table tz).

- [ ] **Step 1: Write the failing tests** (`test_sessions.py`; 2026-11-20 is a Friday, 2026-11-22 a Sunday, 2026-11-23 a Monday):

```python
@pytest.mark.parametrize("quote", [stale, db_cache, polygon, finnhub, yahoo_without_market_time])
def test_rejected_sources(quote):              assert is_alertable(quote, us_stock, NOW)[0] is False
def test_us_monday_before_open_with_friday_close_ok():     # now Mon 09:00 ET, marketTime Fri 16:00 ET
    assert is_alertable(q, us_stock, now) == (True, "ok")
def test_us_monday_after_open_with_friday_close_rejected():  # now Mon 10:00 ET -> "old_market_time"
def test_us_after_friday_holiday_rejected():               # now Fri 11:00 ET, marketTime Thu 16:00 ET
def test_tase_friday_with_thursday_market_time_rejected(): # now Fri 11:00 IL, marketTime Thu 17:20 IL
def test_tase_sunday_with_friday_close_ok():               # now Sun 11:00 IL, marketTime Fri 13:59 IL
def test_crypto_coingecko_ok():                            assert is_alertable(cg_quote, btc, NOW) == (True, "ok")
def test_fund_not_monitored():                             assert is_alertable(q, fund, NOW) == (False, "not_monitored")
def test_us_open_tracks_dst():   # 2026-03-06 14:00Z out (09:00 EST); 2026-03-09 13:45Z in (09:45 EDT)
def test_tase_friday_close():    # 2026-11-20 11:59Z in (13:59 IL); 12:01Z out (14:01 IL)
def test_tase_sunday_closed():   # 2026-11-22 09:00Z out
def test_classify_crypto_wins_over_region():   # CRYPTO + market_region US -> "crypto"; "TEVA.TA" region US -> "TASE"
def test_trade_date_uses_exchange_timezone():  # marketTime 2026-11-19T22:30Z, exchangeTimezone Asia/Jerusalem -> date(2026,11,20)
```
`test_yahoo_normalization.py`: `test_yahoo_quote_carries_market_time` (meta `regularMarketTime=1763740800`, `exchangeTimezoneName="America/New_York"` → `marketTime == "2025-11-21T16:00:00+00:00"`, `exchangeTimezone` set), `test_provider_timestamps_are_aware_utc` (Yahoo/CoinGecko/Polygon/Finnhub mocked → `tzinfo` is UTC). `test_market_data_fallback.py`: `test_crypto_falls_back_to_yahoo_usd_pair` (CoinGecko fails → Yahoo asked for `BTC-USD`, `source == "yahoo"`, `symbol == "BTC"`), `test_yahoo_crypto_fallback_does_not_double_suffix` (asset `BTC-USD` → Yahoo asked for `BTC-USD`).

- [ ] **Step 2: Run to verify failure.** FAIL (`ModuleNotFoundError: app.services.sessions`).

- [ ] **Step 3: Implement** `sessions.py` (pure; `zoneinfo`), the provider fields and the crypto fallback.

- [ ] **Step 4: Run tests.** Default suite green.

- [ ] **Step 5: Commit.** `feat(markets): session table, alertable-price rule, Yahoo marketTime` (+ trailer).

---

### Task 8: Idempotent `eod_snapshot`

**Files:**
- Modify: `backend/app/tasks/scheduler.py:32-101` (`snapshot_eod_prices`)
- Test: `backend/tests/test_eod.py`

**Interfaces:**
- Consumes: `upsert_eod`, `is_alertable`, `trade_date_for`, `classify`.
- Produces: `scheduler._utcnow() -> datetime` (aware UTC; every job reads time through it so tests monkeypatch it).
- Behaviour: per held asset, a non-alertable quote is skipped (not an error); otherwise `upsert_eod(..., ts=marketTime (crypto: now), trade_date=trade_date_for(...), source="eod_snapshot", overwrite=True)` and commit per asset; then `compute_daily_snapshot(_utcnow().date())`; raise rule from Task 6 kept.

- [ ] **Step 1: Write the failing tests.**

```python
async def test_eod_twice_same_day_one_row_refreshed_price(eval_db, monkeypatch):   # quotes 100 then 101
    assert eod_rows == 1 and row.price == 101.0
async def test_eod_saturday_run_writes_friday_trade_date(...):   # now Sat 2026-11-21 21:30Z, marketTime Fri 21:00Z
    assert row.trade_date == date(2026, 11, 20) and no row with trade_date 2026-11-21
async def test_eod_tase_trade_date_from_market_time(...):        # exchangeTimezone Asia/Jerusalem
    assert row.trade_date == date(2026, 11, 20)
async def test_eod_skips_non_alertable_quote(...):              # stale quote
    assert eod_rows == 0                                       # and no exception
```

- [ ] **Step 2: Run to verify failure.** FAIL (two rows / Saturday row).

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run tests.** Default suite green.

- [ ] **Step 5: Commit.** `fix(eod): one EOD row per asset and trade date` (+ trailer).

---

### Task 9: `eod_catchup` and portfolio catch-up

**Files:**
- Modify: `backend/app/tasks/scheduler.py:119-228` (replace `backfill_historical_prices` with `eod_catchup`), `leader_startup`, `build_scheduler`
- Test: `backend/tests/test_eod_catchup.py`; move `test_backfill_closes_normalized` out of `backend/tests/test_yahoo_normalization.py:116-147` into it as `test_catchup_closes_normalized`

**Interfaces:**
- Consumes: `upsert_eod`, `session_bounds`, `classify`, `heartbeat`, `_utcnow`.
- Produces: `async eod_catchup() -> None`; `_catchup_start(last_trade_date: date | None, now: datetime) -> datetime` (`last − 5 days`, else `now − 183 days`; never earlier than `now − 400 days`); cron job `eod_catchup` 17:15 America/New_York, `misfire_grace_time=3600`; `leader_startup` step 1 becomes `heartbeat("eod_catchup")(eod_catchup)`.
- Rules: Yahoo chart `params={"interval": "1d", "period1": int(start.timestamp()), "period2": int(now.timestamp())}` (no `range`); crypto symbol mapping and unit normalisation as today; skip `close=None`; bar `trade_date` = bar timestamp in `meta.exchangeTimezoneName` (crypto: UTC date); skip the exchange-local today's bar unless `now` is past that day's session close (crypto: skip the current UTC day; `other`: always skip today); `ts` = `session_bounds(exchange, trade_date)[1]` (crypto: `23:59:59 UTC` of `trade_date`; `other`: bar timestamp); `source="eod_backfill"`, `overwrite=False`. Portfolio: for each UTC date D in `[today − 30, today)`, oldest first, where some held asset has an EOD row with `trade_date == D` and `portfolio_snapshots` has no row for D → `compute_daily_snapshot(D)`. Per-asset failures counted; raise `RuntimeError("EOD catch-up: N asset(s) failed")` at the end, after writing everything else.

- [ ] **Step 1: Write the failing tests** (httpx `MockTransport` as in `test_yahoo_normalization.py`):

```python
async def test_catchup_fills_three_day_gap(...):          # rows Mon..Tue; now Fri after close; bars Wed..Fri
    assert {r.trade_date for r in eod} >= {Wed, Thu, Fri}
async def test_catchup_requests_period_window(...):        assert "range" not in params and int(params["period1"]) == ts(last_trade_date - 5d)
async def test_catchup_does_not_overwrite_existing_row(...):  assert row.price == original
async def test_catchup_skips_todays_in_progress_bar(...):  # now Fri 15:00 ET -> no Fri row
async def test_catchup_row_ts_is_session_close(...):       # US trade_date 2026-11-19 -> ts 2026-11-19 21:00Z
async def test_catchup_computes_portfolio_only_for_missing_day(...):   # D-2 has a portfolio row, D-1 not
    assert computed_days == [D-1]
async def test_catchup_failure_writes_others_then_raises(...):  # 2 assets, one 500s
    with pytest.raises(RuntimeError, match="1 asset"): ...; assert other asset rows written
async def test_catchup_closes_normalized(...):             # ILA closes 6400, 6512 -> 64.0, 65.12 ILS
async def test_leader_startup_order(...):                  assert calls == ["eod_catchup", "backfill_daily_pnl", "breadth_warm"]
def test_eod_catchup_job_registered():                     # cron 17:15 America/New_York, misfire 3600
```

- [ ] **Step 2: Run to verify failure.** FAIL (`AttributeError: eod_catchup`).

- [ ] **Step 3: Implement.** Delete `backfill_historical_prices` and its "< 5 rows" gate.

- [ ] **Step 4: Run tests.** Default suite green.

- [ ] **Step 5: Commit.** `feat(eod): daily catch-up fills missed session days` (+ trailer).

---

### Task 10: Alerts skip non-alertable prices; `quote_refresh`; price-job failure rule

**Files:**
- Modify: `backend/app/tasks/scheduler.py:231-345` (`evaluate_price_alerts`), add `quote_refresh`, `build_scheduler`
- Modify: `backend/app/services/sessions.py` (add the batch rule and watched set)
- Modify: `backend/tests/factories.py:85-96` (`patch_quote`/`patch_quotes` fill `source="yahoo"` and `marketTime=<now UTC ISO>` via `setdefault`, so existing tests keep live quotes)
- Modify: `backend/tests/test_alert_cutover.py:39-48` (stale quote now hits the non-alertable skip: assert untouched alert and a warning containing `stale=1`)
- Test: `backend/tests/test_alert_staleness.py`

**Interfaces:**
- Consumes: `is_alertable`, `classify`, `is_in_session`, `heartbeat`, `_utcnow`.
- Produces (`sessions.py`): `watched_assets(db: Session) -> list[Asset]` (held ∪ assets with an active alert, distinct); `class QuoteBatchError(RuntimeError)`; `check_quote_batch(reasons: Mapping[str, str]) -> None` (ignores `"not_monitored"`; raises when there is ≥ 1 symbol and either zero are `"ok"` or more than 50 % are not `"ok"`; message holds counts per reason only).
- Produces: `async quote_refresh() -> None` (watched assets whose `classify` exchange `is_in_session`; `get_quotes`; `check_quote_batch`); job `quote_refresh` interval 5 min, misfire 120; `price_alert_evaluation` interval start offset +30 s so it rides the 60 s quote cache.
- Behaviour in `evaluate_price_alerts`: `is_alertable` runs before the cutover pause; non-alertable → no trigger, no `high_water_mark` change; fund alerts skipped as `not_monitored`; one WARNING per run with counts per reason (e.g. `non-alertable quotes skipped: stale=1 source=2`); the existing pause stays for alertable quotes; `check_quote_batch` raises after the notify pass.

- [ ] **Step 1: Write the failing tests.**

```python
async def test_stale_quote_no_trigger_and_hwm_unchanged(eval_db, monkeypatch):  # TSL hwm 100, stale 50
    assert a.is_active and a.triggered_at is None and a.high_water_mark == 100.0
async def test_polygon_quote_not_alertable(...):          # source polygon, price below threshold
    assert a.is_active
async def test_cached_quote_without_market_time_is_skipped(...):   # {"price": 50, "source": "yahoo", "marketTime": None}
    assert a.is_active and a.high_water_mark == 100.0
async def test_non_alertable_logs_one_warning_with_counts(caplog, ...):
    assert len([r for r in caplog.records if "non-alertable" in r.getMessage()]) == 1 and "stale=2" in msg
async def test_fund_alert_skipped_not_monitored(...):     assert a.is_active
async def test_evaluate_fails_when_majority_non_alertable(...):     # 3 symbols, 2 stale
    with pytest.raises(QuoteBatchError): ...
async def test_evaluate_ok_when_minority_non_alertable(...):        # 3 symbols, 1 stale -> no raise
async def test_quote_refresh_fetches_only_in_session_watched(...):  # now Sun 09:00Z: US+TASE closed, crypto open
    assert fetched == ["BTC"]
async def test_quote_refresh_raises_when_zero_succeed(...):         with pytest.raises(QuoteBatchError): ...
async def test_quote_refresh_no_watched_assets_is_success(...):     # no raise, no fetch
def test_quote_refresh_job_registered():   # interval 5 min, misfire 120
```

- [ ] **Step 2: Run to verify failure.** FAIL (stale quote ratchets the mark / `ImportError: QuoteBatchError`).

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run tests.** Default suite green, including the existing alert, trailing and cutover suites.

- [ ] **Step 5: Commit.** `fix(alerts): never evaluate or ratchet on stale or fallback prices` (+ trailer).

---

### Task 11: `GET /health` and `GET /health/deep`

**Files:**
- Create: `backend/app/services/health.py`, `backend/app/routers/health.py`, `backend/tests/test_health.py`
- Modify: `backend/app/main.py:62-76` (drop the inline `/health`; `app.include_router(health.router)` with no prefix)

**Interfaces:**
- Consumes: `JobHeartbeat`, `head_revision`, `db_revision`, `SCHEDULER_LOCK_KEY`, `watched_assets`, `classify`, `is_in_session`, `latest_session_open`, `_ensure_aware`.
- Produces (`services/health.py`): `@dataclass(frozen=True) class Check: ok: bool; detail: str; critical: bool = True`; `JOB_LIMITS: dict[str, tuple[timedelta, bool]]` = `quote_refresh` 15 min, `price_alert_evaluation` 15 min, `eod_snapshot` 26 h, `eod_catchup` 26 h (all critical), `breadth_snapshot` 26 h (not critical); `LEADER_LIMIT = timedelta(minutes=3)`; `leader_lock_held(db) -> bool` (Postgres: `pg_locks` advisory, `classid=0`, `objid=7400418`, `objsubid=1`, granted, current database; other dialects True); `deep_checks(db: Session, now: datetime) -> dict[str, Check]` (keys: `db`, `migrations`, `scheduler_leader`, `job_<name>` ×5, `alert_delivery`, `prices_us`, `prices_tase`, `prices_crypto`); `overall_status(checks) -> Literal["ok","degraded","down"]`; `_utcnow()`.
- Produces (router): `GET /health` → `200 {"status":"ok","checks":{"process":{"ok":true,"detail":"up"}}}`, no DB. `GET /health/deep` (sync `def`, so it runs in the threadpool; `Depends(get_db)`; on Postgres `SET LOCAL statement_timeout = '2s'` first) → `{"status", "checks": {name: {"ok","detail"}}}`, HTTP 503 only for `down`.
- Rules: `db` failure → every other check `Check(False, "skipped: db unreachable")`. Job reference = `last_success_at`, else `scheduler_leader.last_started_at`; detail like `last success 27h ago (limit 26h); last error 3m ago` (ages only, never `last_error` text). `alert_delivery` fails when any `triggered_at` is > 15 min old with both delivery stamps null. Prices: outside session (or < 15 min after open) → `Check(True, "closed")`; else each watched asset on that exchange needs a `source IN ('yahoo','coingecko')` snapshot ≥ now − 30 min; fund/other asset types count as `not_monitored` and never fail; detail `"2 of 11 watched assets without a fresh price for 30+ min; 3 not_monitored"`. `prices_crypto`: an asset fresh only from Yahoo → `Check(False, ..., critical=False)` (degraded); any asset with neither → critical failure.

- [ ] **Step 1: Write the failing tests** (SQLite; fixed `now`; `head_revision` monkeypatched and an `alembic_version` table created for `migrations`):

```python
def test_fresh_install_is_ok(db_session):          # no holdings/alerts; only scheduler_leader started 1 min ago
    assert overall_status(deep_checks(db_session, NOW)) == "ok"
@pytest.mark.parametrize("name,setup,ok", [...])   # one ok and one failing case per check key
def test_each_check(name, setup, ok, db_session):  assert deep_checks(db_session, NOW)[name].ok is ok
def test_status_mapping_and_503_only_on_down(client_deep):   # down -> 503; degraded (breadth only) -> 200; ok -> 200
def test_prices_closed_outside_session(db_session):          assert checks["prices_us"] == Check(True, "closed")
def test_never_run_job_uses_leader_start(db_session):        # leader started 20 h ago, eod_snapshot never ran -> ok; 27 h -> fail
def test_prices_crypto_degraded_when_only_yahoo_fresh(db_session):   assert not c.ok and not c.critical
def test_not_monitored_never_fails(db_session):              assert c.ok and "1 not_monitored" in c.detail
def test_db_unreachable_skips_rest(...):                     assert all(v.detail == "skipped: db unreachable" for k, v in checks.items() if k != "db")
def test_naive_times_treated_as_utc(db_session):             # naive heartbeat + snapshot rows; no TypeError, correct ages
def test_body_has_no_quantity_cost_symbol_or_error_text(client_deep):
    # lot qty 123.456, avg_cost 789.01, symbol "ZQXJ"; heartbeat last_error "ZQXJ boom"
    assert not any(s in resp.text for s in ("123.456", "789.01", "ZQXJ", "boom"))
def test_shallow_health_needs_no_db(monkeypatch):            # SessionLocal patched to raise
    assert r.status_code == 200 and r.json()["checks"]["process"] == {"ok": True, "detail": "up"}
```

- [ ] **Step 2: Run to verify failure.** FAIL (`ModuleNotFoundError: app.services.health`).

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run tests.** Default suite green.

- [ ] **Step 5: Commit.** `feat(health): shallow /health and /health/deep with heartbeats` (+ trailer).

---

### Task 12: Edge network: compose, nginx, deploy script

**Files:**
- Modify: `docker-compose.prod.yml` (whole file)
- Create: `nginx/conf.d/default.conf`; Delete: `nginx/conf.d/default.conf.template`, `scripts/init-letsencrypt.sh`
- Modify: `scripts/deploy.sh` (rewrite), `.env.prod.example` (blind sed), `scripts/provision.sh`, `scripts/cloud-init.yml` (one-line note each), `TODO-DEPLOYMENT.md` (must-do #3, Phase 3.2 SSL, 3.3 password protection → point at the zivalx-ops runbook)

**Interfaces:**
- Consumes: `GET /health`, `GET /health/deep` (Task 11).
- Produces (contract with ops/trender, spec §6 tables): top-level `name: navigator`; `container_name`s `navigator-postgres`, `navigator-redis`, `navigator-api`, `navigator-frontend`, `navigator-nginx`; only `nginx` joins `edge` (`networks: edge: {external: true, name: edge}`), `api` stays on `navigator-net` with `stop_grace_period: 30s`; no published ports; no certbot service or volumes; nginx mounts `./nginx/conf.d/default.conf` read-only with no `command`/`environment`.
- nginx `default.conf`: one `listen 80; server_name _;` server; `resolver 127.0.0.11 valid=10s;` and `set $api_upstream http://api:7000;` with `proxy_pass $api_upstream;`; `set_real_ip_from 172.16.0.0/12; set_real_ip_from 10.0.0.0/8; set_real_ip_from 192.168.0.0/16; real_ip_header X-Forwarded-For; real_ip_recursive on;`; `X-Forwarded-Proto $http_x_forwarded_proto`; `location = /health` and `location = /health/deep` without rate limit; `/api/` (with `limit_req zone=api burst=20 nodelay`) and the SPA block unchanged; keep X-Frame-Options, X-Content-Type-Options, X-XSS-Protection, Referrer-Policy; no HSTS, TLS, ACME or `auth_basic`.
- `deploy.sh` (`set -euo pipefail`): preflight `.env.prod` exists, `grep -qx 'POSTGRES_DB=navigator' .env.prod` (grep exit status only, never print the file), `docker network inspect edge >/dev/null` else `run the zivalx-ops edge setup first`; steps exactly as spec §6 (dump via `/opt/zivalx-ops/backup/zbackup dump navigator` if executable, else `umask 077` + `docker exec navigator-postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /var/backups/navigator/predeploy-<UTC ts>.dump`, keep newest 5; `git pull --ff-only`; `$COMPOSE build`; `$COMPOSE up -d`; wait ≤ 180 s for `docker inspect -f '{{.State.Health.Status}}' navigator-api` = `healthy`; print the `status` field of `/health/deep` via `docker exec navigator-api curl -sS http://localhost:7000/health/deep`; fail unless `docker port navigator-nginx` is empty).
- `.env.prod.example`: run exactly `sed -i '' -e '/^# Domain$/d' -e '/^DOMAIN=/d' -e '/^CERTBOT_EMAIL=/d' .env.prod.example` and confirm with `grep -c '^POSTGRES_DB=navigator$' .env.prod.example` → `1`. Do not open the file.

- [ ] **Step 1: Write the checks first and run them against the current tree to see them fail:**
  `docker compose -f docker-compose.prod.yml --env-file .env.prod.example config -q` (exit 0 both before and after, since `-q` prints nothing); `docker compose -f docker-compose.prod.yml --env-file .env.prod.example config --format json | python3 -c 'import json,sys; c=json.load(sys.stdin); s=c["services"]; assert c["name"]=="navigator"; assert all("ports" not in v for v in s.values()); assert "certbot" not in s; assert set(s["nginx"]["networks"])=={"navigator-net","edge"}; assert set(s["api"]["networks"])=={"navigator-net"}; assert s["api"]["container_name"]=="navigator-api"'` → FAIL now (the JSON contains interpolated example values: pipe it straight into the assert, never print it).

- [ ] **Step 2: Edit compose, nginx, scripts, docs** as specified.

- [ ] **Step 3: Verify.** The compose assert above passes; `docker run --rm -v "$PWD/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" -v "$PWD/nginx/conf.d/default.conf:/etc/nginx/conf.d/default.conf:ro" nginx:1.25-alpine nginx -t` → `syntax is ok`; `bash -n scripts/deploy.sh`; `shellcheck scripts/deploy.sh` if installed; `grep -rn "certbot\|htpasswd\|DOMAIN" docker-compose.prod.yml nginx scripts/deploy.sh` → no hits. The spec's on-server manual checks (empty `docker port`, trender export over `edge`, killing the leader worker) are the deployer's, not this task's.

- [ ] **Step 4: Commit.** `chore(deploy): join the edge network; TLS and basic auth move to the ops Caddy` (+ trailer).

---

### Task 13: `CLAUDE.md` corrections

**Files:**
- Modify: `CLAUDE.md:36-40, 92-106, 110-119, 124-129, 148-149`

**Interfaces:**
- Consumes: names from Tasks 1–12 (`python -m app.migrate`, `backend/alembic/versions`, locks `7400417`/`7400418`, `SCHEDULER_ENABLED`, `job_heartbeats`, `/health/deep`, `edge`, container names).

- [ ] **Step 1: Edit** per spec §7: indicators are wired end to end (`GET /api/markets/indicators?keys=` → `IndicatorsService`; breadth by the `breadth_snapshot` job into Redis); yield ×10 scaling *is* implemented (`indicators.py:41-43`, `"scale": 0.1`); replace every "no Alembic / create_all" passage with "Alembic owns the schema" (migrate command, where revisions go, readable ids, enum `ADD VALUE` needs `op.get_context().autocommit_block()`, lock `7400417`, local non-Docker dev runs `python -m app.migrate` after pulling); the job table from spec §2 plus leader lock `7400418`, `SCHEDULER_ENABLED`, heartbeats; Tests section names the `TEST_POSTGRES_URL` opt-in files and the throwaway-container command; Gotchas add `/health/deep`, the `edge` network, the container names, and that basic auth now lives at the edge.

- [ ] **Step 2: Verify.** `grep -n "create_all\|backfill_historical\|no Alembic\|isn't yet implemented" CLAUDE.md` → only the sentence saying SQLite tests still use `create_all`. Default suite still green.

- [ ] **Step 3: Commit.** `docs: CLAUDE.md reflects Alembic, the scheduler leader, health and edge` (+ trailer).
