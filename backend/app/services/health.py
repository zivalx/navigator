"""Deep health checks behind `GET /health/deep`.

Every `detail` holds counts and ages only: never quantities, costs, symbols,
error text or secrets. Check names are snake_case (the ops watchdog drops any
name outside `[a-z0-9_.:-]`).
"""
import logging
import time
from dataclasses import dataclass
from typing import NamedTuple
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..locks import SCHEDULER_LOCK_KEY
from ..models.alert import PriceAlert
from ..models.asset import Asset
from ..models.job_heartbeat import JobHeartbeat
from ..models.price import PriceSnapshot
from ..schema_version import db_revision, head_revision
from .log_redaction import redact_secrets
from .portfolio import _ensure_aware
from .sessions import (
    ALERTABLE_SOURCES,
    classify,
    is_in_session,
    latest_session_open,
    watched_assets,
)

logger = logging.getLogger(__name__)

Status = Literal["ok", "degraded", "down"]


@dataclass(frozen=True)
class Check:
    ok: bool
    detail: str
    critical: bool = True


POLLS_TOLERATED = 3  # a poll job is overdue after this many missed intervals


def poll_limits(quote_minutes: int, alert_minutes: int) -> dict[str, tuple[timedelta, bool]]:
    """Overdue thresholds of the two interval jobs: 3 x their interval."""
    return {
        "quote_refresh": (timedelta(minutes=POLLS_TOLERATED * quote_minutes), True),
        "price_alert_evaluation": (timedelta(minutes=POLLS_TOLERATED * alert_minutes), True),
    }


# job name -> (overdue threshold, critical)
JOB_LIMITS: dict[str, tuple[timedelta, bool]] = {
    **poll_limits(settings.quote_refresh_interval_minutes, settings.alert_evaluation_interval_minutes),
    "eod_snapshot": (timedelta(hours=26), True),
    "eod_catchup": (timedelta(hours=26), True),
    "breadth_snapshot": (timedelta(hours=26), False),
}
LEADER_LIMIT = timedelta(minutes=3)
LEADER_JOB = "scheduler_leader"

def alert_delivery_limit(alert_minutes: int) -> timedelta:
    """A triggered alert may wait three evaluation cycles for its Telegram send
    (one failed send is retried the next cycle)."""
    return timedelta(minutes=POLLS_TOLERATED * alert_minutes)


def open_grace(quote_minutes: int) -> timedelta:
    """Time after the open before prices must be fresh: two refresh cycles."""
    return timedelta(minutes=2 * quote_minutes)


ALERT_DELIVERY_LIMIT = alert_delivery_limit(settings.alert_evaluation_interval_minutes)
PRICE_FRESH_FOR = timedelta(minutes=POLLS_TOLERATED * settings.quote_refresh_interval_minutes)
OPEN_GRACE = open_grace(settings.quote_refresh_interval_minutes)
FRESH_SOURCES = frozenset().union(*ALERTABLE_SOURCES.values())
SKIPPED = "skipped: db unreachable"
STATEMENT_TIMEOUT = "2s"
DEEP_BUDGET_SECONDS = 8.0  # inside the watchdog's 10 s deep timeout


class _Hb(NamedTuple):
    """Heartbeat times detached from the session (a rollback expires ORM rows)."""
    last_started_at: Optional[datetime]
    last_success_at: Optional[datetime]
    last_error_at: Optional[datetime]


def _monotonic() -> float:
    return time.monotonic()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _age(delta: timedelta) -> str:
    secs = max(int(delta.total_seconds()), 0)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h"


def leader_lock_held(db: Session) -> bool:
    """True when some session holds the scheduler advisory lock in this database."""
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return True
    row = db.execute(
        text(
            "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND classid = 0 "
            "AND objid = :key AND objsubid = 1 AND granted "
            "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "LIMIT 1"
        ),
        {"key": SCHEDULER_LOCK_KEY},
    ).first()
    return row is not None


def _reference(hb: Optional[_Hb], leader_hb: Optional[_Hb]):
    """(reference time, is_success): last success, else the leader's last start."""
    if hb is not None and hb.last_success_at is not None:
        return _ensure_aware(hb.last_success_at), True
    if leader_hb is not None and leader_hb.last_started_at is not None:
        return _ensure_aware(leader_hb.last_started_at), False
    return None, False


