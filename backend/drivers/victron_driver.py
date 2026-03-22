"""Async Victron Multiplus II 3-phase Modbus TCP driver.

Connects to the Venus OS GX device over Modbus TCP using pymodbus
``AsyncModbusTcpClient``.  Reads system-level and VE.Bus registers,
returns typed ``VictronSystemData`` snapshots, and writes Hub4
per-phase AC power setpoints.

Usage::

    async with VictronDriver("192.168.0.10") as driver:
        state = await driver.read_system_state()
        await driver.write_ac_power_setpoint(1, -500.0)  # L1: 500 W discharge

Sign convention
---------------
Victron's native Modbus convention for battery power already matches the
canonical EMS convention:

  * ``battery_power_w > 0``  ->  **charging**
  * ``battery_power_w < 0``  ->  **discharging**

No sign flips are applied in read or write methods.

Logging
-------
The module logger is ``backend.drivers.victron_driver``.  Set it to DEBUG to
see every Modbus read/write with register addresses and raw values.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusException

from backend.drivers.victron_models import VictronPhaseData, VictronSystemData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Register addresses
# Source: Victron CCGX-Modbus-TCP-register-list.xlsx + attributes.csv
# https://github.com/victronenergy/dbus_modbustcp/blob/master/attributes.csv
# ---------------------------------------------------------------------------

# System registers (unit_id=100)
_SYS_REG_BATTERY_VOLTAGE = 840   # uint16, scale 10 (raw / 10 -> V)
_SYS_REG_BATTERY_CURRENT = 841   # int16,  scale 10 (raw / 10 -> A)
_SYS_REG_BATTERY_POWER = 842     # int16,  scale 1  (W, positive=charging)
_SYS_REG_BATTERY_SOC = 843       # uint16, scale 1  (%)
_SYS_REG_GRID_L1_POWER = 820     # int16,  scale 1  (W)
_SYS_REG_GRID_L2_POWER = 821     # int16,  scale 1  (W)
_SYS_REG_GRID_L3_POWER = 822     # int16,  scale 1  (W)

# VE.Bus registers (unit_id=227 default)
_VB_REG_AC_OUT_L1_V = 15   # uint16, scale 10 (raw / 10 -> V)
_VB_REG_AC_OUT_L2_V = 16   # uint16, scale 10
_VB_REG_AC_OUT_L3_V = 17   # uint16, scale 10
_VB_REG_AC_OUT_L1_I = 18   # int16,  scale 10 (raw / 10 -> A)
_VB_REG_AC_OUT_L2_I = 19   # int16,  scale 10
_VB_REG_AC_OUT_L3_I = 20   # int16,  scale 10
_VB_REG_AC_OUT_L1_P = 23   # int16,  scale 0.1 (raw * 0.1 -> W)
_VB_REG_AC_OUT_L2_P = 24   # int16,  scale 0.1
_VB_REG_AC_OUT_L3_P = 25   # int16,  scale 0.1
_VB_REG_STATE = 31          # uint16, scale 1
_VB_REG_MODE = 33           # uint16, scale 1

# Hub4 writable registers (VE.Bus unit)
_VB_REG_HUB4_L1_SETPOINT = 37     # int16, scale 1, W
_VB_REG_HUB4_DISABLE_CHARGE = 38  # uint16, 0=allowed, 1=disabled
_VB_REG_HUB4_DISABLE_FEEDIN = 39  # uint16, 0=allowed, 1=disabled
_VB_REG_HUB4_L2_SETPOINT = 40     # int16, scale 1, W
_VB_REG_HUB4_L3_SETPOINT = 41     # int16, scale 1, W

# Phase number -> setpoint register mapping
_PHASE_SETPOINT_REG = {
    1: _VB_REG_HUB4_L1_SETPOINT,
    2: _VB_REG_HUB4_L2_SETPOINT,
    3: _VB_REG_HUB4_L3_SETPOINT,
}


def _signed16(value: int) -> int:
    """Interpret a 16-bit unsigned register value as signed int16.

    Modbus registers are transmitted as unsigned 16-bit.  Values >= 0x8000
    represent negative numbers in two's complement.
    """
    return value - 0x10000 if value >= 0x8000 else value


class VictronDriver:
    """Async driver for the Victron Multiplus II 3-phase system via Modbus TCP.

    Parameters
    ----------
    host:
        IP address or hostname of the Venus OS GX device.
    port:
        TCP port (default 502 for Modbus TCP).
    timeout_s:
        Per-request timeout in seconds (default 5.0).
    system_unit_id:
        Modbus unit ID for system-level registers (default 100).
    vebus_unit_id:
        Modbus unit ID for VE.Bus inverter registers (default 227).
    """

    def __init__(
        self,
        host: str,
        port: int = 502,
        timeout_s: float = 5.0,
        system_unit_id: int = 100,
        vebus_unit_id: int = 227,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self._system_unit_id = system_unit_id
        self._vebus_unit_id = vebus_unit_id

        self._client = AsyncModbusTcpClient(
            host=host,
            port=port,
            timeout=timeout_s,
            retries=1,
        )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the Modbus TCP server and verify link with a health check.

        Reads the battery SoC register (843) as a health check to confirm
        the Modbus link is live and the system unit ID is correct.

        Raises
        ------
        ConnectionError
            If the TCP connection fails or the health check register read
            returns an error.
        """
        connected = await self._client.connect()
        if not connected:
            raise ConnectionError(
                f"Failed to connect to Victron Modbus TCP at {self.host}:{self.port}"
            )

        # Health check: read SoC register to verify link and unit ID
        result = await self._client.read_holding_registers(
            address=_SYS_REG_BATTERY_SOC,
            count=1,
            slave=self._system_unit_id,
        )
        if result.isError():
            self._client.close()
            raise ConnectionError(
                f"Victron health check failed: cannot read SoC register 843 "
                f"from unit {self._system_unit_id} at {self.host}:{self.port}"
            )

        initial_soc = result.registers[0]
        logger.info(
            "Victron Modbus TCP connected: %s:%d (system_unit=%d, vebus_unit=%d, "
            "initial SoC=%d%%)",
            self.host,
            self.port,
            self._system_unit_id,
            self._vebus_unit_id,
            initial_soc,
        )

    async def close(self) -> None:
        """Close the Modbus TCP connection."""
        self._client.close()
        logger.debug(
            "Victron Modbus TCP disconnected from %s:%d", self.host, self.port
        )

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "VictronDriver":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Reconnect helper
    # ------------------------------------------------------------------

    async def _with_reconnect(self, coro_factory):
        """Execute ``coro_factory()`` and retry once on Modbus/connection failure.

        On the first ``ModbusException`` or ``ConnectionException``, closes
        the client, reconnects, and retries exactly once.
        """
        try:
            return await coro_factory()
        except (ModbusException, ConnectionException) as exc:
            logger.warning(
                "Modbus error on %s:%d (%s) -- reconnecting and retrying",
                self.host,
                self.port,
                type(exc).__name__,
            )
            self._client.close()
            await self.connect()
            return await coro_factory()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def read_system_state(self) -> VictronSystemData:
        """Read all relevant registers and return a typed system snapshot.

        Makes batched ``read_holding_registers`` calls to minimize TCP
        round-trips.  Applies scale factors and signed int16 conversion
        as documented in the Victron register list.

        Returns
        -------
        VictronSystemData
            Snapshot with battery state, per-phase AC data, grid power,
            and ESS control registers.
        """

        async def _do() -> VictronSystemData:
            # --- System battery registers 840-843 (4 consecutive) ---
            sys_bat = await self._client.read_holding_registers(
                address=_SYS_REG_BATTERY_VOLTAGE,
                count=4,
                slave=self._system_unit_id,
            )
            bat_voltage_v = sys_bat.registers[0] / 10.0
            bat_current_a = _signed16(sys_bat.registers[1]) / 10.0
            bat_power_w = float(_signed16(sys_bat.registers[2]))
            bat_soc_pct = float(sys_bat.registers[3])

            # --- System grid registers 820-822 (3 consecutive) ---
            sys_grid = await self._client.read_holding_registers(
                address=_SYS_REG_GRID_L1_POWER,
                count=3,
                slave=self._system_unit_id,
            )
            grid_l1_w = float(_signed16(sys_grid.registers[0]))
            grid_l2_w = float(_signed16(sys_grid.registers[1]))
            grid_l3_w = float(_signed16(sys_grid.registers[2]))
            grid_total_w = grid_l1_w + grid_l2_w + grid_l3_w

            # --- VE.Bus AC output voltage 15-17 (3 consecutive) ---
            vb_volt = await self._client.read_holding_registers(
                address=_VB_REG_AC_OUT_L1_V,
                count=3,
                slave=self._vebus_unit_id,
            )
            l1_voltage_v = vb_volt.registers[0] / 10.0
            l2_voltage_v = vb_volt.registers[1] / 10.0
            l3_voltage_v = vb_volt.registers[2] / 10.0

            # --- VE.Bus AC output current 18-20 (3 consecutive) ---
            vb_curr = await self._client.read_holding_registers(
                address=_VB_REG_AC_OUT_L1_I,
                count=3,
                slave=self._vebus_unit_id,
            )
            l1_current_a = _signed16(vb_curr.registers[0]) / 10.0
            l2_current_a = _signed16(vb_curr.registers[1]) / 10.0
            l3_current_a = _signed16(vb_curr.registers[2]) / 10.0

            # --- VE.Bus AC output power 23-25 (3 consecutive) ---
            vb_pow = await self._client.read_holding_registers(
                address=_VB_REG_AC_OUT_L1_P,
                count=3,
                slave=self._vebus_unit_id,
            )
            l1_power_w = _signed16(vb_pow.registers[0]) * 0.1
            l2_power_w = _signed16(vb_pow.registers[1]) * 0.1
            l3_power_w = _signed16(vb_pow.registers[2]) * 0.1

            # --- VE.Bus state register 31 ---
            vb_state = await self._client.read_holding_registers(
                address=_VB_REG_STATE,
                count=1,
                slave=self._vebus_unit_id,
            )
            vebus_state = int(vb_state.registers[0])

            # --- VE.Bus mode register 33 ---
            vb_mode = await self._client.read_holding_registers(
                address=_VB_REG_MODE,
                count=1,
                slave=self._vebus_unit_id,
            )
            ess_mode = int(vb_mode.registers[0])

            return VictronSystemData(
                battery_soc_pct=bat_soc_pct,
                battery_power_w=bat_power_w,
                battery_current_a=bat_current_a,
                battery_voltage_v=bat_voltage_v,
                l1=VictronPhaseData(
                    power_w=l1_power_w,
                    current_a=l1_current_a,
                    voltage_v=l1_voltage_v,
                    setpoint_w=None,
                ),
                l2=VictronPhaseData(
                    power_w=l2_power_w,
                    current_a=l2_current_a,
                    voltage_v=l2_voltage_v,
                    setpoint_w=None,
                ),
                l3=VictronPhaseData(
                    power_w=l3_power_w,
                    current_a=l3_current_a,
                    voltage_v=l3_voltage_v,
                    setpoint_w=None,
                ),
                ess_mode=ess_mode,
                system_state=None,  # Not available via simple register read
                vebus_state=vebus_state,
                grid_power_w=grid_total_w,
                grid_l1_power_w=grid_l1_w,
                grid_l2_power_w=grid_l2_w,
                grid_l3_power_w=grid_l3_w,
                consumption_w=None,   # Not available via Modbus
                pv_on_grid_w=None,    # Not available via Modbus
                timestamp=time.monotonic(),
            )

        return await self._with_reconnect(_do)

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    async def write_ac_power_setpoint(
        self, phase: int, watts: float
    ) -> None:
        """Write a per-phase AC power setpoint to the Hub4 register.

        Parameters
        ----------
        phase:
            Phase number: 1, 2, or 3.
        watts:
            Setpoint in watts.  Positive = import from grid (charge battery /
            supply loads).  Negative = export to grid (discharge battery).

            Sign convention matches canonical EMS convention (positive=charge)
            and Victron's native Modbus convention -- no conversion needed.

        Raises
        ------
        ValueError
            If phase is not 1, 2, or 3.
        """
        reg = _PHASE_SETPOINT_REG.get(phase)
        if reg is None:
            raise ValueError(f"Invalid phase {phase}: must be 1, 2, or 3")

        # Convert to int16, then mask to unsigned for the wire format
        value = int(watts) & 0xFFFF

        async def _do() -> None:
            await self._client.write_register(
                address=reg,
                value=value,
                slave=self._vebus_unit_id,
            )
            logger.debug(
                "Victron setpoint: phase L%d = %d W (reg %d, raw 0x%04X, "
                "unit %d)",
                phase,
                int(watts),
                reg,
                value,
                self._vebus_unit_id,
            )

        await self._with_reconnect(_do)
