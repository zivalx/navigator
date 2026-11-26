# Navigator platform basics: design (2026-11-22)

Branch `chore/platform-basics` (worktree `_wt/navigator-basics`, based on `feat/il-markets`, which is itself based on
`feat/tase-ils`).
Companion docs: trender `chore/platform-basics` and `zivalx-ops`. The shared contract (names
below in `code`) is identical across the three. Paths are relative to `backend/` unless they
start at the repo root.

## Why

- **Two schedulers in production.** `Dockerfile.prod:22-28` runs 2 gunicorn workers. Each
  worker's lifespan calls `start_scheduler()` (`app/main.py:24`), so every job runs twice. The
  results: duplicate Telegram messages (`notify_triggered_alerts` sends, then commits,
  `tasks/scheduler.py:361-389`), duplicate EOD rows (`scheduler.py:68-77`; no unique key in
  `models/price.py:8-24`), and doubled history on a fresh install (`backfill_historical_prices`,
  `scheduler.py:126-210`).
- **EOD is not idempotent and never catches up.** The EOD cron runs 7 days a week
  (`scheduler.py:401-411`). It writes one row per run and computes only today's portfolio row
  (`:93-97`). A missed day stays missing forever. The 6-month backfill runs only while there
  are fewer than 5 EOD rows in total (`:126-136`).
- **Alerts fire on stale prices.** When every provider fails, `get_quote` returns the last DB
  snapshot marked `stale` (`services/market_data.py:110-146`). Polygon's quote is the
  *previous day's* bar (`providers/polygon.py:22`). The evaluator checks `stale` only for the
  pre-cutover pause (`scheduler.py:295-304`). It then ratchets trailing-stop highs and fires
  on whatever came back (`:306-326`).
- **No migration tool.** The schema comes from `create_all`, plus hand-written enum and
  column DDL that runs on every boot (`database.py:31-164`). `alembic==1.14.0` is installed
  but has never been initialised (`requirements.txt:8`).
- **No liveness.** `/health` returns `{"status":"ok"}` without touching anything
  (`main.py:62-64`). Jobs swallow their errors and log them (`scheduler.py:98-99, 225-226,
  341-343`).
- **The stack can't share the VPS.** Navigator's nginx publishes 80/443 (`docker-compose.prod.yml:72-74`)
  and runs its own certbot (`:99-108`), and the container names depend on the checkout
  directory (no `container_name`, and the project name comes from the directory,
  `scripts/deploy.sh:34-36`).

## Goals

1. Exactly one APScheduler instance across all gunicorn workers. A new leader takes over
   within about 30 s when the old one dies.
2. EOD price rows are unique per `(asset, trade date)`. Re-runs are harmless, and missed
   session days are filled automatically.
3. Alerts never evaluate on, or ratchet with, a stale or fallback price.
4. Alembic owns the schema. A baseline equals today's schema, existing databases are
   adopted automatically and safely, and production never calls `create_all`.
5. A `job_heartbeats` table, a shallow `GET /health` and a `GET /health/deep` that follow the
   shared contract.
6. Navigator joins the external `edge` network with no published ports. TLS, certificates
   and basic auth move to the ops Caddy. Container and database names are stable.

## Non-goals

- FX history, a sales ledger or sell model, holdings alerts in trender, and retiring
  Navigator alerts. All of these wait.
- Changing what history reads. `_latest_snapshot` still reads every source
  (`services/portfolio.py:266-276`), and intraday quote snapshots are still written
  (`market_data.py:160-181`). Pruning them would change stored history values, so it belongs
  with the FX and history work.
- A full exchange holiday calendar. Holidays are handled through Yahoo's market time
  (see §3).
- Moving the synchronous ORM out of async jobs (`scheduler.py:34`).
- Funds and `other` assets have no live quote path (`market_data.py:75`); this round doesn't
  fix that.

## Anti-goals

- No DDL at app boot in production. No `create_all` against Postgres outside Alembic.
- No new single-worker assumption. The design must stay correct with N workers or 2
  containers.
- `/health/deep` never shows quantities, costs, symbols, error text or secrets.
- Navigator has no auth of its own behind the edge. Don't add a second login.

## Approaches considered

**Single scheduler**
- (a) `--workers 1`: simplest, but it couples API capacity to the scheduler, and a later
  scale-up silently reintroduces the bug.
- (b) A separate `scheduler` compose service: one more container and image path. It still
  needs a guard against a second copy.
- (c) `gunicorn --preload` with the scheduler in the master: asyncio and APScheduler threads
  don't survive the fork. Rejected.
- (d) Redis lock with a TTL: needs renewals, and Redis is a cache here.
- **Picked (approved): a Postgres session advisory lock `7400418`.** It is held on a
  dedicated connection, and Postgres releases it the moment that session dies. It needs no
  extra container.

**EOD uniqueness**
- A unique index on an expression over `timestamp`: it is timezone-fragile and differs
  between SQLite and Postgres.
- A unique key on `(asset_id, source, timestamp)`: it doesn't stop two runs a minute apart.
- **Picked: a nullable `trade_date` column.** It is set only on EOD rows, with a partial
  unique index on `(asset_id, trade_date)`.

**Adopting Alembic for existing databases**
- Hand-stamp each database: error-prone, and a database missing an old `init_db` step would
  be stamped wrong.
- A self-detecting baseline that sometimes creates and sometimes converges: unusual, and it
  hides what happened.
- **Picked:** `python -m app.migrate`. A legacy database is detected, converged with the
  frozen `init_db` DDL, checked, stamped `0001`, and then upgraded. A fresh database is simply
  upgraded.