def _check_leader(db: Session, now: datetime, hbs: dict[str, _Hb]) -> Check:
    if not leader_lock_held(db):
        return Check(False, "no leader lock held")
    hb = hbs.get(LEADER_JOB)
    stamps = [_ensure_aware(t) for t in (hb.last_started_at, hb.last_success_at) if t] if hb else []
    ref = max(stamps) if stamps else None  # a restart stamps last_started_at, not last_success_at
    if ref is None:
        return Check(False, "no leader heartbeat")
    age = now - ref
    detail = f"leader heartbeat {_age(age)} ago"
    return Check(age <= LEADER_LIMIT, detail)


def _check_job(name: str, now: datetime, hbs: dict[str, _Hb]) -> Check:
    limit, critical = JOB_LIMITS[name]
    hb = hbs.get(name)
    ref, is_success = _reference(hb, hbs.get(LEADER_JOB))
    if ref is None:
        return Check(False, "no heartbeat yet", critical)
    age = now - ref
    ok = age <= limit
    if is_success:
        detail = f"last success {_age(age)} ago"
    else:
        detail = f"no success yet; leader started {_age(age)} ago"
    if not ok:
        detail += f" (limit {_age(limit)})"
    if hb is not None and hb.last_error_at is not None:
        detail += f"; last error {_age(now - _ensure_aware(hb.last_error_at))} ago"
    return Check(ok, detail, critical)


def _check_alert_delivery(db: Session, now: datetime) -> Check:
    rows = (
        db.query(PriceAlert.triggered_at)
        .filter(
            PriceAlert.triggered_at.isnot(None),
            PriceAlert.notified_at.is_(None),
            PriceAlert.notification_skipped_at.is_(None),
        )
        .all()
    )
    stuck = sum(1 for (t,) in rows if now - _ensure_aware(t) > ALERT_DELIVERY_LIMIT)
    if stuck:
        return Check(False, f"{stuck} triggered alerts undelivered for {_age(ALERT_DELIVERY_LIMIT)}+")
    return Check(True, "all delivered")


def _fresh_sources(db: Session, now: datetime) -> dict[str, set[str]]:
    cutoff = now - PRICE_FRESH_FOR
    rows = (
        db.query(PriceSnapshot.asset_id, PriceSnapshot.source, PriceSnapshot.timestamp)
        .filter(PriceSnapshot.source.in_(FRESH_SOURCES), PriceSnapshot.timestamp >= cutoff)
        .all()
    )
    out: dict[str, set[str]] = {}
    for asset_id, source, ts in rows:
        if _ensure_aware(ts) >= cutoff:
            out.setdefault(asset_id, set()).add(source)
    return out


def _price_checks(db: Session, now: datetime) -> dict[str, Check]:
    assets_by_exchange: dict[str, list[Asset]] = {"US": [], "TASE": [], "crypto": []}
    for asset in watched_assets(db):
        group = assets_by_exchange.get(classify(asset))
        if group is not None:
            group.append(asset)
    fresh = _fresh_sources(db, now)
    out: dict[str, Check] = {}
    for exchange, key in (("US", "prices_us"), ("TASE", "prices_tase"), ("crypto", "prices_crypto")):
        if exchange != "crypto":
            opened = latest_session_open(exchange, now)
            if not is_in_session(exchange, now) or opened is None or now < opened + OPEN_GRACE:
                out[key] = Check(True, "closed")
                continue
        out[key] = _freshness(exchange, assets_by_exchange[exchange], fresh)
    return out


