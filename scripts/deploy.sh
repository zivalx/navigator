#!/bin/bash
# Production deployment script for Navigator
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Navigator Production Deployment ==="
echo "Working directory: $PROJECT_DIR"

# Check for .env.prod
if [ ! -f .env.prod ]; then
    echo "Error: .env.prod not found. Copy .env.prod.example and fill in your values."
    exit 1
fi

# Pull latest code
echo ""
echo "### Pulling latest code ..."
git pull origin main

# Build all containers
echo ""
echo "### Building containers ..."
docker compose -f docker-compose.prod.yml build

# Start services
echo ""
echo "### Starting services ..."
docker compose -f docker-compose.prod.yml up -d

# Wait for API health
echo ""
echo "### Waiting for API to become healthy ..."
MAX_RETRIES=30
RETRY_COUNT=0
until docker compose -f docker-compose.prod.yml exec -T api curl -sf http://localhost:7000/health > /dev/null 2>&1; do
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "Error: API failed to become healthy after ${MAX_RETRIES} attempts"
        echo "Check logs: docker compose -f docker-compose.prod.yml logs api"
        exit 1
    fi
    echo "  Waiting... ($RETRY_COUNT/$MAX_RETRIES)"
    sleep 2
done
echo "API is healthy."

# Print status
echo ""
echo "### Service Status ==="
docker compose -f docker-compose.prod.yml ps

echo ""
echo "=== Deployment complete ==="
