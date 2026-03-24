"""Data models for VRM diagnostics and DESS schedule integration.

Defines typed dataclasses consumed by :class:`~backend.vrm_client.VrmClient`
and :class:`~backend.dess_mqtt.DessMqttSubscriber`.  All fields have safe
defaults so the models can be instantiated without arguments in tests and
during graceful degradation.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DessScheduleSlot:
    """A single DESS schedule slot (0-3).

    Attributes:
        soc_pct:    Target SoC percentage for this slot.
        start_s:    Seconds from midnight (local GX time) when slot begins.
        duration_s: Duration of the slot in seconds.
        strategy:   DESS strategy: 0=optimize, 1=charge, 2=sell.
        active:     Whether this slot is currently active.
    """

    soc_pct: float = 0.0
    start_s: int = 0
    duration_s: int = 0
    strategy: int = 0
    active: bool = False


@dataclass
class DessSchedule:
    """Current DESS schedule (up to 4 slots).

    Attributes:
        slots:       List of 4 schedule slots.
        mode:        DESS mode: 0=off, 1=auto(VRM), 4=Node-RED.
        last_update: ``time.time()`` of the last MQTT message update.
    """

    slots: list[DessScheduleSlot] = field(
        default_factory=lambda: [DessScheduleSlot() for _ in range(4)]
    )
    mode: int = 0
    last_update: float = 0.0


@dataclass
class VrmDiagnostics:
    """Cached VRM diagnostics snapshot.

    Attributes:
        battery_soc_pct: Battery state of charge (%).
        battery_power_w: Battery power in watts.
        grid_power_w:    Grid power in watts.
        pv_power_w:      PV power in watts.
        consumption_w:   Consumption power in watts.
        timestamp:       ``time.time()`` when the diagnostics were fetched.
    """

    battery_soc_pct: float | None = None
    battery_power_w: float | None = None
    grid_power_w: float | None = None
    pv_power_w: float | None = None
    consumption_w: float | None = None
    timestamp: float = 0.0
