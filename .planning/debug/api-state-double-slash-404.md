---
status: awaiting_human_verify
trigger: "Frontend requests //api/state (double slash) which returns 404"
created: 2026-03-25T00:00:00Z
updated: 2026-03-25T00:00:00Z
---

## Current Focus

hypothesis: CONFIRMED - HA Ingress proxy concatenates ingress_entry (/) with the remaining request path (/api/state), producing //api/state. StaticFiles normalizes this for HTML/assets but FastAPI router does strict matching.
test: Add path normalization to IngressMiddleware to collapse // into /
expecting: All requests will have normalized single-slash paths
next_action: Apply fix to backend/ingress.py

## Symptoms

expected: GET /api/state should return 200 with EMS state data
actual: GET //api/state returns 404 — double slash in path
errors: HTTP 404 Not Found for //api/state
reproduction: Load frontend UI — immediately requests //api/state and gets 404. GET /api/health (single slash) works fine.
started: Current issue, every page load

## Eliminated

## Evidence

- timestamp: 2026-03-25T00:01:00Z
  checked: frontend fetch URLs
  found: useEmsState uses fetch("./api/state") and App.tsx uses fetch("./api/state") — relative URLs
  implication: Frontend is NOT adding a leading slash; the double slash comes from the server side

- timestamp: 2026-03-25T00:02:00Z
  checked: Vite config and built HTML
  found: base: './' in vite.config.ts, built index.html uses ./assets/... and ./favicon.svg
  implication: All frontend references are relative, correct for ingress

- timestamp: 2026-03-25T00:03:00Z
  checked: ems/config.yaml ingress settings
  found: ingress: true, ingress_port: 8000, ingress_entry: /
  implication: HA Ingress proxy concatenates ingress_entry (/) + request remainder (/api/state) = //api/state

- timestamp: 2026-03-25T00:04:00Z
  checked: backend/ingress.py IngressMiddleware
  found: Only sets scope["root_path"] from X-Ingress-Path header. Does NOT normalize the request path.
  implication: Double-slash paths pass through to FastAPI router unchanged

- timestamp: 2026-03-25T00:05:00Z
  checked: Log pattern analysis
  found: StaticFiles serves // and //assets/... with 200 (normalizes internally). FastAPI router returns 404 for //api/state (strict path match). /api/health returns 200 (likely from health checker, not browser).
  implication: The fix must normalize paths before they reach the FastAPI router

## Resolution

root_cause: HA Ingress proxy concatenates ingress_entry (/) with the request path remainder (/api/state), producing //api/state. The IngressMiddleware only sets root_path but does not normalize the double slashes. StaticFiles internally normalizes paths (so HTML/assets load), but FastAPI's APIRouter does strict path matching and //api/state does not match /api/state.
fix: Add path normalization to IngressMiddleware to collapse consecutive slashes into single slashes in scope["path"]
verification: 14 dedicated ingress tests pass (double/triple slash normalisation, WebSocket, combined with X-Ingress-Path). Full test suite: 1725 passed, 21 skipped, 0 failures.
files_changed: [backend/ingress.py, tests/test_ingress.py]