def _freshness(exchange: str, assets: list[Asset], fresh: dict[str, set[str]]) -> Check:
    monitored = not_monitored = missing = fallback_only = 0
    for asset in assets:
        atype = asset.asset_type.value if hasattr(asset.asset_type, "value") else asset.asset_type
        allowed = ALERTABLE_SOURCES.get(atype)
        if allowed is None:
            not_monitored += 1
            continue
        monitored += 1
        seen = fresh.get(asset.id, set()) & allowed
        if not seen:
            missing += 1
        elif exchange == "crypto" and "coingecko" not in seen:
            fallback_only += 1
    if monitored == 0:
        return Check(True, "no monitored assets" + (f"; {not_monitored} not_monitored" if not_monitored else ""))
    mins = int(PRICE_FRESH_FOR.total_seconds() // 60)
    suffix = f"; {not_monitored} not_monitored" if not_monitored else ""
    base = f"{missing} of {monitored} watched assets without a fresh price for {mins}+ min"
    if missing:
        return Check(False, base + suffix)
    if fallback_only:
        return Check(
            False,
            f"{fallback_only} of {monitored} watched assets on fallback source only" + suffix,
            critical=False,
        )
    return Check(True, base + suffix)


def _check_migrations(db: Session) -> Check:
    rev = db_revision(db.connection())
    head = head_revision()
    if rev == head:
        return Check(True, "at head")
    return Check(False, "database revision differs from code head")


def _rollback(db: Session) -> None:
    """Roll back and re-arm the statement timeout (SET LOCAL dies with the
    transaction, so every rollback would otherwise leave later checks unbounded)."""
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        return
    arm_statement_timeout(db)


def arm_statement_timeout(db: Session) -> None:
    """`SET LOCAL statement_timeout` for the current transaction (Postgres only)."""
    try:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
    except Exception:  # noqa: BLE001  (the db check reports an unreachable database)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def _log_failure(what: str, exc: Exception) -> None:
    # No traceback and no raw text: driver errors can carry host, user or DSN.
    logger.error("health: %s: %s", what, redact_secrets(exc))


def _guard(fn: Callable[[], Check], db: Session, critical: bool = True) -> Check:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001  (no error text in the body)
        _log_failure("check failed", e)
        _rollback(db)
        return Check(False, "check failed", critical)


class _Budget:
    def __init__(self, seconds: float):
        self.deadline = _monotonic() + seconds

    def spent(self) -> bool:
        return _monotonic() >= self.deadline


def deep_checks(db: Session, now: datetime, budget_seconds: float = DEEP_BUDGET_SECONDS) -> dict[str, Check]:
    keys = (
        ["db", "migrations", "scheduler_leader"]
        + [f"job_{n}" for n in JOB_LIMITS]
        + ["alert_delivery", "prices_us", "prices_tase", "prices_crypto"]
    )
    critical_of = {f"job_{n}": c for n, (_, c) in JOB_LIMITS.items()}
    budget = _Budget(budget_seconds)

    def failed(detail: str) -> dict[str, Check]:
        return {k: Check(False, detail, critical_of.get(k, True)) for k in keys}

    try:
        db.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        _log_failure("database unreachable", e)
        _rollback(db)
        out = failed(SKIPPED)
        out["db"] = Check(False, "unreachable")
        return out

    out: dict[str, Check] = {"db": Check(True, "reachable")}

    def step(key: str, fn: Callable[[], Check]) -> None:
        if budget.spent():
            out[key] = Check(False, "check failed", critical_of.get(key, True))
        else:
            out[key] = _guard(fn, db, critical_of.get(key, True))

    step("migrations", lambda: _check_migrations(db))

    hbs: Optional[dict[str, _Hb]] = None
    if not budget.spent():
        try:
            hbs = {
                h.job_name: _Hb(h.last_started_at, h.last_success_at, h.last_error_at)
                for h in db.query(JobHeartbeat).all()
            }
        except Exception as e:  # noqa: BLE001
            _log_failure("heartbeat read failed", e)
            _rollback(db)
    if hbs is None:
        for k in ["scheduler_leader"] + [f"job_{n}" for n in JOB_LIMITS]:
            out[k] = Check(False, "check failed", critical_of.get(k, True))
    else:
        step("scheduler_leader", lambda: _check_leader(db, now, hbs))
        for name in JOB_LIMITS:
            step(f"job_{name}", lambda n=name: _check_job(n, now, hbs))

    step("alert_delivery", lambda: _check_alert_delivery(db, now))

    price_keys = ("prices_us", "prices_tase", "prices_crypto")
    if budget.spent():
        for k in price_keys:
            out[k] = Check(False, "check failed")
    else:
        try:
            out.update(_price_checks(db, now))
        except Exception as e:  # noqa: BLE001
            _log_failure("price checks failed", e)
            _rollback(db)
            for k in price_keys:
                out[k] = Check(False, "check failed")
    return {k: out[k] for k in keys}


def overall_status(checks: dict[str, Check]) -> Status:
    failed = [c for c in checks.values() if not c.ok]
    if any(c.critical for c in failed):
        return "down"
    return "degraded" if failed else "ok"