**Front door**
- Keep nginx with TLS and certbot: this conflicts with trender on 80/443. Rejected by the
  approved edge pick.
- Basic auth in both the edge and nginx: two passwords to rotate, and trender's internal
  export call would need credentials.
- **Picked: the edge Caddy does TLS and basic auth.** Navigator's nginx serves the SPA and
  proxies to the API on `edge`, with no auth.

## How (decided)

### Advisory-lock key registry
Ruling (lead, 2026-11-22): one table, identical in the Navigator, trender and ops docs. Advisory
locks are per database, but the keys stay unique across the platform. All keys fit in int4, so
`pg_locks` shows `classid=0`, `objsubid=1`.

| Key | App | Use |
|---|---|---|
| `7400417` | Navigator | init / migrate lock (`python -m app.migrate`, `alembic/env.py`) |
| `7400418` | Navigator | scheduler leader |
| `7400419` | trender | scheduler leader |
| `7400420` | trender | migrate (`alembic/env.py`) |

### 1. Scheduler leader (`app/tasks/leader.py`, new)

- **`SchedulerLeader`** is created in the lifespan in place of `start_scheduler()` (`main.py:24`).
  - It runs an asyncio task with a loop period of `SCHEDULER_LEADER_INTERVAL_SECONDS=30`.
  - Blocking DB calls go through `asyncio.to_thread`.
- **Lock connection.** Each process has its own engine: `create_engine(database_url,
  poolclass=NullPool, connect_args={"keepalives":1,"keepalives_idle":30,
  "keepalives_interval":10,"keepalives_count":3})`.
  - The single connection is `AUTOCOMMIT`, so it is never idle in a transaction.
  - It is never shared with jobs or requests.
- **Follower step.** Call `SELECT pg_try_advisory_lock(7400418)`.
  - `true`: become leader. Build the scheduler (§2), start it, and upsert heartbeat
    `scheduler_leader` (`last_started_at = now`).
  - `false`: stay follower. A broken connection is reconnected on the next tick.
- **Leader step.**
  - Run `SELECT 1` on the lock connection, then upsert `scheduler_leader.last_success_at = now`
    using a normal session.
  - If the ping fails, the session (and so the lock) is gone. Immediately call
    `scheduler.shutdown(wait=False)`, set `is_leader=False`, drop the connection and go back to
    follower.
- **Job guard.** Every job wrapper (§5) returns early if `leader.is_leader` is false. This
  bounds the split-brain window to one loop period.
- **Shutdown** (lifespan exit): shut down the scheduler, then `pg_advisory_unlock(7400418)`,
  then close the connection.
- **Failover.**
  - When gunicorn kills or restarts a worker, its socket closes and Postgres frees the lock at
    once. The other worker, or the replacement, acquires it within 30 s.
  - When Postgres restarts, every process becomes a follower and one wins the race.
  - The startup jobs run again on every new leadership. They are idempotent (§3).
- **Non-leaders** run no scheduler and no jobs. They serve HTTP, including `/health/deep`,
  which reads leadership from `pg_locks` (§5).
- **SQLite** (tests, ad hoc dev) has no advisory locks, so the process is always leader.
- **`SCHEDULER_ENABLED`** (new, default `true`): `false` skips the leader loop entirely. Use it
  for scripts and for a laptop pointed at a shared database.
- **Don't set `idle_session_timeout`** on the server. The 30 s ping keeps the lock session
  alive regardless.
- **Notification de-duplication** (defence for the split-brain window).
  - `notify_triggered_alerts` (`scheduler.py:348-393`) selects pending ids first.
  - For each id it re-selects with `.with_for_update(skip_locked=True)` and filters on
    `notified_at IS NULL AND notification_skipped_at IS NULL`.
  - It then sends, stamps and commits, one alert at a time, and keeps the retry-next-cycle
    behaviour. (SQLite ignores `FOR UPDATE`.)

### 2. Scheduler configuration (`tasks/scheduler.py`)

- Split `start_scheduler()` into **`build_scheduler() -> AsyncIOScheduler`** (testable without
  starting) and the leader's start call.
- `AsyncIOScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
  "misfire_grace_time": 120})`.
