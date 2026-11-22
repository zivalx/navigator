"""Deep health checks behind `GET /health/deep`.

Every `detail` holds counts and ages only: never quantities, costs, symbols,
error text or secrets. Check names are snake_case (the ops watchdog drops any
name outside `[a-z0-9_.:-]`).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..locks import SCHEDULER_LOCK_KEY
from ..models.alert import PriceAlert
from ..models.asset import Asset
from ..models.job_heartbeat import JobHeartbeat
from ..models.price import PriceSnapshot
from ..schema_version import db_revision, head_revision
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


# job name -> (overdue threshold, critical)
JOB_LIMITS: dict[str, tuple[timedelta, bool]] = {
    "quote_refresh": (timedelta(minutes=15), True),
    "price_alert_evaluation": (timedelta(minutes=15), True),
    "eod_snapshot": (timedelta(hours=26), True),
    "eod_catchup": (timedelta(hours=26), True),
    "breadth_snapshot": (timedelta(hours=26), False),
}
LEADER_LIMIT = timedelta(minutes=3)
LEADER_JOB = "scheduler_leader"

ALERT_DELIVERY_LIMIT = timedelta(minutes=15)
PRICE_FRESH_FOR = timedelta(minutes=30)
OPEN_GRACE = timedelta(minutes=15)
FRESH_SOURCES = frozenset().union(*ALERTABLE_SOURCES.values())
SKIPPED = "skipped: db unreachable"


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


def _reference(hb: Optional[JobHeartbeat], leader_hb: Optional[JobHeartbeat]):
    """(reference time, is_success): last success, else the leader's last start."""
    if hb is not None and hb.last_success_at is not None:
        return _ensure_aware(hb.last_success_at), True
    if leader_hb is not None and leader_hb.last_started_at is not None:
        return _ensure_aware(leader_hb.last_started_at), False
    return None, False


def _check_leader(db: Session, now: datetime, hbs: dict[str, JobHeartbeat]) -> Check:
    if not leader_lock_held(db):
        return Check(False, "no leader lock held")
    hb = hbs.get(LEADER_JOB)
    ref, _ = _reference(hb, hb)
    if ref is None:
        return Check(False, "no leader heartbeat")
    age = now - ref
    detail = f"leader heartbeat {_age(age)} ago"
    return Check(age <= LEADER_LIMIT, detail)


def _check_job(name: str, now: datetime, hbs: dict[str, JobHeartbeat]) -> Check:
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


def _guard(fn: Callable[[], Check], db: Session, critical: bool = True) -> Check:
    try:
        return fn()
    except Exception:  # noqa: BLE001  (no error text in the body)
        logger.exception("health check failed")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return Check(False, "check failed", critical)


def deep_checks(db: Session, now: datetime) -> dict[str, Check]:
    keys = (
        ["db", "migrations", "scheduler_leader"]
        + [f"job_{n}" for n in JOB_LIMITS]
        + ["alert_delivery", "prices_us", "prices_tase", "prices_crypto"]
    )
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        logger.warning("health: database unreachable")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        out = {k: Check(False, SKIPPED) for k in keys}
        out["db"] = Check(False, "unreachable")
        for name, (_, critical) in JOB_LIMITS.items():
            out[f"job_{name}"] = Check(False, SKIPPED, critical)
        return out

    out: dict[str, Check] = {"db": Check(True, "reachable")}
    out["migrations"] = _guard(lambda: _check_migrations(db), db)

    hbs: dict[str, JobHeartbeat] = {}
    hb_ok = True
    try:
        hbs = {h.job_name: h for h in db.query(JobHeartbeat).all()}
    except Exception:  # noqa: BLE001
        logger.exception("health: heartbeat read failed")
        hb_ok = False
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    if hb_ok:
        out["scheduler_leader"] = _guard(lambda: _check_leader(db, now, hbs), db)
        for name, (_, critical) in JOB_LIMITS.items():
            out[f"job_{name}"] = _guard(lambda n=name: _check_job(n, now, hbs), db, critical)
    else:
        out["scheduler_leader"] = Check(False, "check failed")
        for name, (_, critical) in JOB_LIMITS.items():
            out[f"job_{name}"] = Check(False, "check failed", critical)

    out["alert_delivery"] = _guard(lambda: _check_alert_delivery(db, now), db)
    try:
        out.update(_price_checks(db, now))
    except Exception:  # noqa: BLE001
        logger.exception("health: price checks failed")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        for k in ("prices_us", "prices_tase", "prices_crypto"):
            out[k] = Check(False, "check failed")
    return {k: out[k] for k in keys}


def overall_status(checks: dict[str, Check]) -> Status:
    failed = [c for c in checks.values() if not c.ok]
    if any(c.critical for c in failed):
        return "down"
    return "degraded" if failed else "ok"
