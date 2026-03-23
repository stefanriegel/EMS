"""Home Assistant Ingress ASGI middleware.

When the EMS add-on is accessed through the HA Ingress proxy, the Supervisor
injects an ``X-Ingress-Path`` header containing the base path (e.g.
``/api/hassio_ingress/<token>``).  This middleware reads that header and sets
``scope["root_path"]`` so that FastAPI generates correct URLs for redirects,
OpenAPI docs, and WebSocket endpoints.

The middleware is implemented as a raw ASGI wrapper (not BaseHTTPMiddleware)
for two reasons:

1. **WebSocket support** -- ``BaseHTTPMiddleware`` does not handle WebSocket
   scope types.  This middleware must work for both HTTP and WS connections.
2. **Performance** -- raw ASGI avoids the per-request overhead of wrapping
   the request/response cycle in a ``Request`` object.

When the header is absent (direct port access), the scope is left unchanged.
"""
from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send


class IngressMiddleware:
    """Set ``root_path`` from the ``X-Ingress-Path`` header for HA Ingress."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = scope.get("headers", [])
            for name, value in headers:
                if name == b"x-ingress-path":
                    scope["root_path"] = value.decode("latin-1").rstrip("/")
                    break
        await self.app(scope, receive, send)