- Cron jobs use `America/New_York` (same as today's `US/Eastern` alias).
- When the leader acquires the lock at time T, a single date job **`leader_startup`** runs at
  T+15 s. It runs, in order:
  1. `eod_catchup`
  2. `backfill_daily_pnl` (only if `portfolio_snapshots` is empty)
  3. `breadth_warm_start` (Redis-only)

  This replaces the three staggered startup jobs (`scheduler.py:426-457`) and removes the
  ordering-by-delay.

| Job id (= heartbeat `job_name`) | Trigger | misfire_grace_time | Overdue after | Critical |
|---|---|---|---|---|
| `scheduler_leader` (leader loop, not an APScheduler job) | every 30 s | – | 3 min | yes |
| `quote_refresh` (new) | interval 5 min | 120 s | 15 min | yes |
| `price_alert_evaluation` | interval 5 min | 120 s | 15 min | yes |
| `eod_snapshot` | cron 16:30 America/New_York, daily | 3600 s | 26 h | yes |
| `breadth_snapshot` | cron 17:00 America/New_York, daily | 3600 s | 26 h | no |
| `eod_catchup` | cron 17:15 America/New_York, daily, plus `leader_startup` | 3600 s | 26 h | yes |
| `leader_startup` | date T+15 s | 300 s | not checked | – |

- **`quote_refresh`** runs every 5 minutes.
  - It calls `get_quotes()` for the **watched set**: held assets plus assets with an active
    alert.
  - It restricts that set to assets whose exchange is in session (§3); crypto is always in
    session.
  - It writes nothing itself. The quote path writes the snapshot it already writes
    (`market_data.py:106`), and that is the freshness signal (§5).
- **EOD runs daily,** not Monday–Friday. Crypto trades on weekends, and `trade_date` (§3)
  makes weekend runs no-ops for exchange-traded assets.

### 3. Sessions, staleness, EOD idempotency and catch-up

**Session table** (`app/services/sessions.py`, new). Each asset is classified as:
- `crypto` if `asset_type == CRYPTO`
- `TASE` if `market_region == IL` or the symbol ends in `.TA`
- `US` if `market_region == US`
- `other` otherwise

| Exchange | TZ | Session (local) |
|---|---|---|
| US | America/New_York | Mon–Fri 09:30–16:00 |
| TASE | Asia/Jerusalem | Mon–Thu 09:59–17:25; Fri 09:59–14:00 |
| crypto | UTC | always |
| other | – | no session check |

- **TASE week.** TASE moved to a Monday–Friday week on 5 January 2026: there is no Sunday
  session, and Friday trading runs 10:00–14:00. The trender doc (§4, calendar) states the same
  assumption; keep the two identical. This is confirmed by press and custodian notices
  (Times of Israel, Globes, Clearstream). The Monday–Thursday close and Friday close above are
  **unverified against TASE's own published schedule**. The builder must check tase.co.il and
  adjust the table constants. Keep them as data, not logic.
- **Holidays** are deliberately not modelled. The two consumers below are built to be
  holiday-safe without a calendar.

**Quote fields.** The Yahoo provider adds two fields to its quote:
- `marketTime`: aware UTC ISO, from `meta.regularMarketTime`
- `exchangeTimezone`: from `meta.exchangeTimezoneName`

All provider `timestamp` values become aware UTC:
- `datetime.now(timezone.utc)` for Yahoo and CoinGecko (`yahoo.py:73`, `coingecko.py:65,105`)
- `fromtimestamp(t, tz=timezone.utc)` for Polygon and Finnhub (`polygon.py:43`, `finnhub.py:41`)

That fixes the naive-timestamp skew. The cache key stays `quote:v2:`: an old cached entry has
no `marketTime`, so it is treated as not alertable for at most 60 s.

**Alertable price** (`services/sessions.py: is_alertable(quote, asset, now) -> (bool, reason)`).
A quote is alertable only if all of the following hold:
1. `stale` is not set and `source != "db_cache"`.
2. `source` is an **alertable source** for the asset type. A Polygon previous-day bar or a
   Finnhub USD fallback is never alertable.
   Ruling (lead, 2026-11-22):
   - **stock / etf / index:** Yahoo is primary and alertable. `AssetType.INDEX` already exists,
     because this branch is now based on `feat/il-markets`.
   - **crypto:** CoinGecko is primary. Yahoo crypto quotes that carry `marketTime` also count as
     alertable.
   - **fund:** has no live source, so it is reported as `not_monitored` and price alerts on
     funds are skipped (not evaluated).
3. For US and TASE: `marketTime` is present and `>= open of the most recent session that has
   started` (per the table).
   - For `other`: `marketTime` is at most 96 h old.
   - For crypto: no time check.

What this means for holidays and thin names:
- A holiday leaves the most recent "session" open in the future of the last trade, so alerts
  pause until the next real trade. Prices don't move on a holiday, so nothing is lost.
- A thin TASE name that hasn't traded today pauses the same way.

`evaluate_price_alerts` applies the check before the pause logic (`scheduler.py:291-304`):
- A non-alertable quote is skipped. There is no trigger and no `high_water_mark` ratchet.
- Each run logs one WARNING with counts per reason.
- The existing stale/unit pause stays as it is for alertable quotes.

**EOD rows** (`models/price.py`).
- Add `trade_date = Column(Date, nullable=True)`.
- Add the index `Index("uq_price_snapshots_eod", "asset_id", "trade_date", unique=True,
  postgresql_where=text("trade_date IS NOT NULL"), sqlite_where=text("trade_date IS NOT NULL"))`.
- Only `source in ("eod_snapshot", "eod_backfill")` rows carry a `trade_date`. Quote snapshots
  stay `NULL` and keep being written on every provider fetch (confirmed:
  `market_data.py:100-106` → `_store_price_snapshot`, `:160-181`).
- `trade_date` is the local date of the last trade in the exchange's timezone:
  - Yahoo: `marketTime` converted to `exchangeTimezone`
  - bars: the bar timestamp in `meta.exchangeTimezoneName`
  - crypto: the UTC date
- One helper, `upsert_eod(db, asset, price, currency, ts, trade_date, source, overwrite)`,
  builds a dialect `insert(...).on_conflict_do_update/do_nothing(index_elements=["asset_id",
  "trade_date"], index_where=trade_date IS NOT NULL)`, for both Postgres and SQLite.

**`eod_snapshot`** (`scheduler.py:32-101`):
- Write only alertable-source quotes (primary and not stale). Anything else is left to the
  catch-up.
- `ts = marketTime` (crypto: now) and `overwrite=True`, so a same-day re-run refreshes the
  close.
- A Saturday run for a US asset produces `trade_date` = Friday and rewrites Friday's
  identical row, so there are no weekend rows.
- Then it calls `compute_daily_snapshot(today_utc)` as now (`:93-97`).

**`eod_catchup`** replaces `backfill_historical_prices` (`scheduler.py:119-228`) and drops the
"< 5 rows" gate. For each held asset:
- Set `start = max(trade_date) − 5 days`. If there is no EOD row, use `now − 183 days` (today's
  6mo). Cap at 400 days.
- Fetch the Yahoo chart with `interval=1d&period1=&period2=` (not `range`). The crypto symbol
  mapping stays as today (`:162-164`), as does the unit normalisation (`:187-199`).
- Skip `close=None`. Skip the bar for the exchange-local today unless `now` is past that day's
  session end (crypto: skip the current UTC day).
- Write `source="eod_backfill"`, `overwrite=False` (`DO NOTHING`). Existing rows are never
  changed. Ruling (lead, 2026-11-22): `ts` = the exchange session close, in UTC, for that
  `trade_date` (not the bar timestamp), so these rows beat same-day intraday quote rows in
  `_latest_snapshot`.
- **Portfolio catch-up.** For every UTC date D in the last 30 days before today where some held
  asset has an EOD row on D (Ruling (lead, 2026-11-22): matched on `trade_date`) and `portfolio_snapshots` has no row for D, call
  `compute_daily_snapshot(D)`, oldest first. Existing portfolio rows are never recomputed
  (history stability).
- Per-asset failures are counted. The job raises at the end if any asset failed, so the
  heartbeat records an error, after it has written everything it could.

### 4. Alembic

**Files**
- `backend/alembic.ini` with `script_location = %(here)s/alembic`.
- `backend/alembic/env.py`:
  - `target_metadata = Base.metadata` after `import app.models`.
  - The URL comes from `settings.database_url`.
  - `compare_type=True`.
- `backend/alembic/versions/`. Use readable revision ids, not random hex, because trender's
  B2 collision came from hex ids:
  - `0001_baseline` (revision `0001`, down `None`)
  - `0002_eod_trade_date_and_heartbeats` (revision `0002`, down `0001`)

**Baseline `0001` = today's schema.**
- **Base branch:** this branch is already based on `feat/il-markets`, so `AssetType.INDEX` and
  the `assettype` label `INDEX` (`database.py`, `models/asset.py`) exist; no rebase is needed
  (Ruling (lead, 2026-11-22)). The baseline matches what ships.
- **Generating it:** produce it by `alembic revision --autogenerate` against an empty Postgres,
  then hand-check it against `pg_dump --schema-only` of a database built by the *current*
  `init_db`.
- **Tables (11):** `assets`, `holding_lots`, `cash_balances`, `watchlists`, `watchlist_items`,
  `price_snapshots`, `fx_rates`, `news_items`, `price_alerts`, `portfolio_snapshots`,
  `maintenance_runs`.
- **Columns:** with `price_alerts.threshold` nullable and the trailing-stop columns present
  (`database.py:127-150`).
- **Enum types:** SQLAlchemy stores member **names** as labels.
  - `currency`: 18 labels in model order
  - `assettype`: STOCK, ETF, CRYPTO, FUND, INDEX, OTHER
  - `marketregion`: US, EU, ASIA, IL
  - `alertrule`: PRICE_BELOW, PRICE_ABOVE, TRAILING_STOP
  - `alertintent`: BUY, SELL
- **Indexes:** exactly what `create_all` makes. That includes both `ix_news_items_published_at`
  and `ix_news_published_at`, and the unique `ix_portfolio_snapshots_date`.
- **Data step:** `INSERT INTO maintenance_runs (name, ran_at) VALUES ('minor_units_cutover',
  now()) ON CONFLICT (name) DO NOTHING`. This replaces `ensure_cutover_marker` at boot for
  fresh databases.
- **`downgrade()`** raises.

**Revision `0002`**
1. `ALTER TABLE price_snapshots ADD COLUMN trade_date date`.
2. Set `trade_date` on existing EOD rows:
   - `eod_snapshot`: `(timestamp AT TIME ZONE 'America/New_York')::date`. The rows were taken
     at 16:30 ET.
   - `eod_backfill`: `(timestamp AT TIME ZONE 'UTC')::date`. Bar timestamps fall on the session
     date for US, TASE and crypto.
   - Ruling (lead, 2026-11-22): existing weekend `eod_snapshot` rows are remapped to the
     previous session's `trade_date` for their exchange, then de-duplicated (step 3), keeping
     the latest. Their values equal Friday's close, so history doesn't change.
3. De-duplicate, keeping the latest row per key. This is the row `_latest_snapshot` already
  picks, so **no history value changes**:
   ```sql
   DELETE FROM price_snapshots p USING (
     SELECT id, row_number() OVER (PARTITION BY asset_id, trade_date
                                   ORDER BY timestamp DESC, id DESC) AS rn
     FROM price_snapshots WHERE trade_date IS NOT NULL) d
   WHERE p.id = d.id AND d.rn > 1;
   ```
   Log only the deleted row count.
4. `CREATE UNIQUE INDEX uq_price_snapshots_eod ON price_snapshots (asset_id, trade_date)
   WHERE trade_date IS NOT NULL`.
5. Create the table `job_heartbeats` (§5).

`downgrade()` drops the table, the index and the column. The de-duplicated rows are not
restored; the pre-deploy dump covers that.

**`app/migrate.py`** (new; `python -m app.migrate`). All of it runs under advisory lock
`7400417`, using today's `_pg_init_lock` (`database.py:70-78`); that key becomes the
*migration* lock.
- **Neither `alembic_version` nor `assets` exists** → `command.upgrade(cfg, "head")`.
- **`assets` exists but `alembic_version` doesn't** (a legacy database) → adopt it:
  1. Run the frozen legacy converge. Move `_apply_enum_values`, `_migrate_price_alerts` and
     `ensure_cutover_marker` unchanged into `app/legacy_schema.py`, plus the `INDEX` add.
  2. `Base.metadata.create_all(tables=[the baseline tables except price_snapshots])`. This
     creates any baseline table an old database never got, such as `maintenance_runs` or
     `portfolio_snapshots`.
  3. Verify that the 11 tables, the enum labels and the `price_alerts` columns above are
     present. On failure, exit 1 without stamping.
  4. `command.stamp(cfg, "0001")`, then `command.upgrade(cfg, "head")`.
- **`alembic_version` exists** → `command.upgrade(cfg, "head")`.
- Print the revision before and after, and nothing else.
- The adopt path is valid only while those baseline tables' models are unchanged (a test
  enforces it). Ruling (lead, 2026-11-22): production has no legacy database (nothing is
  deployed; it starts fresh at head), so the adopt branch serves only the local and dev
  databases. Delete `legacy_schema.py` and the adopt branch one release after those are on
  `0002`.
