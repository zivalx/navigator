"""Job heartbeats (`job_heartbeats`): start, success and error times per job.

Each write is an `INSERT … ON CONFLICT (job_name) DO UPDATE` in its own
session and commit, so a job's rollback can't erase its heartbeat.
"""
from ..database import SessionLocal
from ..db_upsert import insert_for
from ..models.job_heartbeat import JobHeartbeat

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
