# Phase 15: HA Ingress Support - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Add HA Ingress support so the EMS dashboard is accessible from the HA sidebar. Implement ASGI middleware for X-Ingress-Path handling, auth bypass for Ingress requests, frontend relative paths, and dynamic WebSocket URL construction. Both direct port access (8000) and Ingress access work simultaneously.

</domain>

<decisions>
## Implementation Decisions

### Ingress Architecture
- ASGI middleware (~40 lines) reads X-Ingress-Path header, sets scope["root_path"]. FastAPI handles the rest natively
- Auth bypass: check for X-Ingress-Path header presence — if header exists, skip JWT validation. HA authenticates before forwarding
- Frontend: Vite base: './' for relative asset paths. Dynamic WebSocket URL from new URL('./api/ws/state', window.location.href)
- Dual access: port 8000 direct with JWT auth, Ingress via HA sidebar without JWT. Same app, same files

### Add-on Config & WebSocket
- config.yaml: ingress: true, ingress_port: 8099, panel_icon: mdi:battery-charging, panel_title: EMS
- WebSocket: trust HA Supervisor proxy. Construct URL dynamically from window.location. HTTP polling fallback in useEmsState.ts kicks in automatically if WS fails
- API calls: all use relative paths (fetch('./api/state') not fetch('/api/state'))
- Static files: keep FastAPI StaticFiles mount as-is. Vite base: './' makes all asset references relative

### Claude's Discretion
- Exact middleware file location (backend/ingress.py or inline in main.py)
- Whether to add ingress_port: 8099 as separate listen or reuse port 8000 with Supervisor proxy
- Error handling if X-Ingress-Path header has unexpected format
- Whether login page needs Ingress-awareness (probably not — Ingress bypasses auth entirely)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- backend/auth.py — AuthMiddleware with JWT validation (needs Ingress bypass)
- backend/main.py — FastAPI app with StaticFiles mount and lifespan
- frontend/src/hooks/useEmsSocket.ts — WebSocket connection with polling fallback
- frontend/src/hooks/useEmsState.ts — State management with WS/HTTP dual path
- frontend/vite.config.ts — Vite build configuration

### Established Patterns
- AuthMiddleware exempts certain paths (was /api/setup/*, now cleaned up in Phase 12)
- FastAPI StaticFiles mount serves frontend from /static or root
- useEmsSocket constructs ws:// URL — needs to become relative/dynamic
- Vite dev proxy forwards /api/ws/state to backend

### Integration Points
- ha-addon/config.yaml — add ingress fields
- backend/main.py — add IngressMiddleware before AuthMiddleware
- backend/auth.py — add X-Ingress-Path bypass in AuthMiddleware
- frontend/vite.config.ts — set base: './'
- frontend/src/hooks/useEmsSocket.ts — dynamic WS URL construction
- Any hardcoded /api/ paths in frontend components

</code_context>

<specifics>
## Specific Ideas

No specific requirements beyond research findings. Follow HA developer docs for Ingress configuration.

</specifics>

<deferred>
## Deferred Ideas

- Ingress-specific error page if Supervisor proxy fails
- Dark mode sync with HA theme (v1.3)

</deferred>