- `env.py` takes `pg_advisory_lock(7400417)` itself **unless** `config.attributes["lock_held"]`
  is set by `migrate.py`. A bare `alembic upgrade head` is therefore serialised too, without
  self-deadlock.
- **Future enum `ADD VALUE`** revisions must use `op.get_context().autocommit_block()`.

**Entrypoint and boot**
- New `backend/docker-entrypoint.sh`: `set -e; python -m app.migrate; exec "$@"`.
- `Dockerfile.prod` and `Dockerfile` get `ENTRYPOINT ["/app/docker-entrypoint.sh"]`, and the
  `CMD` stays the same, with **2 workers kept**.
- Migrations run once per container start, in one process, *before* gunicorn forks. The lock
  only covers concurrent `docker compose run`s or a second container.
- `docker compose exec` doesn't run the entrypoint, so the audit script is unaffected.
- A failed migration exits the container. `restart: unless-stopped` then loops, and the
  watchdog sees it as down.
- **`init_db()`** (`database.py:31-46`):
  - SQLite: `create_all` + `ensure_cutover_marker` (tests and ad hoc dev only).
  - Postgres: **no DDL.** Read `alembic_version` and compare it with
    `ScriptDirectory.get_current_head()`. If they differ, raise `RuntimeError("DB at <rev>,
    code expects <head>; run python -m app.migrate")`.
