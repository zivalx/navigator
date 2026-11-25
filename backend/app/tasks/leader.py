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
# Longest a lock or heartbeat call may block a tick (asyncio backstop to the
# TCP bounds below); a slow ping demotes the leader.
LOCK_CALL_TIMEOUT_SECONDS = 10.0
# How long stop() waits for an in-flight tick before giving up on it.
_STOP_WAIT_SECONDS = 5.0

# Server side of the lock session (per-session settings, lock connection only).
_SERVER_KEEPALIVES_IDLE = 30
_SERVER_KEEPALIVES_INTERVAL = 10
_SERVER_KEEPALIVES_COUNT = 3
_SERVER_TCP_USER_TIMEOUT_MS = 60000
_SERVER_TCP = (
    f"-c tcp_keepalives_idle={_SERVER_KEEPALIVES_IDLE} "
    f"-c tcp_keepalives_interval={_SERVER_KEEPALIVES_INTERVAL} "
    f"-c tcp_keepalives_count={_SERVER_KEEPALIVES_COUNT} "
    f"-c tcp_user_timeout={_SERVER_TCP_USER_TIMEOUT_MS}"
)
# Client side. keepalives only cover an idle socket; tcp_user_timeout bounds a
# query in flight (without it Linux retransmits for ~15 min).
_CLIENT_TCP = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
    "tcp_user_timeout": 20000,  # ms
}

# Split-brain invariant: the leader must stop its scheduler BEFORE Postgres
# frees the lock, so the client bound must stay shorter than the server bound.
# - Client: after the last packet of a good ping, the heartbeat stamp may take
#   up to LOCK_CALL_TIMEOUT_SECONDS, the loop then sleeps one interval, and the
#   next ping times out after LOCK_CALL_TIMEOUT_SECONDS and demotes:
#   interval + 2 * call timeout = 30 + 20 = 50 s at most.
# - Server: it frees the session no sooner than keepalive idle + count *
#   interval = 30 + 3 * 10 = 60 s after the last packet it received
#   (tcp_user_timeout 60 s bounds unacknowledged data the same way).
_CLIENT_DEMOTE_BOUND_SECONDS = SCHEDULER_LEADER_INTERVAL_SECONDS + 2 * LOCK_CALL_TIMEOUT_SECONDS
_SERVER_RELEASE_BOUND_SECONDS = min(
    _SERVER_KEEPALIVES_IDLE + _SERVER_KEEPALIVES_COUNT * _SERVER_KEEPALIVES_INTERVAL,
    _SERVER_TCP_USER_TIMEOUT_MS / 1000,
)
assert _CLIENT_DEMOTE_BOUND_SECONDS < _SERVER_RELEASE_BOUND_SECONDS, "leader would outlive its lock"


def _lock_connect_args(database_url: str) -> dict:
    """Client TCP settings, plus the server settings appended to any
    `options` the URL already carries (never replacing them)."""
    existing = make_url(database_url).query.get("options")
    if isinstance(existing, tuple):  # ?options=a&options=b
        existing = " ".join(existing)
    options = f"{existing} {_SERVER_TCP}" if existing else _SERVER_TCP
    return {**_CLIENT_TCP, "options": options}


