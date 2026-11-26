#!/bin/sh
# Migrate to the code's Alembic head before the app starts (and before
# gunicorn forks its workers). A failed migration exits the container.
set -e
python -m app.migrate
exec "$@"