- **Dev compose** (`backend/docker-compose.yml`). Ruling (lead, 2026-11-22): it keeps
  `uvicorn --reload` and runs `python -m app.migrate` before it. Leader-lock churn on reload is
  acceptable. `SCHEDULER_ENABLED` (default true, §1) switches the scheduler off.
- **Local non-Docker dev** runs `python -m app.migrate` once after pulling. The local Docker
  database goes through the entrypoint.

### 5. Heartbeats and health

**Table** (`models/job_heartbeat.py`, created by `0002`):
```sql
CREATE TABLE job_heartbeats (
  job_name        text PRIMARY KEY,
  last_started_at timestamptz,
  last_success_at timestamptz,
  last_error_at   timestamptz,
  last_error      text          -- redact_secrets(str(e))[:500]; never quantities
);
```

**Recording** (`app/tasks/heartbeat.py`):
- `@heartbeat("job_name")` wraps each job in the table (§2).
- It checks `leader.is_leader`, upserts `last_started_at`, runs the job, and on return upserts
  `last_success_at`.
- On an exception it upserts `last_error_at` and `last_error` and logs the error.
- Every write is an `INSERT … ON CONFLICT (job_name) DO UPDATE` in its **own** session and
  commit, so a job rollback can't erase a heartbeat.
- **Jobs must raise to fail.** Today they catch and log (`scheduler.py:98-99, 113-114,
  225-226, 341-343`, `breadth.py:199`). Keep the per-item `try/except`, but re-raise or raise
  a summary error at job level:
  - `eod_snapshot`: if any asset had an error, or the daily portfolio row raised. An asset
    skipped because it isn't alertable is not an error.
  - `eod_catchup`: as described in §3.
  - `price_alert_evaluation`: if the quote fetch raised (`:278-281`). Individual skips are not
    errors, except under the price-job failure rule below.
  - Ruling (lead, 2026-11-22), price-job failure rule: `quote_refresh` and
    `price_alert_evaluation` record a job FAILURE in `job_heartbeats` (and raise) when more than
    50% of the watched symbols come back as errors or non-alertable, or when zero symbols
    succeed. Otherwise they record success with counts in the detail. `get_quotes` swallows
    per-symbol errors (`market_data.py:150-158`), which is why this rule is needed.
  - `breadth_snapshot`: if no data was downloaded.

**`GET /health`** (shallow; no DB): `200 {"status":"ok","checks":{"process":{"ok":true,"detail":"up"}}}`.
The compose `api` healthcheck keeps using it.

**`GET /health/deep`** (root path, not `/api`):
- Checks run in a threadpool with a 2 s `statement_timeout`.
- Status (Ruling (lead, 2026-11-22), all apps and the watchdog):
  - `down` if any critical check fails; HTTP **503**.
  - `degraded` if only non-critical checks fail; HTTP **200**.
  - `ok` otherwise (HTTP 200).
  - The watchdog parses the JSON `status`. Paging policy: page immediately on `down` or an
    unreachable endpoint; page if `degraded` persists 30 minutes or more; send a recovery
    message when it returns to `ok`.
- `detail` holds counts and ages only.

