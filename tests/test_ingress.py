"""Tests for HA Ingress ASGI middleware."""
from __future__ import annotations

import pytest

from backend.ingress import IngressMiddleware


async def _make_scope(path: str, headers: list[tuple[bytes, bytes]] | None = None):
    """Build a minimal HTTP ASGI scope."""
    return {
        "type": "http",
        "path": path,
        "root_path": "",
        "headers": headers or [],
    }


@pytest.mark.anyio
async def test_double_slash_normalised():
    """//api/state should be normalised to /api/state."""
    captured = {}

    async def app(scope, receive, send):
        captured["path"] = scope["path"]

    mw = IngressMiddleware(app)
    scope = await _make_scope("//api/state")
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert captured["path"] == "/api/state"


@pytest.mark.anyio
async def test_triple_slash_normalised():
    """///api/state should be normalised to /api/state."""
    captured = {}

    async def app(scope, receive, send):
        captured["path"] = scope["path"]

    mw = IngressMiddleware(app)
    scope = await _make_scope("///api/state")
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert captured["path"] == "/api/state"


@pytest.mark.anyio
async def test_single_slash_unchanged():
    """Normal /api/state should pass through unchanged."""
    captured = {}

    async def app(scope, receive, send):
        captured["path"] = scope["path"]

    mw = IngressMiddleware(app)
    scope = await _make_scope("/api/state")
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert captured["path"] == "/api/state"


@pytest.mark.anyio
async def test_root_double_slash_normalised():
    """// should be normalised to /."""
    captured = {}

    async def app(scope, receive, send):
        captured["path"] = scope["path"]

    mw = IngressMiddleware(app)
    scope = await _make_scope("//")
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert captured["path"] == "/"


@pytest.mark.anyio
async def test_ingress_header_sets_root_path():
    """X-Ingress-Path header should set root_path."""
    captured = {}

    async def app(scope, receive, send):
        captured["root_path"] = scope["root_path"]

    mw = IngressMiddleware(app)
    scope = await _make_scope(
        "/api/state",
        headers=[(b"x-ingress-path", b"/api/hassio_ingress/abc123/")],
    )
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert captured["root_path"] == "/api/hassio_ingress/abc123"


@pytest.mark.anyio
async def test_ingress_header_and_double_slash():
    """Both X-Ingress-Path and double-slash normalisation should work together."""
    captured = {}

    async def app(scope, receive, send):
        captured["root_path"] = scope["root_path"]
        captured["path"] = scope["path"]

    mw = IngressMiddleware(app)
    scope = await _make_scope(
        "//api/state",
        headers=[(b"x-ingress-path", b"/api/hassio_ingress/abc123")],
    )
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert captured["root_path"] == "/api/hassio_ingress/abc123"
    assert captured["path"] == "/api/state"


@pytest.mark.anyio
async def test_websocket_scope_normalised():
    """WebSocket scope paths should also be normalised."""
    captured = {}

    async def app(scope, receive, send):
        captured["path"] = scope["path"]

    mw = IngressMiddleware(app)
    scope = {
        "type": "websocket",
        "path": "//api/ws/state",
        "root_path": "",
        "headers": [],
    }
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert captured["path"] == "/api/ws/state"
