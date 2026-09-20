#!/bin/bash
# Deploy / update Navigator on the VPS (Phase 2.3 + 3 of TODO-DEPLOYMENT.md).
# Run ON the server, from the repo root (default /opt/navigator):
#     cd /opt/navigator && ./scripts/deploy.sh
#
# Idempotent: safe to re-run for updates. On first run it bootstraps a
# Let's Encrypt certificate (temp self-signed -> start nginx -> certbot issues
# the real cert -> reload). On later runs it just pulls and redeploys.
#
# Prerequisites on the box (handled by cloud-init.yml): Docker + compose,
# ufw (22/80/443), repo cloned. You must create two secret files first:
#     .env.prod          (cp .env.prod.example .env.prod, fill real values)
#     nginx/.htpasswd    (htpasswd -B -c nginx/.htpasswd <user>)
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose --env-file .env.prod -f docker-compose.prod.yml"

# --- preflight -------------------------------------------------------------
[ -f .env.prod ] || { echo "ERROR: .env.prod missing. cp .env.prod.example .env.prod and fill it."; exit 1; }
[ -s nginx/.htpasswd ] || { echo "ERROR: nginx/.htpasswd missing/empty. Run: htpasswd -B -c nginx/.htpasswd <user>"; exit 1; }

# Load DOMAIN + CERTBOT_EMAIL from .env.prod (only those two, safely).
DOMAIN="$(grep -E '^DOMAIN=' .env.prod | cut -d= -f2-)"
CERTBOT_EMAIL="$(grep -E '^CERTBOT_EMAIL=' .env.prod | cut -d= -f2-)"
[ -n "$DOMAIN" ] || { echo "ERROR: DOMAIN not set in .env.prod"; exit 1; }
[ -n "$CERTBOT_EMAIL" ] || { echo "ERROR: CERTBOT_EMAIL not set in .env.prod"; exit 1; }
echo "=== Deploying Navigator for $DOMAIN ==="

# --- pull latest -----------------------------------------------------------
echo "### git pull"
git pull --ff-only

# Docker volume names are <project>_<vol>; project defaults to the dir name.
PROJECT="$(basename "$(pwd)")"
CONF_VOL="${PROJECT}_certbot_conf"

cert_exists() {
    docker run --rm -v "${CONF_VOL}:/etc/letsencrypt" alpine \
        test -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" 2>/dev/null
}

if cert_exists; then
    # --- routine update path -----------------------------------------------
    echo "### Real certificate present — building and (re)starting the stack"
    $COMPOSE up -d --build
else
    # --- first-time SSL bootstrap ------------------------------------------
    echo "### No certificate yet — bootstrapping Let's Encrypt"
    echo "    NOTE: the domain must already point at this server, and (if using"
    echo "    Cloudflare) be set to DNS-only (grey cloud) so the HTTP-01"
    echo "    challenge on port 80 reaches the origin. Flip to proxied after."

    echo "### 1/4 seeding a temporary self-signed cert so nginx can start"
    docker run --rm -v "${CONF_VOL}:/etc/letsencrypt" --entrypoint sh alpine/openssl -c "
        mkdir -p /etc/letsencrypt/live/${DOMAIN} &&
        openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
          -keyout /etc/letsencrypt/live/${DOMAIN}/privkey.pem \
          -out /etc/letsencrypt/live/${DOMAIN}/fullchain.pem \
          -subj '/CN=${DOMAIN}'"

    echo "### 2/4 starting the stack (nginx now serves the ACME challenge on :80)"
    $COMPOSE up -d --build

    echo "### 3/4 requesting the real certificate via certbot webroot"
    # Remove the self-signed dir so certbot writes a clean live/${DOMAIN}.
    docker run --rm -v "${CONF_VOL}:/etc/letsencrypt" alpine \
        rm -rf "/etc/letsencrypt/live/${DOMAIN}" "/etc/letsencrypt/archive/${DOMAIN}" \
               "/etc/letsencrypt/renewal/${DOMAIN}.conf"
    $COMPOSE run --rm --entrypoint certbot certbot certonly \
        --webroot -w /var/www/certbot \
        -d "${DOMAIN}" \
        --email "${CERTBOT_EMAIL}" --agree-tos --no-eff-email --non-interactive

    echo "### 4/4 reloading nginx with the real certificate"
    $COMPOSE exec nginx nginx -s reload
fi

echo ""
echo "=== Done. Health check: ==="
sleep 3
curl -fsS "https://${DOMAIN}/health" && echo "  ^ /health OK" || echo "  /health not reachable yet — check: $COMPOSE logs nginx"
echo ""
echo "Visit https://${DOMAIN}/ — you'll get the Basic Auth login prompt."
echo "Logs:   $COMPOSE logs -f"
