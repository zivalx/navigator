"""Postgres advisory-lock keys owned by Navigator (one place, never reused).

7400419 / 7400420 belong to trender; don't take them here.
"""

MIGRATION_LOCK_KEY = 7400417  # python -m app.migrate and alembic/env.py
SCHEDULER_LOCK_KEY = 7400418  # scheduler leader election
