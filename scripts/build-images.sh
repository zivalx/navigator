#!/bin/sh
# Local image build: navigator-api:local (backend/Dockerfile.prod) and navigator-frontend:local.
# Behind a TLS-inspecting proxy (local only): CORP_CA=/path/to/root-ca.pem passes it as the BuildKit
# secret `corp_ca` (never copied into an image). Unset => the plain build. deploy.sh does not use this.
set -eu
cd "$(dirname "$0")/.."
# RUN --mount (the corp_ca secret) requires BuildKit.
export DOCKER_BUILDKIT=1
if [ -n "${CORP_CA:-}" ]; then
  docker build --secret id=corp_ca,src="$CORP_CA" -f backend/Dockerfile.prod -t navigator-api:local backend
  docker build --secret id=corp_ca,src="$CORP_CA" -t navigator-frontend:local frontend
else
  docker build -f backend/Dockerfile.prod -t navigator-api:local backend
  docker build -t navigator-frontend:local frontend
fi
