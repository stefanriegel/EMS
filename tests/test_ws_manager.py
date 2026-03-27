"""Tests for backend/ws_manager.py — ConnectionManager."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_ws() -> AsyncMock:
    """Return an AsyncMock that looks like a FastAPI WebSocket."""
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


class TestConnectionManagerConnect:
    @pytest.mark.anyio
    async def test_connect_accepts_and_registers(self):
        from backend.ws_manager import ConnectionManager
        mgr = ConnectionManager()
        ws = _make_ws()
        await mgr.connect(ws)
        ws.accept.assert_awaited_once()
        assert ws in mgr._active


class TestConnectionManagerDisconnect:
    @pytest.mark.anyio
    async def test_disconnect_removes_client(self):
        from backend.ws_manager import ConnectionManager
        mgr = ConnectionManager()
        ws = _make_ws()
        await mgr.connect(ws)
        mgr.disconnect(ws)
        assert ws not in mgr._active

    def test_disconnect_idempotent_on_unknown(self):
        from backend.ws_manager import ConnectionManager
        mgr = ConnectionManager()
        ws = _make_ws()
        # Should not raise even if ws was never connected
        mgr.disconnect(ws)


class TestConnectionManagerBroadcast:
    @pytest.mark.anyio
    async def test_broadcast_calls_send_json_on_all_clients(self):
        from backend.ws_manager import ConnectionManager
        mgr = ConnectionManager()
        ws1, ws2 = _make_ws(), _make_ws()
        await mgr.connect(ws1)
        await mgr.connect(ws2)
        await mgr.broadcast({"key": "value"})
        ws1.send_json.assert_awaited_once_with({"key": "value"})
        ws2.send_json.assert_awaited_once_with({"key": "value"})

    @pytest.mark.anyio
    async def test_broadcast_removes_dead_client_continues_to_others(self):
        from backend.ws_manager import ConnectionManager
        mgr = ConnectionManager()
        dead_ws = _make_ws()
        dead_ws.send_json.side_effect = RuntimeError("connection closed")
        live_ws = _make_ws()
        await mgr.connect(dead_ws)
        await mgr.connect(live_ws)
        # Should not raise
        await mgr.broadcast({"x": 1})
        assert dead_ws not in mgr._active
        assert live_ws in mgr._active
        live_ws.send_json.assert_awaited_once_with({"x": 1})

    @pytest.mark.anyio
    async def test_broadcast_does_not_crash_on_empty_set(self):
        from backend.ws_manager import ConnectionManager
        mgr = ConnectionManager()
        # Must not raise
        await mgr.broadcast({"ping": True})
