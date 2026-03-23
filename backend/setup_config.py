"""EMS setup wizard persistence layer.

Defines :class:`EmsSetupConfig`, the flat dataclass written to disk by the
setup wizard and loaded at startup to bootstrap environment variables before
the main config dataclasses are constructed.

Atomic write pattern
--------------------
:func:`save_setup_config` writes to ``<path>.tmp`` and then calls
``os.replace()`` so the destination file is never partially written.

Environment variables
---------------------
``EMS_CONFIG_PATH`` — override the default config file path.  Default:
``/config/ems_config.json`` (the Home Assistant add-on config directory).

Observability
-------------
- ``INFO "Setup config loaded from {path} — huawei_host={...}"``  on successful load.
- ``WARNING "No setup config found at {path} — starting in setup-only mode"`` on absent file.
- ``INFO "Setup complete — config written to {path}"``  on save (logged by caller in setup_api.py).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Module-level constant — read once at import time so callers and tests can
# inspect the resolved default without referencing os.environ directly.
EMS_CONFIG_PATH: str = os.environ.get("EMS_CONFIG_PATH", "/config/ems_config.json")


@dataclass
class EmsSetupConfig:
    """Flat configuration produced by the setup wizard.

    All fields have defaults so the dataclass can be instantiated with zero
    arguments (useful for rendering the wizard with pre-populated placeholders).

    Field names intentionally mirror the environment variable names used by
    the existing ``*.from_env()`` config dataclasses so they can be injected
    via ``os.environ.setdefault()`` in the lifespan.
    """

    # --- Huawei Modbus ---
    huawei_host: str = ""
    huawei_port: int = 502

    # --- Victron Modbus TCP ---
    victron_host: str = ""
    victron_port: int = 502
    victron_system_unit_id: int = 100
    victron_battery_unit_id: int = 225
    victron_vebus_unit_id: int = 227

    # --- EVCC HTTP ---
    evcc_host: str = "192.168.0.10"
    evcc_port: int = 7070

    # --- EVCC MQTT ---
    evcc_mqtt_host: str = "192.168.0.10"
    evcc_mqtt_port: int = 1883

    # --- Home Assistant REST ---
    ha_url: str = ""
    ha_token: str = ""
    ha_heat_pump_entity_id: str = ""

    # --- Octopus Go tariff ---
    octopus_off_peak_start_min: int = 30
    octopus_off_peak_end_min: int = 330
    octopus_off_peak_rate_eur_kwh: float = 0.08
    octopus_peak_rate_eur_kwh: float = 0.28

    # --- Modul3 grid-fee tariff ---
    modul3_surplus_start_min: int = 0
    modul3_surplus_end_min: int = 0
    modul3_deficit_start_min: int = 0
    modul3_deficit_end_min: int = 0
    modul3_surplus_rate_eur_kwh: float = 0.0
    modul3_deficit_rate_eur_kwh: float = 0.0

    # --- Feed-in tariff ---
    feed_in_rate_eur_kwh: float = 0.074

    # --- Seasonal strategy ---
    winter_months: str = "11,12,1,2"
    winter_min_soc_boost_pct: int = 10

    # --- SoC limits ---
    huawei_min_soc_pct: float = 10.0
    huawei_max_soc_pct: float = 95.0
    victron_min_soc_pct: float = 15.0
    victron_max_soc_pct: float = 95.0


def load_setup_config(path: str) -> EmsSetupConfig | None:
    """Load an :class:`EmsSetupConfig` from *path*.

    Returns ``None`` in all error cases — never raises:

    - File does not exist → ``None``
    - File is not valid JSON → ``None`` (logged at WARNING)
    - JSON cannot be unpacked into :class:`EmsSetupConfig` → ``None``

    Parameters
    ----------
    path:
        Absolute path to the JSON config file.
    """
    if not os.path.exists(path):
        logger.warning("No setup config found at %s — starting in setup-only mode", path)
        return None

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        logger.warning("Setup config at %s is not valid JSON — ignoring: %s", path, exc)
        return None

    try:
        cfg = EmsSetupConfig(**data)
    except (TypeError, KeyError) as exc:
        logger.warning("Setup config at %s has unexpected fields — ignoring: %s", path, exc)
        return None

    logger.info(
        "Setup config loaded from %s — huawei_host=%s victron_host=%s",
        path,
        cfg.huawei_host,
        cfg.victron_host,
    )
    return cfg


def save_setup_config(cfg: EmsSetupConfig, path: str) -> None:
    """Persist *cfg* to *path* atomically.

    Writes to ``<path>.tmp`` first, then calls ``os.replace()`` to atomically
    swap the file into place.  Parent directories are created if absent.

    Parameters
    ----------
    cfg:
        The :class:`EmsSetupConfig` instance to persist.
    path:
        Absolute path for the JSON config file.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    tmp_path = path + ".tmp"
    payload = json.dumps(dataclasses.asdict(cfg), indent=2)

    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(payload)

    os.replace(tmp_path, path)