| Check | Critical | ok when |
|---|---|---|
| `db` | yes | `SELECT 1` succeeds. If it fails, every later check is `ok:false, "skipped: db unreachable"`. |
| `migrations` | yes | `alembic_version` equals the code's head. |
| `scheduler_leader` | yes | `pg_locks` has a granted advisory lock with `objid=7400418` in the current database, **and** the `scheduler_leader.last_success_at` heartbeat is ≤ 3 min old. |
| `job_quote_refresh`, `job_price_alert_evaluation`, `job_eod_snapshot`, `job_eod_catchup` | yes | Now − reference ≤ the overdue threshold (§2). |
| `job_breadth_snapshot` | no | Same rule. |
| `alert_delivery` | yes | No `price_alerts` row has `triggered_at` > 15 min old with both `notified_at` and `notification_skipped_at` null. |
| `prices_us`, `prices_tase`, `prices_crypto` | yes | See freshness below. |

- **Reference time** for the job checks is `last_success_at`. If that is null, use
  `scheduler_leader.last_started_at`. This gives a fresh install grace without letting
  restarts reset the clock once a success exists.
- **Freshness.** For the watched set (held plus active-alert assets) on that exchange:
  - Applies only while the exchange is in session per §3, and from 15 min after open (crypto:
    always).
  - Each asset needs a `price_snapshots` row with `source IN ('yahoo','coingecko')` and
    `timestamp` ≥ now − 30 min (Ruling, lead 2026-11-22: same 30-min threshold as trender).
  - This measures *our ability to fetch*, not market activity, so holidays and thin names
    don't false-page.
  - Outside session: `ok:true, detail "closed"`.
  - The detail reads like `"2 of 11 watched assets without a fresh price for 20+ min"`.
  - Ruling (lead, 2026-11-22): `prices_crypto` is `degraded` (not `down`) when CoinGecko fails
    but Yahoo crypto works. It is `down` only when both fail.
  - Ruling (lead, 2026-11-22): held assets with no live quote source (for example funds, and
    `other`) are reported as `not_monitored` in the check detail, for example
    `"2 of 11 watched assets without a fresh price for 20+ min; 3 not_monitored"`. They never
    make a check fail, so they never page.

Example body:
```json
{"status":"down","checks":{"db":{"ok":true,"detail":"reachable"},
 "scheduler_leader":{"ok":true,"detail":"leader heartbeat 21s ago"},
 "job_eod_catchup":{"ok":false,"detail":"last success 27h ago (limit 26h); last error 3m ago"},
 "prices_tase":{"ok":true,"detail":"closed"}}}
```

### 6. Compose, nginx and scripts (edge network)

**`docker-compose.prod.yml`**
- **Project name.** Add a top-level `name: navigator`. This equals today's default for
  `/opt/navigator`, so the volumes stay `navigator_pg_data` and so on.
- **Fixed `container_name`s:**
  - `navigator-postgres`
  - `navigator-redis`
  - `navigator-api`
  - `navigator-frontend` (one-shot)
  - `navigator-nginx`
- **`postgres`:**
  - Unchanged, except that `POSTGRES_DB` **must be `navigator`**. `deploy.sh` checks it.
  - On `navigator-net` only.
- **`api`:** on `navigator-net` only. Ruling (lead, 2026-11-22): only `navigator-nginx` joins
  `edge`; the API is never on it. Add `stop_grace_period: 30s`.
- **`nginx`:**
  - **Remove** `ports` (`:72-74`), the `.htpasswd` mount (`:85-88`), the certbot mounts
    (`:90-91`), `DOMAIN` and the `envsubst` command (`:80-81, 92-94`).
  - Mount a plain `./nginx/conf.d/default.conf`.
  - Networks: `navigator-net` and `edge`.
- **Delete** the `certbot` service (`:99-108`) and the `certbot_conf` and `certbot_www`
  volumes (`:118-119`). Existing volumes on a host are left for the ops runbook to remove.
- **Networks:** `edge: {external: true, name: edge}`. Ops creates the network; Navigator
  never does.

**`nginx/conf.d/default.conf`** replaces `default.conf.template`. It has one `listen 80;
server_name _;` block.
- **Remove** the port-80 redirect and the ACME block (`template:1-13`), the TLS block
  (`:17-30`), HSTS (`:37`, the edge owns it) and `auth_basic` (`:43-44`). Keep the other
  security headers.
- **Real client IP** for `limit_req` (`nginx.conf:36`): `set_real_ip_from 172.16.0.0/12;
  set_real_ip_from 10.0.0.0/8; set_real_ip_from 192.168.0.0/16; real_ip_header
  X-Forwarded-For; real_ip_recursive on;`.
- Pass `X-Forwarded-Proto $http_x_forwarded_proto`.
- `location = /health` and `location = /health/deep` go to `http://api:7000`, with no rate
  limit. `/api/` and the SPA are unchanged.
- **Upstream resolution** (ops requirement): `resolver 127.0.0.11 valid=10s;` and a variable
  `proxy_pass` (`set $api_upstream http://api:7000;`), so a recreated `api` container doesn't
  leave nginx with a stale IP.
- **Auth: the edge does it.** Navigator keeps no `.htpasswd`.
  - **Trust boundary:** every container on `edge` can reach nginx, and so `/api/`,
    unauthenticated. Ops must attach only the edge Caddy and trender's app.
  - `X-Export-Token` (`routers/export.py`) still guards the export.

**What ops and trender use**

