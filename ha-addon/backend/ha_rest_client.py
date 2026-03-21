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
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
# Converters — module-level so tests can import them
# ---------------------------------------------------------------------------


def _float_converter(state: str) -> float | None:
    """Convert a HA entity state string to float, or None on failure."""
    try:
        return float(state)
    except (ValueError, TypeError):
        return None


def _str_converter(state: str) -> str | None:
    """Pass-through converter — returns the raw state string."""
    if state is None:
        return None
    return str(state)


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


class MultiEntityHaClient:
    """Polls multiple HA REST sensor entities concurrently and caches values.

    Parameters
    ----------
    url:
        Base URL of the HA instance, e.g. ``http://homeassistant.local:8123``.
    token:
        Long-lived access token.  Never logged.
    entity_map:
        ``dict[field_name, (entity_id, converter)]`` where *converter* is a
        callable ``(str) -> float | str | None``.
    poll_interval_s:
        Seconds between poll rounds (default 30).
    """

    def __init__(
        self,
        url: str,
        token: str,
        entity_map: dict[str, tuple[str, Callable]],
        poll_interval_s: float = 30.0,
    ) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._entity_map: dict[str, tuple[str, Callable]] = dict(entity_map)
        self._poll_interval_s = poll_interval_s
        self._cache: dict[str, float | str | None] = {
            field: None for field in entity_map
        }
        self._task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Expand wildcards and start the background polling task."""
        await self._expand_wildcards()
        # Re-initialize cache with expanded entity map
        self._cache = {field: None for field in self._entity_map}
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "HA REST multi-entity client started — %d entities, poll_interval=%ss",
            len(self._entity_map),
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
            logger.info("HA REST multi-entity client stopped")

    # ------------------------------------------------------------------
    # Public read interface
    # ------------------------------------------------------------------

    def get_entity_value(self, field: str) -> float | str | None:
        """Return the cached value for *field*, or ``None`` if absent/failed."""
        return self._cache.get(field)

    def get_all_values(self) -> dict[str, float | str | None]:
        """Return a shallow copy of the full cache dict."""
        return dict(self._cache)

    def get_cached_value(self) -> float | None:
        """Backward-compatible shim — returns ``heat_pump_power_w`` field value."""
        val = self._cache.get("heat_pump_power_w")
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                return None
        return None

    # ------------------------------------------------------------------
    # Wildcard expansion
    # ------------------------------------------------------------------

    async def _expand_wildcards(self) -> None:
        """Expand entity IDs ending with ``*`` by querying ``GET /api/states``."""
        wildcards = {
            field: (eid, conv)
            for field, (eid, conv) in self._entity_map.items()
            if eid.endswith("*")
        }
        if not wildcards:
            return

        endpoint = f"{self._url}/api/states"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.get(endpoint, headers=headers)
                resp.raise_for_status()
                all_states = resp.json()
        except Exception as exc:
            logger.warning("HA REST wildcard expansion failed: %s", exc)
            return

        for field, (pattern, conv) in wildcards.items():
            prefix = pattern[:-1]  # Remove trailing *
            del self._entity_map[field]
            for entity in all_states:
                eid = entity.get("entity_id", "")
                if eid.startswith(prefix):
                    # Use the entity_id itself as the field name for expanded entries
                    expanded_field = eid.replace(".", "_")
                    self._entity_map[expanded_field] = (eid, conv)

    # ------------------------------------------------------------------
    # Per-entity poll (public for tests — called sequentially in tests,
    # concurrently via asyncio.gather in _poll_loop)
    # ------------------------------------------------------------------

    async def _poll_one(
        self, field: str, entity_id: str, converter: Callable
    ) -> None:
        """Poll a single entity and update the cache."""
        endpoint = f"{self._url}/api/states/{entity_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.get(endpoint, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                raw_state = data.get("state", "")
                value = converter(raw_state)
                self._cache[field] = value
                logger.info(
                    "HA REST multi-entity poll entity_id=%s field=%s value=%s",
                    entity_id, field, value,
                )
        except Exception as exc:
            self._cache[field] = None
            logger.warning(
                "HA REST multi-entity poll failed entity_id=%s field=%s exc=%s",
                entity_id, field, exc,
            )

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Infinite polling loop — polls all entities concurrently each round."""
        try:
            while True:
                tasks = [
                    self._poll_one(field, eid, conv)
                    for field, (eid, conv) in self._entity_map.items()
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(self._poll_interval_s)
        except asyncio.CancelledError:
            raise
