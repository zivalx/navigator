"""Scheduler leader election: exactly one process runs the APScheduler jobs.

Every gunicorn worker runs a SchedulerLeader loop. Each tick a follower tries
the session advisory lock SCHEDULER_LOCK_KEY on its own dedicated connection;
the one that gets it builds and starts the scheduler. Each tick the leader
pings that connection; if the session is gone, so is the lock, and the leader
stops its scheduler at once and goes back to following. Postgres frees the
lock the moment the holder's session dies, so another worker takes over
within one interval. SQLite has no advisory locks: the process always leads.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from sqlalchemy import Connection, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from ..config import settings
from ..locks import SCHEDULER_LOCK_KEY
from ..services.log_redaction import redact_secrets
from . import heartbeat

logger = logging.getLogger(__name__)

SCHEDULER_LEADER_INTERVAL_SECONDS = 30
LEADER_HEARTBEAT = "scheduler_leader"
# How long stop() waits for an in-flight tick before giving up on it.
_STOP_WAIT_SECONDS = 5.0

_KEEPALIVES = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3}


class LeaderLock(Protocol):
    def try_acquire(self) -> bool: ...
    def ping(self) -> bool: ...
    def release(self) -> None: ...


class PgAdvisoryLock:
    """Session advisory lock `key` on one AUTOCOMMIT connection that is never
    shared with jobs or requests. Not thread-safe: SchedulerLeader calls it
    from one worker thread at a time."""

    def __init__(self, database_url: str, key: int = SCHEDULER_LOCK_KEY):
        self._key = int(key)
        self._engine = create_engine(database_url, poolclass=NullPool, connect_args=_KEEPALIVES)
        self._conn: Connection | None = None
        self._pid: int | None = None

    def backend_pid(self) -> int | None:
        """Server pid of the lock session, None while disconnected."""
        return self._pid

    def try_acquire(self) -> bool:
        try:
            if self._conn is None:
                self._conn = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
                self._pid = self._conn.exec_driver_sql("SELECT pg_backend_pid()").scalar()
            return bool(self._conn.exec_driver_sql(f"SELECT pg_try_advisory_lock({self._key})").scalar())
        except Exception:
            self._drop()  # reconnect on the next tick
            raise

    def ping(self) -> bool:
        """True while the session that took the lock is alive. Comparing the
        pid (not just SELECT 1) means a silently replaced session never passes."""
        if self._conn is None:
            return False
        try:
            return self._conn.exec_driver_sql("SELECT pg_backend_pid()").scalar() == self._pid
        except Exception:
            self._drop()
            raise

    def release(self) -> None:
        """Unlock and close the connection (a no-op when disconnected)."""
        if self._conn is None:
            return
        try:
            self._conn.exec_driver_sql(f"SELECT pg_advisory_unlock({self._key})")
        except Exception as e:  # noqa: BLE001  (closing the session frees the lock anyway)
            logger.warning("advisory unlock %s failed, dropping the connection: %s", self._key, redact_secrets(e))
        finally:
            self._drop()

    def _drop(self) -> None:
        conn, self._conn, self._pid = self._conn, None, None
        if conn is None:
            return
        try:
            conn.invalidate()  # close the DBAPI connection; never reuse this session
            conn.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("closing the lock connection failed: %s", redact_secrets(e))


class AlwaysLeaderLock:
    """No advisory locks (SQLite tests, ad hoc dev): this process always leads."""

    def try_acquire(self) -> bool:
        return True

    def ping(self) -> bool:
        return True

    def release(self) -> None:
        return None


def _default_scheduler_factory(startup_at=None):
    # Imported here, not at module level: scheduler -> heartbeat -> leader -> scheduler.
    from .scheduler import build_scheduler

    return build_scheduler(startup_at=startup_at)


class SchedulerLeader:
    """Leader loop. Lock and heartbeat calls block, so they run in a thread."""

    def __init__(
        self,
        lock: LeaderLock,
        scheduler_factory: Callable | None = None,
        interval: float = SCHEDULER_LEADER_INTERVAL_SECONDS,
        startup_delay: float = 15,
    ):
        self._lock = lock
        self._scheduler_factory = scheduler_factory or _default_scheduler_factory
        self._interval = interval
        self._startup_delay = startup_delay
        self._scheduler = None
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    @property
    def is_leader(self) -> bool:
        return self._scheduler is not None

    async def tick(self) -> None:
        """One election step. Never raises: errors are logged and retried next tick."""
        try:
            if self._scheduler is None:
                await self._follower_step()
            else:
                await self._leader_step()
        except Exception as e:  # noqa: BLE001
            logger.error("scheduler leader tick failed: %s", redact_secrets(e))

    async def _follower_step(self) -> None:
        if not await asyncio.to_thread(self._lock.try_acquire):
            return
        try:
            startup_at = datetime.now(timezone.utc) + timedelta(seconds=self._startup_delay)
            scheduler = self._scheduler_factory(startup_at=startup_at)
            scheduler.start()
        except Exception:
            # Holding the lock without a scheduler would stop every worker's jobs.
            await self._release_quietly()
            raise
        self._scheduler = scheduler
        logger.info("scheduler leader: acquired the lock, scheduler started")
        await asyncio.to_thread(
            heartbeat.upsert_heartbeat, LEADER_HEARTBEAT, last_started_at=datetime.now(timezone.utc)
        )

    async def _leader_step(self) -> None:
        try:
            alive = await asyncio.to_thread(self._lock.ping)
        except Exception as e:  # noqa: BLE001
            logger.warning("scheduler leader: lock ping failed: %s", redact_secrets(e))
            alive = False
        if not alive:
            self._shutdown_scheduler()
            logger.warning("scheduler leader: lost the lock, scheduler stopped")
            await self._release_quietly()  # drop the connection; reconnect as a follower
            return
        await asyncio.to_thread(
            heartbeat.upsert_heartbeat, LEADER_HEARTBEAT, last_success_at=datetime.now(timezone.utc)
        )

    def _shutdown_scheduler(self) -> None:
        scheduler, self._scheduler = self._scheduler, None
        if scheduler is None:
            return
        try:
            scheduler.shutdown(wait=False)
        except Exception as e:  # noqa: BLE001
            logger.error("scheduler shutdown failed: %s", redact_secrets(e))

    async def _release_quietly(self) -> None:
        try:
            await asyncio.to_thread(self._lock.release)
        except Exception as e:  # noqa: BLE001
            logger.warning("scheduler leader: lock release failed: %s", redact_secrets(e))

    async def _run(self) -> None:
        while not self._stopping.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        """Start the loop in the background; the first tick runs right away."""
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="scheduler-leader")

    async def stop(self) -> None:
        """Stop the loop, shut the scheduler down, then unlock and close."""
        self._stopping.set()
        task, self._task = self._task, None
        if task is not None:
            done, _ = await asyncio.wait({task}, timeout=_STOP_WAIT_SECONDS)
            if not done:
                # A tick is stuck in a lock call on the lock connection; don't
                # touch that connection from another thread. The process is
                # exiting and its closing socket frees the lock.
                task.cancel()
                self._shutdown_scheduler()
                logger.warning("scheduler leader: stop timed out; lock left to the closing session")
                return
        self._shutdown_scheduler()
        await self._release_quietly()


def make_leader() -> SchedulerLeader | None:
    """The process's leader, or None when SCHEDULER_ENABLED is false."""
    if not settings.scheduler_enabled:
        return None
    url = settings.database_url
    if make_url(url).get_backend_name() == "postgresql":
        lock: LeaderLock = PgAdvisoryLock(url)
    else:
        lock = AlwaysLeaderLock()
    return SchedulerLeader(lock)


_current: SchedulerLeader | None = None


def set_current(leader: SchedulerLeader | None) -> None:
    global _current
    _current = leader


def is_leader() -> bool:
    """Whether this process currently leads (False when no leader is set)."""
    return _current is not None and _current.is_leader
