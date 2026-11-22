#!/bin/bash
# Provision the Navigator VPS on Hetzner Cloud (Phase 2 of TODO-DEPLOYMENT.md).
#
# Prerequisites:
#   - hcloud CLI with an active context: `hcloud context create navigator`
#   - SSH keypair at ~/.ssh/id_ed25519(.pub)
#
# Usage:
#   ./scripts/provision.sh                      # cx22 in Falkenstein (EU)
#   LOCATION=ash SERVER_TYPE=cpx21 ./scripts/provision.sh   # Ashburn (US) — CX types are EU-only
# NOTE: host provisioning and the 80/443 firewall now belong to zivalx-ops; this script is superseded.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SERVER_NAME="${SERVER_NAME:-navigator}"
SERVER_TYPE="${SERVER_TYPE:-cx22}"     # 2 vCPU / 4 GB / 40 GB
LOCATION="${LOCATION:-fsn1}"           # fsn1=Falkenstein, nbg1=Nuremberg, hel1=Helsinki, ash=Ashburn US
IMAGE="${IMAGE:-ubuntu-24.04}"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}"
SSH_KEY_NAME="navigator-key"
FIREWALL_NAME="navigator-fw"

echo "=== Provisioning $SERVER_NAME ($SERVER_TYPE, $LOCATION) ==="

if ! hcloud context active > /dev/null 2>&1; then
    echo "Error: no active hcloud context. Run: hcloud context create navigator"
    exit 1
fi

if hcloud server describe "$SERVER_NAME" > /dev/null 2>&1; then
    echo "Error: server '$SERVER_NAME' already exists:"
    hcloud server describe "$SERVER_NAME" -o format='{{.PublicNet.IPv4.IP}}'
    exit 1
fi

# SSH key (idempotent)
if ! hcloud ssh-key describe "$SSH_KEY_NAME" > /dev/null 2>&1; then
    echo "### Uploading SSH key from $SSH_KEY_FILE ..."
    hcloud ssh-key create --name "$SSH_KEY_NAME" --public-key-from-file "$SSH_KEY_FILE"
fi

# Cloud firewall in front of the server (ufw runs on the host as a second layer)
if ! hcloud firewall describe "$FIREWALL_NAME" > /dev/null 2>&1; then
    echo "### Creating firewall $FIREWALL_NAME (22, 80, 443 in) ..."
    hcloud firewall create --name "$FIREWALL_NAME"
    for port in 22 80 443; do
        hcloud firewall add-rule "$FIREWALL_NAME" --direction in --protocol tcp \
            --port "$port" --source-ips 0.0.0.0/0 --source-ips ::/0
    done
    hcloud firewall add-rule "$FIREWALL_NAME" --direction in --protocol icmp \
        --source-ips 0.0.0.0/0 --source-ips ::/0
fi

echo "### Creating server ..."
hcloud server create \
    --name "$SERVER_NAME" \
    --type "$SERVER_TYPE" \
    --location "$LOCATION" \
    --image "$IMAGE" \
    --ssh-key "$SSH_KEY_NAME" \
    --firewall "$FIREWALL_NAME" \
    --user-data-from-file "$SCRIPT_DIR/cloud-init.yml"

IP="$(hcloud server ip "$SERVER_NAME")"

echo ""
echo "=== Server created: $IP ==="
echo "cloud-init is now hardening the box and installing Docker (~2-4 min)."
echo ""
echo "Watch progress:   ssh root@$IP 'tail -f /var/log/cloud-init-output.log'"
echo "When done:        ssh root@$IP 'cloud-init status'   # expect: done"
echo ""
echo "Next (Phase 2.3):"
echo "  1. scp .env.prod root@$IP:/opt/navigator/.env.prod"
echo "  2. ssh root@$IP '/opt/navigator/scripts/deploy.sh'"
echo "  3. Point DNS: navigator.zivalx.com -> $IP (Phase 3)"
