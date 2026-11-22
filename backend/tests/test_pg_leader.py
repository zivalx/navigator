"""Scheduler leader election on a real Postgres (opt-in: TEST_POSTGRES_URL)."""
import asyncio
import os
from datetime import datetime, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.locks import SCHEDULER_LOCK_KEY
from app.models.job_heartbeat import JobHeartbeat
from app.schema_version import alembic_config
from app.tasks import heartbeat
from app.tasks.leader import PgAdvisoryLock, SchedulerLeader
from tests.pg_helpers import requires_pg, scratch_schema


class FakeScheduler:
    def __init__(self):
        self.started = False
        self.shutdown_calls = []

    def start(self):
        self.started = True

    def shutdown(self, wait=True):
        self.shutdown_calls.append(wait)


def _leader(url, built):
    def factory(startup_at=None):
        built.append(FakeScheduler())
        return built[-1]

    return SchedulerLeader(PgAdvisoryLock(url), scheduler_factory=factory, interval=1)


def _lock_holders(admin):
    with admin.connect() as c:
        return [r[0] for r in c.exec_driver_sql(
            "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted "
            f"AND classid = 0 AND objid = {SCHEDULER_LOCK_KEY} AND objsubid = 1 "
            "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
        )]


@requires_pg
@pytest.mark.asyncio
async def test_pg_leader_failover(monkeypatch):
    url = os.environ["TEST_POSTGRES_URL"]
    monkeypatch.setattr(heartbeat, "upsert_heartbeat", lambda *a, **k: None)
    admin = create_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    built_a, built_b = [], []
    a, b = _leader(url, built_a), _leader(url, built_b)
    try:
        await a.tick()
        await b.tick()
        assert [a.is_leader, b.is_leader].count(True) == 1
        old, other = (a, b) if a.is_leader else (b, a)
        old_fake = (built_a if old is a else built_b)[0]
        pid = old._lock.backend_pid()
        assert _lock_holders(admin) == [pid]

        with admin.connect() as c:  # the leader's session dies (worker killed, network cut)
            assert c.exec_driver_sql(f"SELECT pg_terminate_backend({pid})").scalar() is True

        for _ in range(2):
            await old.tick()
            await other.tick()
            if other.is_leader:
                break
            await asyncio.sleep(1)  # one interval: the terminated backend frees the lock on exit

        assert other.is_leader and not old.is_leader and old_fake.shutdown_calls == [False]
        assert _lock_holders(admin) == [other._lock.backend_pid()]

        await other.stop()  # unlock + close: the lock is free again
        assert _lock_holders(admin) == []
    finally:
        await a.stop()
        await b.stop()
        admin.dispose()


@requires_pg
def test_pg_upsert_heartbeat_updates_only_given_fields(monkeypatch):
    with scratch_schema() as eng:
        command.upgrade(alembic_config(eng), "head")
        Session = sessionmaker(bind=eng)
        monkeypatch.setattr(heartbeat, "SessionLocal", Session)
        t1 = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 11, 22, 10, 0, 30, tzinfo=timezone.utc)
        heartbeat.upsert_heartbeat("scheduler_leader", last_started_at=t1)
        heartbeat.upsert_heartbeat("scheduler_leader", last_success_at=t2)
        with Session() as s:
            hb = s.get(JobHeartbeat, "scheduler_leader")
            assert (hb.last_started_at, hb.last_success_at, hb.last_error_at) == (t1, t2, None)


@requires_pg
def test_pg_lock_connection_has_tcp_bounds():
    """Server bound (keepalives, 60 s user timeout) longer than the client's
    20 s tcp_user_timeout: the leader demotes before Postgres frees the lock."""
    lock = PgAdvisoryLock(os.environ["TEST_POSTGRES_URL"])
    try:
        assert lock.try_acquire()
        conn = lock._conn
        server = dict(conn.exec_driver_sql(
            "SELECT name, setting FROM pg_settings WHERE name IN "
            "('tcp_keepalives_idle', 'tcp_keepalives_interval', 'tcp_keepalives_count', 'tcp_user_timeout')"
        ).all())
        assert server == {
            "tcp_keepalives_idle": "30",
            "tcp_keepalives_interval": "10",
            "tcp_keepalives_count": "3",
            "tcp_user_timeout": "60000",
        }
        client = conn.connection.dbapi_connection.get_dsn_parameters()
        assert client["tcp_user_timeout"] == "20000" and client["keepalives_idle"] == "30"
    finally:
        lock.release()
