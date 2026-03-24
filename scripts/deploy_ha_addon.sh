#!/usr/bin/env bash
# scripts/deploy_ha_addon.sh
#
# Deploys the EMS add-on to HAOS via the Samba share.
# Copies all source needed for the HAOS local add-on Docker build into the
# /addons/ems/ share, then triggers a supervisor reload + install/update.
#
# Prerequisites:
#   - Samba share mountable at //192.168.0.10/addons (credentials: homeassistant/homeassistant)
#   - frontend/dist must be built: cd frontend && npm run build
#   - HA_TOKEN set in .env (long-lived token with admin scope)
#
# Usage:
#   bash scripts/deploy_ha_addon.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MOUNT_POINT="/tmp/ha-mount-addons"
HA_HOST="192.168.0.10"
HA_URL="http://$HA_HOST:8123"
ADDON_SLUG="local_ems"

# Load HA_TOKEN from .env
HA_TOKEN=$(grep "^HA_TOKEN=" "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2)
if [[ -z "$HA_TOKEN" ]]; then
  echo "ERROR: HA_TOKEN not found in .env" >&2
  exit 1
fi

echo "=== EMS HA Add-on Deploy ==="

# 1. Build frontend
echo "[1/5] Building frontend..."
cd "$REPO_ROOT/frontend"
npm run build --silent
cd "$REPO_ROOT"

# 2. Mount Samba share
echo "[2/5] Mounting Samba share..."
mkdir -p "$MOUNT_POINT"
if ! mount | grep -q "$MOUNT_POINT"; then
  mount_smbfs "//homeassistant:homeassistant@$HA_HOST/addons" "$MOUNT_POINT"
fi

# 3. Copy add-on files
echo "[3/5] Copying add-on files..."
mkdir -p "$MOUNT_POINT/ems"
# Core add-on files
cp "$REPO_ROOT/ems/Dockerfile"       "$MOUNT_POINT/ems/"
cp "$REPO_ROOT/ems/config.yaml"      "$MOUNT_POINT/ems/"
cp "$REPO_ROOT/ems/build.yaml"       "$MOUNT_POINT/ems/"
cp "$REPO_ROOT/ems/run.sh"           "$MOUNT_POINT/ems/"
# Source required for build (additional_context in build.yaml)
cp "$REPO_ROOT/pyproject.toml"       "$MOUNT_POINT/ems/"
cp -r "$REPO_ROOT/backend"           "$MOUNT_POINT/ems/"
cp -r "$REPO_ROOT/frontend/dist"     "$MOUNT_POINT/ems/dist"
echo "    Files: $(ls "$MOUNT_POINT/ems/" | tr '\n' ' ')"

# 4. Reload supervisor store
echo "[4/5] Reloading supervisor store..."
python3 - <<PYEOF
import json, websocket, sys

token = "$HA_TOKEN"
ws = websocket.create_connection("ws://$HA_HOST:8123/api/websocket", timeout=30)
json.loads(ws.recv())
ws.send(json.dumps({"type": "auth", "access_token": token}))
r = json.loads(ws.recv())
if r["type"] != "auth_ok":
    print("Auth failed", r); sys.exit(1)

ws.send(json.dumps({"id": 1, "type": "supervisor/api", "endpoint": "/store/reload", "method": "post"}))
json.loads(ws.recv())

# Check installed state
ws.send(json.dumps({"id": 2, "type": "supervisor/api", "endpoint": "/store/addons/local_ems", "method": "get"}))
info = json.loads(ws.recv()).get("result", {})
print(f"    Add-on visible: {info.get('available', False)}, installed: {info.get('installed', False)}")
ws.close()
PYEOF

# 5. Install or update
echo "[5/5] Installing/updating add-on (Docker build on HAOS — may take 5-10 min)..."
python3 - <<PYEOF
import json, websocket, sys, time

token = "$HA_TOKEN"
ws = websocket.create_connection("ws://$HA_HOST:8123/api/websocket", timeout=30)
json.loads(ws.recv())
ws.send(json.dumps({"type": "auth", "access_token": token}))
json.loads(ws.recv())

# Check if installed
ws.send(json.dumps({"id": 1, "type": "supervisor/api", "endpoint": "/addons/local_ems/info", "method": "get"}))
r = json.loads(ws.recv())
installed = r.get("result") is not None and not r.get("error")

if installed:
    endpoint = "/addons/local_ems/update"
    action = "update"
else:
    endpoint = "/addons/local_ems/install"
    action = "install"

print(f"    Action: {action}")
ws.send(json.dumps({"id": 2, "type": "supervisor/api", "endpoint": endpoint, "method": "post"}))
r = json.loads(ws.recv())
if r.get("success"):
    print(f"    {action.capitalize()} succeeded.")
else:
    err = r.get("error", {})
    print(f"    {action.capitalize()} failed: {err.get('message', r)}", file=sys.stderr)
    sys.exit(1)
ws.close()
PYEOF

echo ""
echo "=== Deploy complete. Configure options in HA → Settings → Add-ons → EMS, then start. ==="
echo "    Health check: curl http://$HA_HOST:8000/api/health"