| Use | Target |
|---|---|
| Edge Caddy upstream for `navigator.zivalx.com` (`scripts/provision.sh:76`) | `navigator-nginx:80` |
| trender export | `http://navigator-nginx/api/export/positions` with `X-Export-Token` (= trender's `NAVIGATOR_EXPORT_TOKEN`). Ruling (lead, 2026-11-22): internal route through nginx, never through the edge Caddy, so no basic auth. |
| Watchdog (ops) | `127.0.0.1:2080/navigator/health/deep`, the edge's loopback listener, which proxies to `http://navigator-nginx/health/deep` |
| Watchdog, from the host | `docker exec navigator-api curl -fsS http://localhost:7000/health/deep` (`curl` is installed, `Dockerfile.prod:8`) |
| Backup | `docker exec navigator-postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'`. The DB name is `navigator`; the user comes from the container env, so no secret is passed on the host. |

**`scripts/deploy.sh`** (rewrite)
- **Preflight:**
  - `.env.prod` exists.
  - `grep -qx 'POSTGRES_DB=navigator' .env.prod`.
  - `docker network inspect edge` succeeds; otherwise say "run the zivalx-ops edge setup
    first".
- **Drop:** the `.htpasswd`, `DOMAIN`, `CERTBOT_EMAIL` and certificate logic (`:21`
  `.htpasswd` preflight, `:23-27` `DOMAIN` and `CERTBOT_EMAIL`, `:34-77` `CONF_VOL`,
  `cert_exists` and the bootstrap branches) and the public curl (`:82`).
- **Steps:**
  1. If `navigator-postgres` is running, dump it: `/opt/zivalx-ops/backup/zbackup dump navigator`
     when it exists (ops doc), else `pg_dump` to
     `/var/backups/navigator/predeploy-<UTC ts>.dump` (mode 600, keep the newest 5).
  2. `git pull --ff-only`.
  3. `$COMPOSE build`.
  4. `$COMPOSE up -d`; the entrypoint migrates.
  5. Wait up to 180 s for `navigator-api` to report healthy.
  6. Print the `status` of `/health/deep` via `docker exec`.
  7. Assert `docker port navigator-nginx` is empty.
- **Delete** `scripts/init-letsencrypt.sh`.
- `.env.prod.example`: remove `DOMAIN` and `CERTBOT_EMAIL`, set `POSTGRES_DB=navigator`.
- Add a one-line note to `provision.sh` and `cloud-init.yml`: host provisioning and the 80/443
  firewall now belong to zivalx-ops.
- In `TODO-DEPLOYMENT.md`, rewrite must-do #3 and the SSL/basic-auth steps to point at the
  ops runbook.

### 7. `CLAUDE.md` corrections (same branch)

- **Lines 97-106**, the stale indicators note: replace it with "Market indicators are wired
  end to end".
  - `GET /api/markets/indicators?keys=` → `IndicatorsService` → providers (`routers/markets.py:64`,
    `services/indicators.py`).
  - Breadth (S5FI/S5TH) is computed by the `breadth_snapshot` job into Redis.
- **Lines 116-119:** the yield ×10 scaling *is* implemented (`indicators.py:41-43`,
  `"scale": 0.1`).
- **Lines 38-40, 124-129 and 148-149:** replace the "no Alembic / create_all" text with
  "Alembic owns the schema".
  - Covered: `python -m app.migrate`, new revisions under `backend/alembic/versions`, the enum
    `autocommit_block` rule, and the migration lock `7400417`.
- **Lines 92-96:** the scheduler job list, plus the leader lock `7400418`, `SCHEDULER_ENABLED`
  and the heartbeats.
- **Gotchas:** add `/health/deep`, the `edge` network and the container names, and note that
  the old basic auth now lives at the edge.

## Migration / rollout for existing databases

1. **Merge order:**
   - Navigator: PR #2 export → PR #3 tase → `feat/il-markets` → **this branch** →
     export-watch-targets → historical-fx.
   - historical-fx must then:
     - add `fx_daily_rates` as revision `0003`, never via `create_all`;
     - register `refresh_fx_history` through `build_scheduler()` with a heartbeat and
       `ON CONFLICT`;
     - run its rebuild as a CLI only.
2. **Prod.** Decision (user, 2026-11-22): nothing is deployed yet. No production Navigator
   database exists and there is no real Navigator data (the user enters holdings fresh after
   deploy). Production therefore starts fresh at head: `app.migrate` sees neither `assets` nor
   `alembic_version` and runs `0001` -> `0002`, which writes the `minor_units_cutover` marker
   (item 4). The legacy adopt path (§4) is **not** needed in prod; it stays only for the
   local and dev databases (the Mac test data), see item 3.
   - Ops first creates `edge` and starts the Caddy (ops doc; the first deploy is greenfield).
   - Check that the checkout is `/opt/navigator`, so `name: navigator` names the volumes, and
     that `POSTGRES_DB` is `navigator` (`deploy.sh` enforces it).
   - Then run `deploy.sh` (nothing to dump on the first run; the entrypoint migrates; the stack
     starts with 2 workers and 1 leader).
   - Then the audit dry run and `--apply` (`CLAUDE.md` deploy order), then trender. Expected
     result on a fresh database, checked against `scripts/audit_asset_currencies.py` and
     `database.py`: the cutover marker exists (written by `0001`), there are no assets, so
     Yahoo is never called, and the dry run prints "0 asset(s) to fix, 0 alert(s) and 0 watch
     target(s) to convert". `--apply` exits 0, is a no-op on data, and still writes the
     `minor_unit_alerts_converted` marker. Only if the cutover marker were missing would the
     audit exit 1, which a fresh `init` rules out. If it did exit 1, stop and check the
     `maintenance_runs` table.
3. **Local and dev databases** (the Mac test data; legacy, converged by the adopt path):
   (Docker) `docker compose up --build` adopts it on first start. A local
   Postgres outside Docker: `python -m app.migrate`.
4. **Fresh install:** `0001` → `0002`. Then `leader_startup` → `eod_catchup` backfills 183 days
   once, and the daily P&L backfill runs.
5. **Rollback:**
   - The previous image *can* run against `0002`. Its `init_db` is a no-op on the existing
     tables, and it ignores the extra column, index and table. Its EOD inserts leave
     `trade_date` NULL.
   - Rolling back the image re-introduces the two schedulers, so do it only briefly.
   - Restore the pre-deploy dump only if the de-duplication itself must be undone.

## Testing

Default suite (SQLite, `pytest -q`):
- **`is_alertable`:**
  - Rejected: `stale`, `db_cache`, `polygon`, `finnhub`, Yahoo without `marketTime`.
  - US: Monday 10:00 ET with Friday's 16:00 close → ok. After a Friday holiday (Thursday
    `marketTime`) → rejected.
  - TASE: Friday 11:00 IL with a Thursday `marketTime` → rejected. Sunday 11:00 IL with
    Friday's close → ok (no Sunday session).
  - Crypto via CoinGecko → ok.
