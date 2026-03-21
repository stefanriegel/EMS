"""WebSocket connection manager for the EMS real-time push endpoint (S06).

``ConnectionManager`` tracks all active WebSocket clients and broadcasts JSON
payloads to them, removing dead clients silently on send failure.

Module-level singleton ``manager`` is imported by ``backend/api.py``.

Observability
-------------
* ``WARNING ws broadcast failed: client disconnected`` — logged whenever a
  client's send raises an exception; the dead client is removed immediately.
* The active-client set is bounded: a crashed client is dropped on the very
  next broadcast, so the set never grows unboundedly.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manage a set of active WebSocket connections.

    Thread-safety note: all operations are called from the same async event
    loop; no locking is required.
    """

    def __init__(self) -> None:
        self._active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        """Accept the WebSocket handshake and register the client."""
        await ws.accept()
        self._active.add(ws)
        logger.info("ws client connected; active=%d", len(self._active))

    def disconnect(self, ws: WebSocket) -> None:
        """Remove a client from the active set (idempotent)."""
        self._active.discard(ws)
        logger.info("ws client disconnected; active=%d", len(self._active))

    async def broadcast(self, data: dict[str, Any]) -> None:
        """Send *data* as JSON to every active client.

        Clients that raise on ``send_json`` are assumed dead and removed from
        the active set.  Errors are caught per-client so one failure does not
        interrupt delivery to the remaining clients.
        """
        dead: set[WebSocket] = set()
        for ws in list(self._active):
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
                logger.warning("ws broadcast failed: client disconnected")
        self._active -= dead


# Module-level singleton — imported by backend/api.py
manager = ConnectionManager()
