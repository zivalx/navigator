"""build_scheduler(): job set, defaults and misfire values, without running jobs.

APScheduler 3.10 applies job_defaults and computes next_run_time only when a
job is added to a started scheduler, so these tests start it paused (no job
runs) and shut it down afterwards.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.tasks import scheduler as scheduler_module
from app.tasks.scheduler import build_scheduler


@contextmanager
def paused(startup_at=None):
    sched = build_scheduler(startup_at=startup_at)
    sched.start(paused=True)
    try:
        yield sched
    finally:
        sched.shutdown(wait=False)


@pytest.mark.asyncio
async def test_jobs_have_defaults_and_misfire():
    with paused(datetime.now(timezone.utc) + timedelta(hours=1)) as sched:
        jobs = sched.get_jobs()
    assert all(j.coalesce and j.max_instances == 1 for j in jobs)
    misfire = {j.id: j.misfire_grace_time for j in jobs}
    assert misfire.items() >= {
        "eod_snapshot": 3600,
        "breadth_snapshot": 3600,
        "price_alert_evaluation": 120,
        "leader_startup": 300,
    }.items()


def test_scheduler_runs_in_utc():
    assert str(build_scheduler().timezone) == "UTC"


def test_cron_jobs_use_new_york():
    cron = [j for j in build_scheduler().get_jobs() if isinstance(j.trigger, CronTrigger)]
    assert {j.id for j in cron} >= {"eod_snapshot", "breadth_snapshot"}
    for job in cron:
        assert str(job.trigger.timezone) == "America/New_York"


@pytest.mark.asyncio
async def test_leader_startup_scheduled_at_startup_at():
    startup_at = datetime.now(timezone.utc) + timedelta(seconds=15)
    with paused(startup_at) as sched:
        job = sched.get_job("leader_startup")
        assert job.next_run_time == startup_at and job.misfire_grace_time == 300


def test_no_leader_startup_without_startup_at():
    ids = {j.id for j in build_scheduler().get_jobs()}
    assert "leader_startup" not in ids
    # the old staggered startup jobs are gone
    assert not ids & {"backfill_historical", "breadth_warm_start", "backfill_daily_pnl"}


@pytest.mark.asyncio
async def test_leader_startup_runs_each_step_in_order_despite_failures(monkeypatch):
    calls = []

    def step(name, fail=False):
        async def run():
            calls.append(name)
            if fail:
                raise RuntimeError(f"{name} broke")
        return run

    monkeypatch.setattr(scheduler_module, "backfill_historical_prices", step("prices", fail=True))
    monkeypatch.setattr(scheduler_module, "backfill_daily_snapshots_job", step("daily_pnl", fail=True))
    monkeypatch.setattr(scheduler_module.breadth_service, "ensure_breadth_warm", step("breadth"))
    await scheduler_module.leader_startup()
    assert calls == ["prices", "daily_pnl", "breadth"]
