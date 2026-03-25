"""Home Assistant Ingress ASGI middleware.

When the EMS add-on is accessed through the HA Ingress proxy, the Supervisor
injects an ``X-Ingress-Path`` header containing the base path (e.g.
``/api/hassio_ingress/<token>``).  This middleware reads that header and sets
``scope["root_path"]`` so that FastAPI generates correct URLs for redirects,
OpenAPI docs, and WebSocket endpoints.

The middleware also normalises the request path by collapsing consecutive
slashes (``//api/state`` → ``/api/state``).  The HA Ingress proxy concatenates
its ``ingress_entry`` (``/``) with the remainder of the URL path, which
produces double-slash paths.  Starlette's ``StaticFiles`` normalises
internally (so HTML and assets still load), but FastAPI's ``APIRouter``
performs strict matching and rejects paths with extra slashes.

The middleware is implemented as a raw ASGI wrapper (not BaseHTTPMiddleware)
for two reasons:

1. **WebSocket support** -- ``BaseHTTPMiddleware`` does not handle WebSocket
   scope types.  This middleware must work for both HTTP and WS connections.
2. **Performance** -- raw ASGI avoids the per-request overhead of wrapping
   the request/response cycle in a ``Request`` object.

When the header is absent (direct port access), the scope is left unchanged
apart from path normalisation.
"""
from __future__ import annotations

import re

from starlette.types import ASGIApp, Receive, Scope, Send

_MULTI_SLASH = re.compile(r"//+")


class IngressMiddleware:
    """Set ``root_path`` from ``X-Ingress-Path`` and normalise request paths."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = scope.get("headers", [])
            for name, value in headers:
                if name == b"x-ingress-path":
                    scope["root_path"] = value.decode("latin-1").rstrip("/")
                    break
            # Collapse consecutive slashes so //api/state → /api/state.
            path = scope.get("path", "")
            if "//" in path:
                scope["path"] = _MULTI_SLASH.sub("/", path)
        await self.app(scope, receive, send)
