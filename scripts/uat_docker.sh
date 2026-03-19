#!/usr/bin/env bash
# UAT script for S07: Docker Deployment
# Usage: bash scripts/uat_docker.sh
# Exit codes: 0 = all checks passed, 1 = a check failed
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
BASE_URL="http://localhost:8080"
MAX_WAIT=60   # seconds to wait for EMS health endpoint
LOG_WAIT=15   # seconds to wait for startup log message

fail() { echo "FAIL: $1" >&2; docker compose -f "$COMPOSE_FILE" down --timeout 10 2>/dev/null || true; exit 1; }
pass() { echo "PASS: $1"; }

echo "=== EMS Docker UAT ==="

# 1. Bring the stack up
echo "[1/6] Starting stack..."
docker compose -f "$COMPOSE_FILE" up -d || fail "docker compose up failed"
pass "stack started"

# 2. Wait for EMS to be reachable
echo "[2/6] Waiting for EMS to be ready (max ${MAX_WAIT}s)..."
deadline=$(( $(date +%s) + MAX_WAIT ))
while true; do
  if curl -sf --max-time 2 "${BASE_URL}/api/health" > /dev/null 2>&1; then
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    docker compose -f "$COMPOSE_FILE" logs ems | tail -20 >&2
    fail "EMS did not become ready within ${MAX_WAIT}s"
  fi
  sleep 2
done
# Give uvicorn a moment to finish writing startup logs before checking them
sleep 3
pass "EMS is reachable at ${BASE_URL}"

# 3. /api/health returns 200 with "status" key
echo "[3/6] Checking /api/health..."
health=$(curl -sf --max-time 5 "${BASE_URL}/api/health") || fail "/api/health request failed"
echo "$health" | grep -q '"status"' || fail "/api/health response missing 'status' key: $health"
pass "/api/health OK — $(echo "$health" | tr -d '\n')"

# 4. React SPA served at /
echo "[4/6] Checking React SPA at /..."
spa=$(curl -sf --max-time 5 "${BASE_URL}/") || fail "GET / request failed"
echo "$spa" | grep -qi "<title>" || fail "GET / did not return HTML with <title>: $(echo "$spa" | head -3)"
pass "React SPA served at /"

# 5. StaticFiles mounted in EMS logs (retry up to LOG_WAIT seconds)
echo "[5/6] Checking EMS logs for StaticFiles mount..."
log_deadline=$(( $(date +%s) + LOG_WAIT ))
while true; do
  if docker compose -f "$COMPOSE_FILE" logs ems 2>/dev/null | grep -q "StaticFiles mounted"; then
    break
  fi
  if [ "$(date +%s)" -ge "$log_deadline" ]; then
    echo "--- EMS logs ---" >&2
    docker compose -f "$COMPOSE_FILE" logs ems | tail -30 >&2
    fail "StaticFiles not mounted — check uvicorn WORKDIR or frontend/dist presence"
  fi
  sleep 2
done
pass "StaticFiles mounted confirmed in logs"

# 6. Tear down
echo "[6/6] Tearing down..."
docker compose -f "$COMPOSE_FILE" down --timeout 10 || fail "docker compose down failed"
pass "stack stopped cleanly"

echo ""
echo "=== All checks passed ==="
