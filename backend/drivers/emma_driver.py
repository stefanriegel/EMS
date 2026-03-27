"""Async EMMA (Huawei Smart Energy Controller) Modbus TCP driver.

Reads system-level aggregated registers from the EMMA controller at
Modbus unit_id=0 on the same proxy as the Huawei inverters.  EMMA
provides total PV power, load power, feed-in power, and daily energy
counters that neither the inverter nor Victron can provide alone.

Uses raw pymodbus (not huawei-solar) since EMMA registers are not in
the huawei-solar register map.

Protocol notes (SmartHEMS V100R025C00SPC100 Modbus Interface Definitions):
- ALL registers (30xxx sampled data AND 40xxx control) use function code 0x03
  (Read Holding Registers). Function code 0x04 (Read Input Registers) is NOT
  supported by EMMA — using it causes a timeout with no response.
- Power registers (30354–30360) have unit kW and gain=1000, yielding raw
  integer values that must be divided by 1000 to get watts.
- EMMA logical device ID is always 0 (broadcast address).
- ESS control mode register 40000: 2=max self-consumption, 5=TOU, 6=third-party dispatch.

Usage::

    driver = EmmaDriver(host="192.168.0.10", port=502)
    await driver.connect()
    snapshot = await driver.poll()
    await driver.close()

The module logger is ``backend.drivers.emma_driver``.
"""
from __future__ import annotations

import logging

from dataclasses import dataclass

from pymodbus.client import AsyncModbusTcpClient

logger = logging.getLogger(__name__)


@dataclass
class EmmaSnapshot:
    """System-level snapshot from the EMMA controller.

    Sign conventions follow Huawei documentation:
      * ``feed_in_power_w > 0``  ->  exporting to grid
      * ``feed_in_power_w < 0``  ->  importing from grid
      * ``battery_power_w > 0``  ->  charging
      * ``battery_power_w < 0``  ->  discharging
    """

    pv_power_w: int
    """Total PV power across all inverters (W)."""

    load_power_w: int
    """Huawei-side load power (W)."""

    feed_in_power_w: int
    """Grid feed-in power (W, +export/-import)."""

    battery_power_w: int
    """Battery power (W, +charge/-discharge)."""

    battery_soc_pct: float
    """Battery state of charge (0-100 %)."""

    pv_yield_today_kwh: float
    """PV energy yield today (kWh)."""

    consumption_today_kwh: float
    """Energy consumption today (kWh)."""

    charged_today_kwh: float
    """Energy charged into batteries today (kWh)."""

    discharged_today_kwh: float
    """Energy discharged from batteries today (kWh)."""

    ess_control_mode: int
    """ESS control mode register value (read-only)."""


# ---------------------------------------------------------------------------
# Register map
# ---------------------------------------------------------------------------

# Each entry: (start_address, count, signed, gain, field_name)
# gain: divide raw value by this to get the final value
_REGISTER_MAP: list[tuple[int, int, bool, int, str]] = [
    (30354, 2, False, 1000, "pv_power_w"),           # U32, kW gain=1000 → W
    (30356, 2, False, 1000, "load_power_w"),          # U32, kW gain=1000 → W
    (30358, 2, True,  1000, "feed_in_power_w"),       # I32, kW gain=1000 → W
    (30360, 2, True,  1000, "battery_power_w"),       # I32, kW gain=1000 → W
    (30368, 1, False,  100, "battery_soc_pct"),       # U16, % gain=100
    (30346, 2, False,  100, "pv_yield_today_kwh"),    # U32, kWh gain=100
    (30324, 2, False,  100, "consumption_today_kwh"), # U32, kWh gain=100
    (30306, 2, False,  100, "charged_today_kwh"),     # U32, kWh gain=100
    (30312, 2, False,  100, "discharged_today_kwh"),  # U32, kWh gain=100
]

_ESS_MODE_ADDRESS = 40000  # U16, 1 register (holding register)


def _decode_u32(registers: list[int]) -> int:
    """Decode two 16-bit registers as an unsigned 32-bit integer (big-endian)."""
    return (registers[0] << 16) | registers[1]


def _decode_i32(registers: list[int]) -> int:
    """Decode two 16-bit registers as a signed 32-bit integer (big-endian)."""
    val = (registers[0] << 16) | registers[1]
    if val >= 0x80000000:
        val -= 0x100000000
    return val


class EmmaDriver:
    """Async driver for the EMMA Smart Energy Controller via Modbus TCP.

    Parameters
    ----------
    host:
        IP address or hostname of the Modbus TCP proxy (same as Huawei).
    port:
        TCP port (default 502).
    device_id:
        Modbus unit ID of the EMMA controller (default 0).
    timeout_s:
        Per-request timeout in seconds (default 10).
    """

    def __init__(
        self,
        host: str,
        port: int = 502,
        device_id: int = 0,
        timeout_s: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.device_id = device_id
        self._client = AsyncModbusTcpClient(
            host=host,
            port=port,
            timeout=timeout_s,
        )

    async def connect(self) -> None:
        """Open the Modbus TCP connection."""
        await self._client.connect()
        logger.debug(
            "EMMA connected to %s:%d (device_id=%d)",
            self.host,
            self.port,
            self.device_id,
        )

    async def close(self) -> None:
        """Close the Modbus TCP connection."""
        self._client.close()
        logger.debug("EMMA connection closed to %s:%d", self.host, self.port)

    async def poll(self) -> EmmaSnapshot | None:
        """Read all EMMA registers and return a snapshot.

        Returns ``None`` on any communication failure (fire-and-forget).
        """
        try:
            return await self._read_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("EMMA poll failed: %s", exc)
            return None

    async def _read_all(self) -> EmmaSnapshot:
        """Read all input and holding registers, decode, and build snapshot."""
        values: dict[str, float] = {}

        for address, count, signed, gain, field_name in _REGISTER_MAP:
            result = await self._client.read_holding_registers(
                address, count=count, device_id=self.device_id
            )
            if result.isError():
                raise RuntimeError(
                    f"EMMA read error at register {address}: {result}"
                )
            regs = list(result.registers)
            if count == 2:
                raw = _decode_i32(regs) if signed else _decode_u32(regs)
            else:
                raw = regs[0]
            values[field_name] = raw / gain if gain != 1 else raw

        # ESS control mode is a holding register
        ess_result = await self._client.read_holding_registers(
            _ESS_MODE_ADDRESS, count=1, device_id=self.device_id
        )
        if ess_result.isError():
            raise RuntimeError(
                f"EMMA read error at holding register {_ESS_MODE_ADDRESS}: {ess_result}"
            )
        ess_mode = ess_result.registers[0]

        return EmmaSnapshot(
            pv_power_w=int(values["pv_power_w"]),
            load_power_w=int(values["load_power_w"]),
            feed_in_power_w=int(values["feed_in_power_w"]),
            battery_power_w=int(values["battery_power_w"]),
            battery_soc_pct=float(values["battery_soc_pct"]),
            pv_yield_today_kwh=float(values["pv_yield_today_kwh"]),
            consumption_today_kwh=float(values["consumption_today_kwh"]),
            charged_today_kwh=float(values["charged_today_kwh"]),
            discharged_today_kwh=float(values["discharged_today_kwh"]),
            ess_control_mode=ess_mode,
        )
