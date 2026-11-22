#!/bin/bash
# Deploy / update Navigator on the VPS.
# Run ON the server, from the repo root (default /opt/navigator):
#     cd /opt/navigator && ./scripts/deploy.sh
#
# TLS, basic auth and the public listener live at the shared edge Caddy
# (zivalx-ops). Navigator only joins the external `edge` network through
# navigator-nginx and publishes no ports. Idempotent: safe to re-run.
#
# Prerequisites: the zivalx-ops edge setup has run (network `edge` exists) and
# .env.prod exists (cp .env.prod.example .env.prod, fill real values).
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose --env-file .env.prod -f docker-compose.prod.yml"

# --- preflight -------------------------------------------------------------
[ -f .env.prod ] || { echo "ERROR: .env.prod missing. cp .env.prod.example .env.prod and fill it."; exit 1; }
grep -qx 'POSTGRES_DB=navigator' .env.prod \
    || { echo "ERROR: POSTGRES_DB must be 'navigator' in .env.prod"; exit 1; }
docker network inspect edge >/dev/null 2>&1 \
    || { echo "ERROR: docker network 'edge' not found: run the zivalx-ops edge setup first"; exit 1; }

# --- 1. pre-deploy dump (skipped only on a first deploy) --------------------
# A container that exists but is stopped holds data: refuse to deploy over it
# without a dump, instead of skipping the dump.
pg_state="$(docker inspect -f '{{.State.Running}}' navigator-postgres 2>/dev/null || true)"
if [ "$pg_state" = "false" ]; then
    echo "ERROR: navigator-postgres exists but is not running; start it (docker start navigator-postgres) so it can be dumped, then re-run"
    exit 1
fi
if [ "$pg_state" = "true" ]; then
    echo "### pre-deploy database dump"
    if [ -x /opt/zivalx-ops/backup/zbackup ]; then
        /opt/zivalx-ops/backup/zbackup dump navigator
    else
        BACKUP_DIR=/var/backups/navigator
        (umask 077; mkdir -p "$BACKUP_DIR")
        TS="$(date -u +%Y%m%dT%H%M%SZ)"
        OUT="$BACKUP_DIR/predeploy-$TS.dump"
        (
            umask 077
            docker exec navigator-postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$OUT"
        ) || { rm -f "$OUT"; echo "ERROR: pre-deploy dump failed"; exit 1; }
        echo "    wrote $OUT"
        # keep the newest 5
        ls -1t "$BACKUP_DIR"/predeploy-*.dump 2>/dev/null | tail -n +6 | while IFS= read -r old; do rm -f -- "$old"; done
    fi
else
    echo "### navigator-postgres does not exist: skipping pre-deploy dump (first deploy)"
fi

# --- 2. pull latest --------------------------------------------------------
echo "### git pull"
git pull --ff-only

# --- 3/4. build and start (the api entrypoint runs migrations) -------------
echo "### build"
$COMPOSE build
echo "### up"
$COMPOSE up -d

# --- 5. wait for the api to report healthy (<= 180 s) ----------------------
echo "### waiting for navigator-api to be healthy (up to 180 s)"
healthy=""
for _ in $(seq 36); do
    state="$(docker inspect -f '{{.State.Health.Status}}' navigator-api 2>/dev/null || true)"
    if [ "$state" = "healthy" ]; then healthy=1; break; fi
    sleep 5
done
[ -n "$healthy" ] || { echo "ERROR: navigator-api not healthy after 180 s: check: $COMPOSE logs api"; exit 1; }

# --- 6. deep health status (status field only) -----------------------------
echo "### /health/deep status"
docker exec navigator-api curl -sS http://localhost:7000/health/deep \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", "unknown"))' \
    || echo "    (could not read /health/deep)"

# --- 7. nothing may be published on the host -------------------------------
if [ -n "$(docker port navigator-nginx 2>/dev/null)" ]; then
    echo "ERROR: navigator-nginx publishes host ports; it must only join the edge network"
    exit 1
fi
echo "=== Done. navigator-nginx is reachable on the edge network only. ==="
echo "Logs: $COMPOSE logs -f"
