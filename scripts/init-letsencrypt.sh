#!/bin/bash
# First-time Let's Encrypt SSL certificate setup
# Based on the well-known init-letsencrypt.sh pattern
set -e

# Load environment
if [ ! -f .env.prod ]; then
    echo "Error: .env.prod not found. Copy .env.prod.example and fill in your values."
    exit 1
fi

set -a
. ./.env.prod
set +a

if [ -z "$DOMAIN" ]; then
    echo "Error: DOMAIN is not set in .env.prod"
    exit 1
fi

if [ -z "$CERTBOT_EMAIL" ]; then
    echo "Error: CERTBOT_EMAIL is not set in .env.prod"
    exit 1
fi

RSA_KEY_SIZE=4096
DATA_PATH="./certbot"
STAGING=0  # Set to 1 to test against Let's Encrypt staging environment

echo "### Setting up SSL for $DOMAIN ..."

# Create required directories
mkdir -p "$DATA_PATH/conf/live/$DOMAIN"
mkdir -p "$DATA_PATH/www"

# Download recommended TLS parameters
if [ ! -e "$DATA_PATH/conf/options-ssl-nginx.conf" ]; then
    echo "### Downloading recommended TLS parameters ..."
    mkdir -p "$DATA_PATH/conf"
    curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
        > "$DATA_PATH/conf/options-ssl-nginx.conf"
    curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot/certbot/ssl-dhparams.pem \
        > "$DATA_PATH/conf/ssl-dhparams.pem"
fi

# Create dummy certificate so nginx can start
echo "### Creating dummy certificate for $DOMAIN ..."
LIVE_PATH="/etc/letsencrypt/live/$DOMAIN"
docker compose -f docker-compose.prod.yml run --rm --entrypoint "\
    openssl req -x509 -nodes -newkey rsa:$RSA_KEY_SIZE -days 1 \
    -keyout '$LIVE_PATH/privkey.pem' \
    -out '$LIVE_PATH/fullchain.pem' \
    -subj '/CN=localhost'" certbot
echo

# Start nginx with the dummy certificate
echo "### Starting nginx ..."
docker compose -f docker-compose.prod.yml up --force-recreate -d nginx
echo

# Delete dummy certificate
echo "### Deleting dummy certificate ..."
docker compose -f docker-compose.prod.yml run --rm --entrypoint "\
    rm -rf /etc/letsencrypt/live/$DOMAIN && \
    rm -rf /etc/letsencrypt/archive/$DOMAIN && \
    rm -rf /etc/letsencrypt/renewal/$DOMAIN.conf" certbot
echo

# Request real certificate
echo "### Requesting Let's Encrypt certificate for $DOMAIN ..."

# Select staging or production server
if [ $STAGING != "0" ]; then
    STAGING_ARG="--staging"
else
    STAGING_ARG=""
fi

docker compose -f docker-compose.prod.yml run --rm --entrypoint "\
    certbot certonly --webroot -w /var/www/certbot \
    $STAGING_ARG \
    --email $CERTBOT_EMAIL \
    --domain $DOMAIN \
    --rsa-key-size $RSA_KEY_SIZE \
    --agree-tos \
    --no-eff-email \
    --force-renewal" certbot
echo

# Reload nginx with the real certificate
echo "### Reloading nginx ..."
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload

echo "### Done! SSL certificate installed for $DOMAIN"
