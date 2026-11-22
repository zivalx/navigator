"""SchedulerLeader with a mocked lock and a fake scheduler (no Postgres)."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.job_heartbeat import JobHeartbeat
from app.tasks import heartbeat, leader as leader_module
from app.tasks.leader import (
    AlwaysLeaderLock,
    PgAdvisoryLock,
    SchedulerLeader,
    is_leader,
    make_leader,
    set_current,
)


class FakeLock:
    """`acquire` / `ping` are sequences of results; an exception instance is raised."""

    def __init__(self, acquire=(True,), ping=(True,), events=None):
        self._acquire = list(acquire)
        self._ping = list(ping)
        self.events = events if events is not None else []

    @staticmethod
    def _next(seq):
        value = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(value, BaseException):
            raise value
        return value

    def try_acquire(self) -> bool:
        return self._next(self._acquire)

    def ping(self) -> bool:
        return self._next(self._ping)

    def release(self) -> None:
        self.events.append("release")


class FakeScheduler:
    def __init__(self, events):
        self.events = events
        self.started = False
        self.shutdown_calls = []

    def start(self):
        self.started = True

    def shutdown(self, wait=True):
        self.shutdown_calls.append(wait)
        self.events.append("shutdown")


class Factory:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.calls = []  # startup_at of each call
        self.built = []

    def __call__(self, startup_at=None):
        self.calls.append(startup_at)
        fake = FakeScheduler(self.events)
        self.built.append(fake)
        return fake


@pytest.fixture(autouse=True)
def hb_db(db_session, monkeypatch):
    """Heartbeat writes go to the test's in-memory DB, never settings.database_url."""
    monkeypatch.setattr(heartbeat, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    return db_session


def row(db_session, name):
    db_session.expire_all()
    return db_session.get(JobHeartbeat, name)


@pytest.mark.asyncio
async def test_follower_never_builds_scheduler():
    factory = Factory()
    leader = SchedulerLeader(FakeLock(acquire=[False]), scheduler_factory=factory)
    for _ in range(3):
        await leader.tick()
    assert len(factory.calls) == 0 and not leader.is_leader


@pytest.mark.asyncio
async def test_acquire_starts_scheduler_and_stamps_heartbeat(db_session):
    factory = Factory()
    leader = SchedulerLeader(FakeLock(), scheduler_factory=factory, startup_delay=15)
    before = datetime.now(timezone.utc)
    await leader.tick()
    fake = factory.built[0]
    assert len(factory.calls) == 1 and fake.started and leader.is_leader
    assert row(db_session, "scheduler_leader").last_started_at is not None
    # leader_startup is scheduled startup_delay after acquiring
    startup_at = factory.calls[0]
    assert before + timedelta(seconds=15) <= startup_at <= datetime.now(timezone.utc) + timedelta(seconds=15)


@pytest.mark.asyncio
async def test_leader_tick_stamps_success(db_session):
    factory = Factory()
    leader = SchedulerLeader(FakeLock(ping=[True]), scheduler_factory=factory)
    await leader.tick()
    assert row(db_session, "scheduler_leader").last_success_at is None
    await leader.tick()
    hb = row(db_session, "scheduler_leader")
    assert hb.last_success_at is not None and len(factory.calls) == 1
    assert hb.last_started_at is not None  # the upsert touched only the given field


@pytest.mark.asyncio
@pytest.mark.parametrize("ping", [False, OperationalError("SELECT pg_backend_pid()", {}, Exception("gone"))])
async def test_ping_failure_shuts_scheduler_down(ping):
    events = []
    lock = FakeLock(ping=[ping], events=events)
    factory = Factory(events)
    leader = SchedulerLeader(lock, scheduler_factory=factory)
    await leader.tick()  # acquire
    await leader.tick()  # ping fails
    fake = factory.built[0]
    assert fake.shutdown_calls == [False] and not leader.is_leader
    assert events == ["shutdown", "release"]  # connection dropped after the scheduler stops


@pytest.mark.asyncio
async def test_demoted_leader_can_lead_again():
    factory = Factory()
    leader = SchedulerLeader(FakeLock(acquire=[True], ping=[False]), scheduler_factory=factory)
    await leader.tick()  # acquire
    await leader.tick()  # ping fails, demote
    await leader.tick()  # acquire again with a fresh scheduler
    assert leader.is_leader and len(factory.calls) == 2 and factory.built[1].started


@pytest.mark.asyncio
async def test_failed_start_gives_the_lock_back():
    lock = FakeLock()

    def broken_factory(startup_at=None):
        raise RuntimeError("bad job config")

    leader = SchedulerLeader(lock, scheduler_factory=broken_factory)
    await leader.tick()  # logged, not raised
    assert not leader.is_leader and lock.events == ["release"]


@pytest.mark.asyncio
async def test_loop_survives_lock_errors():
    err = OperationalError("SELECT pg_try_advisory_lock(7400418)", {}, Exception("server closed the connection"))
    lock = FakeLock(acquire=[err, True])
    leader = SchedulerLeader(lock, scheduler_factory=Factory(), interval=0.01)
    await leader.start()
    try:
        for _ in range(200):
            if leader.is_leader:
                break
            await asyncio.sleep(0.01)
        assert leader.is_leader
    finally:
        await leader.stop()


@pytest.mark.asyncio
async def test_stop_shuts_down_then_releases():
    events = []
    leader = SchedulerLeader(FakeLock(events=events), scheduler_factory=Factory(events), interval=60)
    await leader.start()
    for _ in range(200):
        if leader.is_leader:
            break
        await asyncio.sleep(0.01)
    await leader.stop()
    assert events == ["shutdown", "release"] and not leader.is_leader


@pytest.mark.asyncio
async def test_follower_stop_only_releases():
    events = []
    leader = SchedulerLeader(FakeLock(acquire=[False], events=events), scheduler_factory=Factory(events))
    await leader.tick()
    await leader.stop()
    assert events == ["release"]


@pytest.mark.asyncio
async def test_module_is_leader_follows_current():
    leader = SchedulerLeader(FakeLock(), scheduler_factory=Factory())
    try:
        set_current(None)
        assert is_leader() is False
        set_current(leader)
        assert is_leader() is False
        await leader.tick()
        assert is_leader() is True
    finally:
        set_current(None)


def test_scheduler_disabled_gives_no_leader(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    assert make_leader() is None


def test_sqlite_url_is_always_leader(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    monkeypatch.setattr(settings, "database_url", "sqlite:///./navigator.db")
    assert isinstance(make_leader()._lock, AlwaysLeaderLock)


def test_postgres_url_gets_advisory_lock(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://u:p@localhost:1/x")
    lock = make_leader()._lock  # builds an engine; connects only on try_acquire
    assert isinstance(lock, PgAdvisoryLock) and lock.backend_pid() is None
    assert leader_module.SCHEDULER_LEADER_INTERVAL_SECONDS == 30