- **Session table:** DST boundaries; TASE Friday 13:59 in, 14:01 out; Sunday out.
- **`evaluate_price_alerts` with a stale quote:**
  - no trigger
  - `high_water_mark` unchanged
  - the pre-cutover pause is still honoured for alertable quotes.
- **EOD:**
  - Two runs on the same day → 1 row, refreshed price.
  - A Saturday run for a US asset → no Saturday row.
  - A TASE `trade_date` comes from `marketTime` in Asia/Jerusalem.
  - A non-alertable quote is skipped, not written.
- **`eod_catchup`:**
  - A 3-day gap is filled.
  - An existing row isn't overwritten (`DO NOTHING`).
  - Today's in-progress bar is skipped.
  - A portfolio row is computed only for the missing D.
  - A per-asset failure leaves the others written and raises at the end.
- **Heartbeat:**
  - A success stamps `last_success_at`.
  - An exception text with an API key is stored redacted and ≤ 500 chars.
  - A job rollback keeps the heartbeat.
  - A non-leader returns without running.
- **`build_scheduler()`:**
  - Every job has `coalesce=True`, `max_instances=1` and the misfire value from the table.
  - The job ids equal the heartbeat names.
- **`/health/deep`:**
  - Each check's ok/fail.
  - The status mapping and 503-only-on-down.
  - "closed" outside the session.
  - A never-run job uses the leader start as its reference.
  - The response body contains no quantity, cost or symbol from the fixture.
- **`/health`** needs no DB.
- **Leader with a mocked lock:**
  - A follower never builds a scheduler.
  - A ping failure shuts the scheduler down.

Postgres, opt-in (`TEST_POSTGRES_URL`), using a schema per test as in `tests/test_enum_migration.py`:
- **`test_pg_upgrade_empty_matches_models`:**
  - `migrate` on an empty schema.
  - `compare_metadata(Base.metadata)` returns no diffs.
  - Explicit enum label lists match §4.
  - The cutover marker exists exactly once.
- **`test_pg_adopt_legacy`:** two fixtures, both captured by the builder *before* changing
  `init_db`:
  - `tests/fixtures/legacy_pre_tase.sql`: 5 currencies, no IL or INDEX, no trailing columns,
    no `maintenance_runs` or `portfolio_snapshots`.
  - `tests/fixtures/legacy_tase.sql`: the current `init_db` output.

  For each: `migrate` → at head, no diffs, the marker kept if it was present (the timestamp is
  unchanged).
- **`test_pg_adopt_refuses_partial`:** drop a required column → exit 1, not stamped.
- **`test_pg_migrate_concurrent`:** two threads on one empty schema → no errors, one
  `alembic_version` row.
- **`test_pg_0002_dedupe`:**
  - Seed at `0001`: duplicate `eod_snapshot` rows, a mixed snapshot/backfill pair and
    intraday rows.
  - After upgrade: one row per key, the latest one kept, the intraday count unchanged.
  - A duplicate insert violates `uq_price_snapshots_eod`.
  - Downgrade to `0001` works.
- **`test_pg_leader_failover`:**
  - Two `SchedulerLeader`s with a 1 s interval → exactly one leader.
  - Close the leader's lock connection → the other leads within 2 intervals, and the old one
    has stopped its scheduler.
- **`test_pg_notify_skip_locked`:** two concurrent notify passes with a fake sender → one send
  per alert.
- **`test_pg_upsert_eod`:** `DO UPDATE` and `DO NOTHING` semantics on the partial index.

Manual, once on the server:
- `docker port navigator-nginx` is empty.
- trender fetches the export over `edge`.
- Kill the leader worker (`kill <pid>`) → `scheduler_leader` recovers within 60 s in
  `/health/deep`.

## Left to the builder

- The exact TASE close times, after checking tase.co.il (constants in `sessions.py`).
- Whether `quote_refresh` and `price_alert_evaluation` are offset by about 30 s, to ride the
  60 s quote cache.
- The log wording, and whether to log the de-duplicated count per asset or as a total
  (total only is fine).
- The shape of the dialect-switching upsert helper.
- Removing the now-unused `_ENUM_LOCK_KEY` naming in favour of `MIGRATION_LOCK_KEY = 7400417`
  and `SCHEDULER_LOCK_KEY = 7400418` constants in one module.

## Open questions for the user

1. **Funds and "other" holdings** (for example TASE mutual funds) get no live quote today
   (`market_data.py:75`). This design reports them as `not_monitored` in
   `/health/deep` rather than paging (Ruling (lead, 2026-11-22)). Is that acceptable for this round, or do you hold such assets and want
   them priced first?
