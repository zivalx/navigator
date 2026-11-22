"""Job heartbeats (`job_heartbeats`): start, success and error times per job.

Each write is an `INSERT … ON CONFLICT (job_name) DO UPDATE` in its own
session and commit, so a job's rollback can't erase its heartbeat.
"""
import functools
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ..database import SessionLocal
from ..db_upsert import insert_for
from ..models.job_heartbeat import JobHeartbeat
from ..services.log_redaction import redact_secrets
# Module import, resolved at call time: leader imports this module too.
from . import leader

logger = logging.getLogger(__name__)

AsyncJob = Callable[[], Awaitable[None]]
MAX_ERROR_CHARS = 500

HEARTBEAT_FIELDS = frozenset({"last_started_at", "last_success_at", "last_error_at", "last_error"})


def upsert_heartbeat(job_name: str, **fields) -> None:
    """Upsert the `job_name` row, setting only `fields` (a subset of
    HEARTBEAT_FIELDS); columns not given keep their stored value."""
    unknown = set(fields) - HEARTBEAT_FIELDS
    if unknown:
        raise ValueError(f"unknown heartbeat fields: {sorted(unknown)}")
    db = SessionLocal()
    try:
        stmt = insert_for(db)(JobHeartbeat.__table__).values(job_name=job_name, **fields)
        if fields:
            stmt = stmt.on_conflict_do_update(
                index_elements=["job_name"],
                set_={name: stmt.excluded[name] for name in fields},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=["job_name"])
        db.execute(stmt)
        db.commit()
    finally:
        db.close()


def _stamp(job_name: str, **fields) -> None:
    """Upsert a heartbeat; a failing write is logged, never raised, so it can
    neither stop a job from running nor turn its outcome around."""
    try:
        upsert_heartbeat(job_name, **fields)
    except Exception as e:  # noqa: BLE001
        logger.error("heartbeat write for %s failed: %s", job_name, redact_secrets(e))


def heartbeat(job_name: str) -> Callable[[AsyncJob], AsyncJob]:
    """Wrap a scheduled job: skip it when this process isn't the leader (so a
    split-brain window lasts one loop period at most), otherwise stamp start,
    then success, or the redacted error. A failing job is logged and
    swallowed; APScheduler never sees the exception."""

    def decorate(job: AsyncJob) -> AsyncJob:
        @functools.wraps(job)
        async def wrapper() -> None:
            if not leader.is_leader():
                return
            _stamp(job_name, last_started_at=datetime.now(timezone.utc))
            try:
                await job()
            except Exception as e:  # noqa: BLE001
                text = redact_secrets(e)
                logger.error("job %s failed: %s", job_name, text)
                _stamp(
                    job_name,
                    last_error_at=datetime.now(timezone.utc),
                    last_error=text[:MAX_ERROR_CHARS],
                )
                return
            _stamp(job_name, last_success_at=datetime.now(timezone.utc))

        wrapper.__heartbeat_name__ = job_name
        return wrapper

    return decorate
