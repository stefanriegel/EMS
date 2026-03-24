"""Async VRM REST client for Victron diagnostics polling.

Polls the VRM API ``/v2/installations/{site_id}/diagnostics`` endpoint on a
configurable interval (default 5 minutes).  Results are cached in a
:class:`~backend.dess_models.VrmDiagnostics` dataclass for synchronous
consumption by the coordinator.

Threading model
---------------
The poll loop runs as an ``asyncio.Task`` inside the main event loop --
no threads involved.  The coordinator reads :attr:`diagnostics` and
:attr:`available` directly (single-threaded asyncio safety).

Availability model
------------------
``available`` starts ``False`` and becomes ``True`` after the first
successful fetch.  It reverts to ``False`` on HTTP errors, connection
failures, or when diagnostics become stale (>15 minutes old).
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from backend.dess_models import VrmDiagnostics

logger = logging.getLogger(__name__)

_VRM_BASE_URL = "https://vrmapi.victronenergy.com"

# VRM idDataAttribute IDs for known diagnostic fields.
# Discovered from VRM API responses; unknown IDs are silently skipped.
_ATTR_BATTERY_SOC = 51
_ATTR_BATTERY_POWER = 49
_ATTR_GRID_POWER = 1
_ATTR_PV_POWER = 131
_ATTR_CONSUMPTION = 73

_STALE_THRESHOLD_S = 900.0  # 15 minutes


class VrmClient:
    """Async VRM REST client with background poll loop.

    Parameters
    ----------
    token:
        VRM Personal Access Token.
    site_id:
        VRM installation site ID (numeric).
    poll_interval_s:
        Seconds between diagnostic polls (default 300).
    """

    def __init__(
        self, token: str, site_id: int, poll_interval_s: float = 300.0
    ) -> None:
        self._token = token
        self._site_id = site_id
        self._poll_interval_s = poll_interval_s
        self._client: httpx.AsyncClient | None = None
        self._diagnostics = VrmDiagnostics()
        self._available = False
        self._task: asyncio.Task[None] | None = None
        self._first_parse_logged = False

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """True when diagnostics are fresh and successfully fetched."""
        return self._available

    @property
    def diagnostics(self) -> VrmDiagnostics:
        """Most recent diagnostics snapshot (may be stale)."""
        return self._diagnostics

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create the httpx client and start the background poll task."""
        self._client = httpx.AsyncClient(
            base_url=_VRM_BASE_URL,
            headers={"X-Authorization": f"Token {self._token}"},
            timeout=30.0,
        )
        self._task = asyncio.create_task(self._poll_loop(), name="vrm-poll")

    async def stop(self) -> None:
        """Cancel the poll task and close the httpx client."""
        if self._task is not None:
            self._task.cancel()
        if self._client is not None:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Background loop: fetch diagnostics, sleep, repeat."""
        while True:
            try:
                await self._fetch_diagnostics()
            except Exception as exc:  # noqa: BLE001
                logger.warning("VRM poll failed: %s", exc)
                self._available = False
            await asyncio.sleep(self._poll_interval_s)

    async def _fetch_diagnostics(self) -> None:
        """Fetch diagnostics from VRM API and update cached state."""
        assert self._client is not None
        resp = await self._client.get(
            f"/v2/installations/{self._site_id}/diagnostics",
            params={"count": 100},
        )
        if resp.status_code == 429:
            logger.warning("VRM rate limited -- backing off")
            self._available = False
            return
        resp.raise_for_status()
        data = resp.json()
        self._diagnostics = self._parse_diagnostics(data)
        self._available = True
        # Check staleness after parsing
        self._check_staleness()

    def _parse_diagnostics(self, data: dict) -> VrmDiagnostics:
        """Parse VRM diagnostics response into a typed dataclass.

        The response has the shape ``{"records": [{"idDataAttribute": int,
        "rawValue": str, ...}, ...]}``.  Known attribute IDs are mapped to
        ``VrmDiagnostics`` fields; unknown IDs are silently skipped.
        """
        diag = VrmDiagnostics(timestamp=time.time())
        records = data.get("records", [])

        if not self._first_parse_logged and records:
            logger.debug("VRM raw diagnostics (first parse): %s", data)
            self._first_parse_logged = True

        for record in records:
            attr_id = record.get("idDataAttribute")
            raw = record.get("rawValue")
            if attr_id is None or raw is None:
                continue
            try:
                value = float(raw)
            except (ValueError, TypeError):
                continue

            if attr_id == _ATTR_BATTERY_SOC:
                diag.battery_soc_pct = value
            elif attr_id == _ATTR_BATTERY_POWER:
                diag.battery_power_w = value
            elif attr_id == _ATTR_GRID_POWER:
                diag.grid_power_w = value
            elif attr_id == _ATTR_PV_POWER:
                diag.pv_power_w = value
            elif attr_id == _ATTR_CONSUMPTION:
                diag.consumption_w = value
            # Unknown attribute IDs are silently skipped.

        return diag

    def _check_staleness(self) -> None:
        """Mark unavailable if diagnostics are older than 15 minutes."""
        if self._diagnostics.timestamp == 0.0:
            return
        age_s = time.time() - self._diagnostics.timestamp
        if age_s > _STALE_THRESHOLD_S:
            logger.warning(
                "VRM diagnostics stale (%.0fs old, threshold %.0fs)",
                age_s,
                _STALE_THRESHOLD_S,
            )
            self._available = False