class LockBusy(Exception):
    """A timed-out lock call is still running on the lock connection; this
    tick must not touch the connection."""


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
        self._engine = create_engine(
            database_url,
            poolclass=NullPool,
            hide_parameters=True,
            connect_args=_lock_connect_args(database_url),
        )
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
    """Leader loop. Lock and heartbeat calls block, so they run in a thread,
    each bounded by `call_timeout`. A lock call that times out is left to
    finish in its thread (the lock connection is never used from two threads):
    the leader demotes at once, and the connection is released only after that
    call has returned."""

    def __init__(
        self,
        lock: LeaderLock,
        scheduler_factory: Callable | None = None,
        interval: float = SCHEDULER_LEADER_INTERVAL_SECONDS,
        startup_delay: float = 15,
        call_timeout: float = LOCK_CALL_TIMEOUT_SECONDS,
    ):
        self._lock = lock
        self._scheduler_factory = scheduler_factory or _default_scheduler_factory
        self._interval = interval
        self._startup_delay = startup_delay
        self._call_timeout = call_timeout
        self._scheduler = None
        self._inflight: asyncio.Future | None = None  # a timed-out lock call, still running
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    @property
    def is_leader(self) -> bool:
        return self._scheduler is not None

    async def tick(self) -> None:
        """One election step. Never raises: errors are logged and retried next tick."""
        try:
            if self._inflight is not None:
                if self._lock_busy():
                    return  # still stuck: leave the connection alone
                await self._release_quietly()  # a late acquire may hold the lock; start clean
                if self._lock_busy():
                    return  # that release timed out too: never queue a call behind it
            if self._scheduler is None:
                await self._follower_step()
            else:
                await self._leader_step()
        except LockBusy:
            logger.debug("scheduler leader: lock call still running, tick skipped")
        except Exception as e:  # noqa: BLE001
            logger.error("scheduler leader tick failed: %s", redact_secrets(e))

    async def _follower_step(self) -> None:
        if not await self._lock_call(self._lock.try_acquire):
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
        await self._stamp(last_started_at=datetime.now(timezone.utc))

    async def _leader_step(self) -> None:
        try:
            alive = await self._lock_call(self._lock.ping)
        except Exception as e:  # noqa: BLE001
            logger.warning("scheduler leader: lock ping failed: %s", redact_secrets(e))
            alive = False
        if not alive:
            self._shutdown_scheduler()
            logger.warning("scheduler leader: lost the lock, scheduler stopped")
            await self._release_quietly()  # drop the connection; reconnect as a follower
            return
        await self._stamp(last_success_at=datetime.now(timezone.utc))

    async def _stamp(self, **fields) -> None:
        # Own session; on timeout the abandoned thread can finish harmlessly.
        try:
            await asyncio.wait_for(
                asyncio.to_thread(heartbeat.upsert_heartbeat, LEADER_HEARTBEAT, **fields),
                timeout=self._call_timeout,
            )
        except TimeoutError:
            logger.error(
                "scheduler leader: heartbeat %s (%s) timed out after %ss",
                LEADER_HEARTBEAT, ", ".join(fields), self._call_timeout,
            )

    async def _lock_call(self, fn):
        """`fn()` in a thread, waiting at most call_timeout. On timeout the call
        keeps running and is recorded in `_inflight`, and TimeoutError is raised.
        The single gate to the lock: raises LockBusy instead of starting a call
        while a timed-out one still runs, so calls never overlap."""
        if self._lock_busy():
            raise LockBusy(fn.__name__)
        fut = asyncio.ensure_future(asyncio.to_thread(fn))
        self._inflight = fut
        done, _ = await asyncio.wait({fut}, timeout=self._call_timeout)
        if not done:
            raise TimeoutError(f"lock call {fn.__name__} took over {self._call_timeout}s")
        self._inflight = None
        return fut.result()

    def _lock_busy(self) -> bool:
        """True while a timed-out lock call still runs; once it has finished,
        forget it (its outcome was already handled as a timeout)."""
        fut = self._inflight
        if fut is None:
            return False
        if not fut.done():
            return True
        if not fut.cancelled():
            fut.exception()  # mark retrieved
        self._inflight = None
        return False

    def _shutdown_scheduler(self) -> None:
        scheduler, self._scheduler = self._scheduler, None
        if scheduler is None:
            return
        try:
            scheduler.shutdown(wait=False)
        except Exception as e:  # noqa: BLE001
            logger.error("scheduler shutdown failed: %s", redact_secrets(e))

    async def _release_quietly(self) -> None:
        if self._lock_busy():
            return  # released once the stuck call returns (next tick, or never if stopping)
        try:
            await self._lock_call(self._lock.release)
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
                task.cancel()
        self._shutdown_scheduler()
        if self._lock_busy():
            # A lock call is stuck on the lock connection; don't touch it from
            # another thread. The process is exiting and its closing socket
            # frees the lock.
            logger.warning("scheduler leader: lock call stuck at stop; lock left to the closing session")
            return
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
