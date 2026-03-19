"""Home Assistant REST API client with background polling.

Polls ``GET /api/states/<entity_id>`` every ``poll_interval_s`` seconds and
caches the last known float value.  All errors are swallowed — the client
never raises; callers receive ``None`` on error or before the first successful
poll.

Observability
-------------
- ``INFO  "HA REST poll: entity_id=<id> value=<float> W"``  — on each successful poll.
- ``WARNING "HA REST poll failed: <exc>"``                   — on each failed poll.
- Failure state exposed: ``get_cached_value()`` returns ``None`` until the
  first successful poll or after a persistent failure.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S = 10.0


class HomeAssistantClient:
    """Polls a single HA REST sensor entity and caches the last known value.

    Parameters
    ----------
    url:
        Base URL of the HA instance, e.g. ``http://homeassistant.local:8123``.
    token:
        Long-lived access token.  Never logged.
    entity_id:
        HA entity ID to poll, e.g. ``sensor.heat_pump_power``.
    poll_interval_s:
        Seconds between polls (default 30).
    """

    def __init__(
        self,
        url: str,
        token: str,
        entity_id: str,
        poll_interval_s: float = 30.0,
    ) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._entity_id = entity_id
        self._poll_interval_s = poll_interval_s
        self._cached_value: float | None = None
        self._task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background polling task."""
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "HA REST client started — entity_id=%s poll_interval=%ss",
            self._entity_id,
            self._poll_interval_s,
        )

    async def stop(self) -> None:
        """Cancel the background polling task cleanly."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("HA REST client stopped — entity_id=%s", self._entity_id)

    # ------------------------------------------------------------------
    # Public read interface
    # ------------------------------------------------------------------

    def get_cached_value(self) -> float | None:
        """Return the last successfully polled sensor value, or ``None``."""
        return self._cached_value

    # ------------------------------------------------------------------
    # HTTP layer (public so tests can call it directly)
    # ------------------------------------------------------------------

    async def get_sensor_value(self, entity_id: str) -> float | None:
        """Fetch the current state of *entity_id* from the HA REST API.

        Returns the numeric value in watts, or ``None`` on any error.
        Never raises.
        """
        endpoint = f"{self._url}/api/states/{entity_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                response = await client.get(endpoint, headers=headers)
                response.raise_for_status()
                data = response.json()
                return float(data["state"])
        except (
            httpx.HTTPError,
            httpx.ConnectError,
            KeyError,
            ValueError,
            Exception,
        ):
            return None

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Infinite polling loop — updates ``_cached_value`` every ``_poll_interval_s``."""
        try:
            while True:
                value = await self.get_sensor_value(self._entity_id)
                if value is not None:
                    self._cached_value = value
                    logger.info(
                        "HA REST poll: entity_id=%s value=%s W",
                        self._entity_id,
                        value,
                    )
                else:
                    logger.warning(
                        "HA REST poll failed: entity_id=%s returned None",
                        self._entity_id,
                    )
                await asyncio.sleep(self._poll_interval_s)
        except asyncio.CancelledError:
            raise
