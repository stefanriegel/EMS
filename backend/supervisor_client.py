"""Home Assistant Supervisor API client.

Provides zero-config service discovery for add-ons running inside Home
Assistant OS.  When ``SUPERVISOR_TOKEN`` is present (injected automatically
by the HA Supervisor into every add-on container), this module can resolve:

- **MQTT broker** credentials via ``GET /services/mqtt``
- **HA Core REST API** base URL + token via the Supervisor proxy
- **EVCC add-on** host + port by scanning installed add-ons for a known slug
  pattern (``*_evcc``)

All methods return ``None`` (never raise) when the Supervisor is unreachable
or the requested service is not available.  Callers fall back to env-var
config transparently.

Usage
-----
::

    client = SupervisorClient.from_env()
    if client:
        mqtt = await client.get_mqtt_service()   # MqttServiceInfo | None
        evcc = await client.get_evcc_info()      # EvccAddonInfo | None
        ha   = client.get_ha_proxy_config()      # HaProxyConfig (always set if client exists)

Observability
-------------
- ``INFO  "Supervisor: MQTT service resolved …"``
- ``INFO  "Supervisor: EVCC add-on found …"``
- ``WARNING "Supervisor: …"`` on non-fatal failures (service unavailable etc.)
- ``DEBUG`` for every HTTP call made to the Supervisor API
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SUPERVISOR_BASE = "http://supervisor"
_TIMEOUT = 5.0


@dataclass(frozen=True)
class MqttServiceInfo:
    host: str
    port: int
    username: str | None
    password: str | None
    ssl: bool


@dataclass(frozen=True)
class EvccAddonInfo:
    """Host + port for a running EVCC add-on reachable from host network."""
    slug: str
    api_host: str
    api_port: int       # REST API port (host-mapped)
    mqtt_host: str
    mqtt_port: int      # MQTT port (host-mapped, typically 5200 for EVCC)


@dataclass(frozen=True)
class HaProxyConfig:
    """HA Core REST/WS access via Supervisor proxy — no user token needed."""
    base_url: str    # e.g. "http://supervisor/core/api"
    token: str       # SUPERVISOR_TOKEN


@dataclass(frozen=True)
class InfluxdbServiceInfo:
    """InfluxDB service resolved from the HA Supervisor."""
    url: str
    token: str | None


# Known EVCC slug suffixes (repo-hash varies per installation)
_EVCC_SLUG_SUFFIX = "_evcc"
# EVCC API port exposed on host network
_EVCC_API_PORT = 7070
# EVCC MQTT port exposed on host network (not the HA broker — EVCC's own MQTT)
_EVCC_MQTT_PORT = 5200


class SupervisorClient:
    """Thin async client for the HA Supervisor REST API."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._headers = {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "SupervisorClient | None":
        """Return a client if ``SUPERVISOR_TOKEN`` is set, else ``None``."""
        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if not token:
            return None
        return cls(token)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_ha_proxy_config(self) -> HaProxyConfig:
        """Return HA Core API access config using the Supervisor proxy.

        No network call required — the proxy URL is always the same and the
        token is the ``SUPERVISOR_TOKEN`` itself.
        """
        return HaProxyConfig(
            base_url=f"{_SUPERVISOR_BASE}/core",
            token=self._token,
        )

    async def get_mqtt_service(self) -> MqttServiceInfo | None:
        """Resolve the Mosquitto broker via the Supervisor Services API.

        Returns ``None`` if the mqtt service is unavailable or the call fails.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                r = await http.get(
                    f"{_SUPERVISOR_BASE}/services/mqtt",
                    headers=self._headers,
                )
                r.raise_for_status()
                data: dict[str, Any] = r.json().get("data", {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supervisor: MQTT service lookup failed — %s", exc)
            return None

        if not data:
            logger.warning("Supervisor: MQTT service not available (no providers)")
            return None

        info = MqttServiceInfo(
            host=data.get("host", "core-mosquitto"),
            port=int(data.get("port", 1883)),
            username=data.get("username") or None,
            password=data.get("password") or None,
            ssl=bool(data.get("ssl", False)),
        )
        logger.info(
            "Supervisor: MQTT service resolved — host=%s port=%d user=%s",
            info.host, info.port, info.username or "(none)",
        )
        return info

    async def get_influxdb_service(self) -> InfluxdbServiceInfo | None:
        """Resolve the InfluxDB add-on via the Supervisor Services API.

        Returns ``None`` if the InfluxDB service is unavailable (404 = normal,
        not all HAOS installations have the HA InfluxDB add-on).
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                r = await http.get(
                    f"{_SUPERVISOR_BASE}/services/influxdb",
                    headers=self._headers,
                )
                if r.status_code == 404:
                    logger.debug("Supervisor: InfluxDB service not available (404)")
                    return None
                r.raise_for_status()
                data: dict[str, Any] = r.json().get("data", {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supervisor: InfluxDB service lookup failed — %s", exc)
            return None

        if not data:
            logger.debug("Supervisor: InfluxDB service not available (no providers)")
            return None

        info = InfluxdbServiceInfo(
            url=data.get("host", ""),
            token=data.get("token") or None,
        )
        logger.info(
            "Supervisor: InfluxDB service resolved — url=%s",
            info.url,
        )
        return info

    async def get_addon_options(self) -> dict | None:
        """Read the current add-on options via the Supervisor API.

        Returns the ``options`` dict, or ``None`` on error.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                r = await http.get(
                    f"{_SUPERVISOR_BASE}/addons/self/options",
                    headers=self._headers,
                )
                r.raise_for_status()
                return r.json().get("data", {}).get("options", {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supervisor: get_addon_options failed — %s", exc)
            return None

    async def set_addon_options(self, options: dict) -> bool:
        """Write add-on options via the Supervisor API (read-merge-write).

        Parameters
        ----------
        options:
            Full options dict to write (replaces all options, not partial).

        Returns ``True`` on success, ``False`` on error.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                r = await http.post(
                    f"{_SUPERVISOR_BASE}/addons/self/options",
                    headers=self._headers,
                    json={"options": options},
                )
                r.raise_for_status()
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supervisor: set_addon_options failed — %s", exc)
            return False

    async def get_evcc_info(self) -> EvccAddonInfo | None:
        """Discover a running EVCC add-on by scanning installed add-ons.

        Matches any add-on whose slug ends with ``_evcc`` (covers both stable
        and nightly EVCC add-on flavours).  Returns the first *started* match.

        Returns ``None`` if no EVCC add-on is running or the call fails.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                r = await http.get(
                    f"{_SUPERVISOR_BASE}/addons",
                    headers=self._headers,
                )
                r.raise_for_status()
                addons: list[dict] = r.json().get("data", {}).get("addons", [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supervisor: add-on list lookup failed — %s", exc)
            return None

        # Find a started EVCC add-on
        evcc_slug: str | None = None
        for addon in addons:
            slug = addon.get("slug", "")
            if slug.endswith(_EVCC_SLUG_SUFFIX) and addon.get("state") == "started":
                evcc_slug = slug
                break

        if not evcc_slug:
            logger.debug("Supervisor: no running EVCC add-on found")
            return None

        # Get network details to find host-mapped ports
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                r = await http.get(
                    f"{_SUPERVISOR_BASE}/addons/{evcc_slug}/info",
                    headers=self._headers,
                )
                r.raise_for_status()
                info: dict = r.json().get("data", {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supervisor: EVCC add-on info failed — %s", exc)
            return None

        network: dict[str, int | None] = info.get("network", {})
        state = info.get("state", "")

        if state != "started":
            logger.warning("Supervisor: EVCC add-on %s is not started (state=%s)", evcc_slug, state)
            return None

        # Resolve host-mapped API port.  EVCC exposes its REST API on the
        # ingress port (usually 7070) mapped to a host port.  Fall back to
        # scanning for a TCP port mapped to _EVCC_API_PORT.
        api_port = _resolve_host_port(network, _EVCC_API_PORT)
        mqtt_port = _resolve_host_port(network, _EVCC_MQTT_PORT)

        # With host_network on EMS, all host-mapped ports are on 127.0.0.1
        api_host = "127.0.0.1"
        mqtt_host = "127.0.0.1"

        result = EvccAddonInfo(
            slug=evcc_slug,
            api_host=api_host,
            api_port=api_port or _EVCC_API_PORT,
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port or _EVCC_MQTT_PORT,
        )
        logger.info(
            "Supervisor: EVCC add-on found — slug=%s api=%s:%d mqtt=%s:%d",
            evcc_slug, result.api_host, result.api_port,
            result.mqtt_host, result.mqtt_port,
        )
        return result


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _resolve_host_port(network: dict[str, int | None], container_port: int) -> int | None:
    """Find the host-mapped port for a given container port in a network dict.

    Network dict format: ``{"7070/tcp": 7070, "5200/tcp": 5200, ...}``
    """
    for key, host_port in network.items():
        try:
            cport = int(key.split("/")[0])
        except (ValueError, IndexError):
            continue
        if cport == container_port and host_port is not None:
            return int(host_port)
    return None
