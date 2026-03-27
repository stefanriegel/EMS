"""Async Huawei SUN2000 / LUNA2000 Modbus TCP driver.

Wraps ``AsyncHuaweiSolar`` (huawei-solar ≥ 2.5) with:
  - Typed read methods returning the project's dataclasses.
  - Two-call battery read split (pack 1 / pack 2) with silent pack-2 fallback.
  - Reconnect-on-failure (one automatic retry, WARNING-logged).
  - Async context manager support.
  - Structured DEBUG/WARNING/ERROR logging on every Modbus interaction.

Usage::

    async with HuaweiDriver("192.168.0.10") as driver:
        master = await driver.read_master()
        battery = await driver.read_battery()
        slave = await driver.read_slave()

Logging::

    import logging
    logging.basicConfig(level=logging.DEBUG)

The module logger is ``backend.drivers.huawei_driver``.  Set it to DEBUG to
see every ``get_multiple`` call with slave ID, register names, and raw results.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from huawei_solar import AsyncHuaweiSolar, ConnectionException
from huawei_solar.register_values import (
    StorageForcibleChargeDischarge,
    StorageWorkingModesC,  # noqa: F401 — re-exported for callers
)

from backend.drivers.huawei_models import (
    HuaweiBatteryData,
    HuaweiMasterData,
    HuaweiSlaveData,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Register name lists (ascending Modbus address — required by get_multiple)
# ---------------------------------------------------------------------------

# Master inverter: state_1 (32000) < pv_01_voltage (32016) < ... < active_power (32080)
_MASTER_REGISTERS: list[str] = [
    "state_1",           # 32000
    "pv_01_voltage",     # 32016
    "pv_01_current",     # 32017
    "pv_02_voltage",     # 32018
    "pv_02_current",     # 32019
    "input_power",       # 32064
    "phase_A_voltage",   # 32069
    "phase_B_voltage",   # 32070
    "phase_C_voltage",   # 32071
    "active_power",      # 32080
    "grid_frequency",    # 32085
    "efficiency",        # 32086
    "internal_temperature",  # 32087
]

# Extended master registers (separate read — different address range)
_MASTER_YIELD_REGISTERS: list[str] = [
    "accumulated_yield_energy",  # 32106
    "daily_yield_energy",        # 32114
]

# Slave inverter: same PV registers, no storage
_SLAVE_REGISTERS: list[str] = [
    "state_1",           # 32000
    "pv_01_voltage",     # 32016
    "pv_01_current",     # 32017
    "pv_02_voltage",     # 32018
    "pv_02_current",     # 32019
    "input_power",       # 32064
    "active_power",      # 32080
]

# Battery pack 1 + system limits (37000–37050, all within 64-reg gap)
# NOTE: storage_unit_1_working_mode_b (37006) is intentionally excluded —
# this register throws DecodeError on some firmware versions and poisons the
# entire get_multiple batch.  It is read separately below with suppress().
_BATTERY_PACK1_REGISTERS: list[str] = [
    "storage_unit_1_running_status",          # 37000
    "storage_unit_1_charge_discharge_power",  # 37001
    "storage_unit_1_state_of_capacity",       # 37004
    "storage_maximum_charge_power",           # 37046
    "storage_maximum_discharge_power",        # 37048
]


# Battery pack 2 + combined system (37738–37767, all within 64-reg gap)
# This call is wrapped in contextlib.suppress — pack 2 may be absent.
_BATTERY_PACK2_REGISTERS: list[str] = [
    "storage_unit_2_state_of_capacity",       # 37738
    "storage_unit_2_running_status",          # 37741
    "storage_unit_2_charge_discharge_power",  # 37743
    "storage_state_of_capacity",              # 37760
    "storage_charge_discharge_power",         # 37765
]


class HuaweiDriver:
    """Async driver for master + slave SUN2000 inverters via Modbus TCP proxy.

    Parameters
    ----------
    host:
        IP address or hostname of the Modbus TCP proxy.
    port:
        TCP port (default 502).
    master_slave_id:
        Modbus slave / unit ID of the master inverter (default 0).
    slave_slave_id:
        Modbus slave / unit ID of the slave (PV-only) inverter (default 2).
    timeout_s:
        Per-request timeout in seconds passed to ``AsyncHuaweiSolar.create``
        (default 10).
    """

    def __init__(
        self,
        host: str,
        port: int = 502,
        master_slave_id: int = 0,
        slave_slave_id: int = 2,
        timeout_s: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.master_slave_id = master_slave_id
        self.slave_slave_id = slave_slave_id
        self.timeout_s = timeout_s
        self._client: AsyncHuaweiSolar | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the Modbus TCP connection.

        Creates a new ``AsyncHuaweiSolar`` session.  Call this before any
        read/write method, or use the async context manager instead.
        """
        self._client = await AsyncHuaweiSolar.create(
            self.host,
            self.port,
            timeout=int(self.timeout_s),
        )
        logger.debug(
            "Connected to Huawei Modbus at %s:%d (master_id=%d slave_id=%d)",
            self.host,
            self.port,
            self.master_slave_id,
            self.slave_slave_id,
        )

    async def close(self) -> None:
        """Close the Modbus TCP connection."""
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.stop()
            self._client = None
            logger.debug("Closed Huawei Modbus connection to %s:%d", self.host, self.port)

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "HuaweiDriver":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Reconnect helper
    # ------------------------------------------------------------------

    async def _with_reconnect(self, coro_factory):
        """Execute ``coro_factory()``, recreating the client on hard TCP failure.

        The huawei-solar library already handles transient failures internally:
        ``_read_registers`` retries up to 6× with exponential backoff and fires
        ``_reconnect()`` every 3rd retry for ``TimeoutError`` /
        ``ConnectionInterruptedException``.  We must NOT fight that by calling
        ``close()`` + ``connect()`` while the library's reconnect task is still
        running — doing so cancels the library's task and creates a race.

        This outer layer only handles ``ConnectionException`` — the case where
        the library's internal backoff has already exhausted all retries and
        given up entirely (e.g. the inverter was in night standby for hours).
        In that case we tear down and rebuild the client from scratch, then
        re-raise so the controller's failure counter increments normally.
        The controller resets failures on the next successful poll, so recovery
        is automatic once Modbus comes back.
        """
        try:
            return await coro_factory()
        except ConnectionException as exc:
            logger.warning(
                "Huawei TCP connection to %s:%d exhausted retries (%s) — "
                "rebuilding client; will recover on next successful poll",
                self.host,
                self.port,
                type(exc).__name__,
            )
            await self.close()
            try:
                await self.connect()
            except Exception as connect_exc:
                logger.warning(
                    "Huawei reconnect attempt failed: %s — will retry next cycle",
                    connect_exc,
                )
            raise  # let the controller's failure counter handle it

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    async def read_master(self) -> HuaweiMasterData:
        """Read master inverter state (PV generation + device status).

        Returns
        -------
        HuaweiMasterData
            Snapshot of the master inverter's PV and AC power registers.
        """

        async def _do() -> HuaweiMasterData:
            assert self._client is not None, "Driver not connected — call connect() first"
            logger.debug(
                "get_multiple slave_id=%d registers=%s",
                self.master_slave_id,
                _MASTER_REGISTERS,
            )
            results = await self._client.get_multiple(
                _MASTER_REGISTERS,
                slave_id=self.master_slave_id,
            )
            # results is list[Result] in the same order as _MASTER_REGISTERS
            r = {name: result.value for name, result in zip(_MASTER_REGISTERS, results)}
            logger.debug("read_master slave_id=%d raw=%s", self.master_slave_id, r)

            # Extended yield registers (separate read, best-effort)
            daily_kwh: float | None = None
            total_kwh: float | None = None
            try:
                yield_results = await self._client.get_multiple(
                    _MASTER_YIELD_REGISTERS,
                    slave_id=self.master_slave_id,
                )
                yr = {n: res.value for n, res in zip(_MASTER_YIELD_REGISTERS, yield_results)}
                daily_kwh = float(yr["daily_yield_energy"])
                total_kwh = float(yr["accumulated_yield_energy"])
            except Exception:
                pass  # non-critical

            return HuaweiMasterData(
                device_status=r["state_1"],
                pv_01_voltage_v=float(r["pv_01_voltage"]),
                pv_01_current_a=float(r["pv_01_current"]),
                pv_02_voltage_v=float(r["pv_02_voltage"]),
                pv_02_current_a=float(r["pv_02_current"]),
                pv_input_power_w=int(r["input_power"]),
                active_power_w=int(r["active_power"]),
                internal_temperature_c=float(r["internal_temperature"]),
                grid_frequency_hz=float(r["grid_frequency"]),
                efficiency_pct=float(r["efficiency"]),
                phase_a_voltage_v=float(r["phase_A_voltage"]),
                phase_b_voltage_v=float(r["phase_B_voltage"]),
                phase_c_voltage_v=float(r["phase_C_voltage"]),
                daily_yield_kwh=daily_kwh,
                total_yield_kwh=total_kwh,
            )

        return await self._with_reconnect(_do)

    async def read_slave(self) -> HuaweiSlaveData:
        """Read slave (PV-only) inverter state.

        Returns
        -------
        HuaweiSlaveData
            Snapshot of the slave inverter's PV and AC power registers.
        """

        async def _do() -> HuaweiSlaveData:
            assert self._client is not None, "Driver not connected — call connect() first"
            logger.debug(
                "get_multiple slave_id=%d registers=%s",
                self.slave_slave_id,
                _SLAVE_REGISTERS,
            )
            results = await self._client.get_multiple(
                _SLAVE_REGISTERS,
                slave_id=self.slave_slave_id,
            )
            r = {name: result.value for name, result in zip(_SLAVE_REGISTERS, results)}
            logger.debug("read_slave slave_id=%d raw=%s", self.slave_slave_id, r)
            return HuaweiSlaveData(
                device_status=r["state_1"],
                pv_01_voltage_v=float(r["pv_01_voltage"]),
                pv_01_current_a=float(r["pv_01_current"]),
                pv_02_voltage_v=float(r["pv_02_voltage"]),
                pv_02_current_a=float(r["pv_02_current"]),
                pv_input_power_w=int(r["input_power"]),
                active_power_w=int(r["active_power"]),
            )

        return await self._with_reconnect(_do)

    async def read_battery(self) -> HuaweiBatteryData:
        """Read battery system state from the master inverter.

        Makes **exactly two** ``get_multiple()`` calls:

        1. Pack-1 registers (37000–37050) — always expected to succeed.
        2. Pack-2 + combined registers (37738–37767) — wrapped in
           ``contextlib.suppress(Exception)``; if absent, pack-2 fields in
           the returned dataclass are ``None``.

        Returns
        -------
        HuaweiBatteryData
            Battery system snapshot.  Pack-2 fields are ``None`` on
            single-LUNA2000 installations.
        """

        async def _do() -> HuaweiBatteryData:
            assert self._client is not None, "Driver not connected — call connect() first"

            # --- Call 1: Pack 1 + system limits ---
            logger.debug(
                "get_multiple (pack1) slave_id=%d registers=%s",
                self.master_slave_id,
                _BATTERY_PACK1_REGISTERS,
            )
            pack1_results = await self._client.get_multiple(
                _BATTERY_PACK1_REGISTERS,
                slave_id=self.master_slave_id,
            )
            p1 = {
                name: result.value
                for name, result in zip(_BATTERY_PACK1_REGISTERS, pack1_results)
            }
            logger.debug("read_battery pack1 slave_id=%d raw=%s", self.master_slave_id, p1)

            # --- Call 1b: working_mode_b (optional — DecodeError on some firmware) ---
            with contextlib.suppress(Exception):
                wm_results = await self._client.get_multiple(
                    ["storage_unit_1_working_mode_b"],
                    slave_id=self.master_slave_id,
                )
                p1["storage_unit_1_working_mode_b"] = wm_results[0].value

            # Fallback: if _b register failed, read the settings register (47004)
            # which is the writable working-mode target and always responds.
            if "storage_unit_1_working_mode_b" not in p1:
                with contextlib.suppress(Exception):
                    wm_settings = await self._client.get_multiple(
                        ["storage_working_mode_settings"],
                        slave_id=self.master_slave_id,
                    )
                    p1["storage_unit_1_working_mode_b"] = wm_settings[0].value
                    logger.debug(
                        "working_mode from settings register: %r",
                        wm_settings[0].value,
                    )

            # --- Call 2: Pack 2 + combined (optional) ---
            p2: dict[str, Any] = {}
            logger.debug(
                "get_multiple (pack2) slave_id=%d registers=%s",
                self.master_slave_id,
                _BATTERY_PACK2_REGISTERS,
            )
            with contextlib.suppress(Exception):
                pack2_results = await self._client.get_multiple(
                    _BATTERY_PACK2_REGISTERS,
                    slave_id=self.master_slave_id,
                )
                p2 = {
                    name: result.value
                    for name, result in zip(_BATTERY_PACK2_REGISTERS, pack2_results)
                }
                logger.debug(
                    "read_battery pack2 slave_id=%d raw=%s", self.master_slave_id, p2
                )

            bat = HuaweiBatteryData(
                # Pack 1 (always present)
                pack1_status=p1.get("storage_unit_1_running_status"),
                pack1_charge_discharge_power_w=int(
                    p1["storage_unit_1_charge_discharge_power"]
                ),
                pack1_soc_pct=float(p1["storage_unit_1_state_of_capacity"]),
                working_mode=p1.get("storage_unit_1_working_mode_b"),
                max_charge_power_w=int(p1["storage_maximum_charge_power"]),
                max_discharge_power_w=int(p1["storage_maximum_discharge_power"]),
                # Pack 2 (optional — None when absent)
                pack2_soc_pct=(
                    float(p2["storage_unit_2_state_of_capacity"])
                    if "storage_unit_2_state_of_capacity" in p2
                    else None
                ),
                pack2_status=(
                    p2.get("storage_unit_2_running_status") if p2 else None
                ),
                pack2_charge_discharge_power_w=(
                    int(p2["storage_unit_2_charge_discharge_power"])
                    if "storage_unit_2_charge_discharge_power" in p2
                    else None
                ),
                # Combined / system-level (from pack-2 call; None if pack-2 absent)
                total_soc_pct=(
                    float(p2["storage_state_of_capacity"])
                    if "storage_state_of_capacity" in p2
                    else float(p1["storage_unit_1_state_of_capacity"])
                ),
                total_charge_discharge_power_w=(
                    int(p2["storage_charge_discharge_power"])
                    if "storage_charge_discharge_power" in p2
                    else int(p1["storage_unit_1_charge_discharge_power"])
                ),
            )

            # --- Call 3: Battery stats (best-effort) ---
            _STATS_REGS = [
                "storage_current_day_charge_capacity",
                "storage_current_day_discharge_capacity",
            ]
            _STATS_REGS2 = [
                "storage_total_charge",
                "storage_total_discharge",
            ]
            try:
                sr = await self._client.get_multiple(
                    _STATS_REGS, slave_id=self.master_slave_id
                )
                sd = {n: res.value for n, res in zip(_STATS_REGS, sr)}
                bat.day_charge_kwh = float(sd["storage_current_day_charge_capacity"])
                bat.day_discharge_kwh = float(sd["storage_current_day_discharge_capacity"])
            except Exception:
                pass
            try:
                sr2 = await self._client.get_multiple(
                    _STATS_REGS2, slave_id=self.master_slave_id
                )
                sd2 = {n: res.value for n, res in zip(_STATS_REGS2, sr2)}
                bat.total_charge_kwh = float(sd2["storage_total_charge"])
                bat.total_discharge_kwh = float(sd2["storage_total_discharge"])
            except Exception:
                pass
            try:
                cap_result = await self._client.get_multiple(
                    ["storage_rated_capacity"], slave_id=self.master_slave_id
                )
                bat.rated_capacity_wh = int(cap_result[0].value)
            except Exception:
                pass

            return bat

        return await self._with_reconnect(_do)

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    async def write_battery_mode(
        self, mode: StorageWorkingModesC, *, dry_run: bool = False
    ) -> None:
        """Set the battery storage working mode.

        Parameters
        ----------
        mode:
            A ``StorageWorkingModesC`` enum member (e.g.
            ``StorageWorkingModesC.MAXIMISE_SELF_CONSUMPTION``).
        dry_run:
            If ``True``, log the intended write but do not execute it.
        """

        async def _do() -> None:
            assert self._client is not None, "Driver not connected — call connect() first"
            if dry_run:
                logger.info(
                    "DRY RUN: would set storage_working_mode_settings=%r slave_id=%d",
                    mode,
                    self.master_slave_id,
                )
                return
            logger.debug(
                "set storage_working_mode_settings=%r slave_id=%d",
                mode,
                self.master_slave_id,
            )
            await self._client.set(
                "storage_working_mode_settings",
                mode,
                slave_id=self.master_slave_id,
            )

        await self._with_reconnect(_do)

    async def write_ac_charging(
        self, enabled: bool, *, dry_run: bool = False
    ) -> None:
        """Enable or disable charging from the AC grid.

        Parameters
        ----------
        enabled:
            ``True`` to allow charging from the grid; ``False`` to forbid it.
        dry_run:
            If ``True``, log the intended write but do not execute it.
        """

        async def _do() -> None:
            assert self._client is not None, "Driver not connected — call connect() first"
            if dry_run:
                logger.info(
                    "DRY RUN: would set storage_charge_from_grid_function=%r slave_id=%d",
                    enabled,
                    self.master_slave_id,
                )
                return
            logger.debug(
                "set storage_charge_from_grid_function=%r slave_id=%d",
                enabled,
                self.master_slave_id,
            )
            await self._client.set(
                "storage_charge_from_grid_function",
                enabled,
                slave_id=self.master_slave_id,
            )

        await self._with_reconnect(_do)

    async def write_max_charge_power(
        self, watts: int, *, dry_run: bool = False
    ) -> None:
        """Set the maximum battery charge power limit.

        Parameters
        ----------
        watts:
            Maximum charge power in watts.
        dry_run:
            If ``True``, log the intended write but do not execute it.
        """

        async def _do() -> None:
            assert self._client is not None, "Driver not connected — call connect() first"
            if dry_run:
                logger.info(
                    "DRY RUN: would set storage_maximum_charging_power=%d slave_id=%d",
                    watts,
                    self.master_slave_id,
                )
                return
            logger.debug(
                "set storage_maximum_charging_power=%d slave_id=%d",
                watts,
                self.master_slave_id,
            )
            await self._client.set(
                "storage_maximum_charging_power",
                watts,
                slave_id=self.master_slave_id,
            )

        await self._with_reconnect(_do)

    # ------------------------------------------------------------------
    # Connectivity validation
    # ------------------------------------------------------------------

    async def validate_connectivity(self) -> bool:
        """Perform a full read cycle to validate Modbus TCP connectivity.

        Returns ``True`` if all expected registers (master, battery, slave)
        are readable.
        """
        try:
            master = await self.read_master()
            battery = await self.read_battery()
            slave = await self.read_slave()
            logger.info(
                "Huawei connectivity validated: master_power=%dW SoC=%.1f%% "
                "slave_power=%dW",
                master.active_power_w,
                battery.total_soc_pct,
                slave.active_power_w,
            )
            return True
        except Exception as exc:
            logger.error("Huawei connectivity validation failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Write-back verification
    # ------------------------------------------------------------------

    async def verify_write_max_charge_power(self, watts: int) -> bool:
        """Write max charge power and verify by reading back the limit register.

        Returns ``True`` if the read-back value matches the written value.
        """
        await self.write_max_charge_power(watts)
        battery = await self.read_battery()
        actual = battery.max_charge_power_w
        match = actual == watts
        if not match:
            logger.warning(
                "Write-back mismatch: wrote max_charge=%d, read back=%d",
                watts,
                actual,
            )
        else:
            logger.info("Write-back verified: max_charge=%d matches", watts)
        return match

    async def verify_write_max_discharge_power(self, watts: int) -> bool:
        """Write max discharge power and verify by reading back the limit register.

        Returns ``True`` if the read-back value matches the written value.
        """
        await self.write_max_discharge_power(watts)
        battery = await self.read_battery()
        actual = battery.max_discharge_power_w
        match = actual == watts
        if not match:
            logger.warning(
                "Write-back mismatch: wrote max_discharge=%d, read back=%d",
                watts,
                actual,
            )
        else:
            logger.info("Write-back verified: max_discharge=%d matches", watts)
        return match

    async def write_max_discharge_power(
        self, watts: int, *, dry_run: bool = False
    ) -> None:
        """Set the maximum battery discharge power limit.

        Parameters
        ----------
        watts:
            Maximum discharge power in watts.
        dry_run:
            If ``True``, log the intended write but do not execute it.
        """

        async def _do() -> None:
            assert self._client is not None, "Driver not connected — call connect() first"
            if dry_run:
                logger.info(
                    "DRY RUN: would set storage_maximum_discharging_power=%d slave_id=%d",
                    watts,
                    self.master_slave_id,
                )
                return
            logger.debug(
                "set storage_maximum_discharging_power=%d slave_id=%d",
                watts,
                self.master_slave_id,
            )
            await self._client.set(
                "storage_maximum_discharging_power",
                watts,
                slave_id=self.master_slave_id,
            )

        await self._with_reconnect(_do)

    # ------------------------------------------------------------------
    # Forcible charge / discharge (mode 6 / THIRD_PARTY_DISPATCH)
    # ------------------------------------------------------------------

    async def write_forcible_discharge(
        self, watts: int, *, dry_run: bool = False
    ) -> None:
        """Trigger a forcible discharge at the specified power level.

        Used in THIRD_PARTY_DISPATCH (mode 6) where the EMS has direct
        control over battery power.  Writes the forcible command register
        (47100) and the discharge power register (47249).

        Parameters
        ----------
        watts:
            Target discharge power in watts (positive value).
        dry_run:
            If ``True``, log the intended write but do not execute it.
        """

        async def _do() -> None:
            assert self._client is not None, "Driver not connected — call connect() first"
            if dry_run:
                logger.info(
                    "DRY RUN: would write forcible_discharge=%dw slave_id=%d",
                    watts,
                    self.master_slave_id,
                )
                return
            logger.debug(
                "write forcible_discharge=%dw slave_id=%d", watts, self.master_slave_id
            )
            await self._client.set(
                "storage_forcible_discharge_power",
                watts,
                slave_id=self.master_slave_id,
            )
            await self._client.set(
                "forcible_charge_discharge_write",
                StorageForcibleChargeDischarge.DISCHARGE,
                slave_id=self.master_slave_id,
            )

        await self._with_reconnect(_do)

    async def write_forcible_charge(
        self, watts: int, *, dry_run: bool = False
    ) -> None:
        """Trigger a forcible charge at the specified power level.

        Parameters
        ----------
        watts:
            Target charge power in watts (positive value).
        dry_run:
            If ``True``, log the intended write but do not execute it.
        """

        async def _do() -> None:
            assert self._client is not None, "Driver not connected — call connect() first"
            if dry_run:
                logger.info(
                    "DRY RUN: would write forcible_charge=%dw slave_id=%d",
                    watts,
                    self.master_slave_id,
                )
                return
            logger.debug(
                "write forcible_charge=%dw slave_id=%d", watts, self.master_slave_id
            )
            await self._client.set(
                "storage_forcible_charge_power",
                watts,
                slave_id=self.master_slave_id,
            )
            await self._client.set(
                "forcible_charge_discharge_write",
                StorageForcibleChargeDischarge.CHARGE,
                slave_id=self.master_slave_id,
            )

        await self._with_reconnect(_do)

    async def write_forcible_stop(self, *, dry_run: bool = False) -> None:
        """Stop any active forcible charge or discharge command.

        Parameters
        ----------
        dry_run:
            If ``True``, log the intended write but do not execute it.
        """

        async def _do() -> None:
            assert self._client is not None, "Driver not connected — call connect() first"
            if dry_run:
                logger.info(
                    "DRY RUN: would write forcible_stop slave_id=%d",
                    self.master_slave_id,
                )
                return
            logger.debug("write forcible_stop slave_id=%d", self.master_slave_id)
            await self._client.set(
                "forcible_charge_discharge_write",
                StorageForcibleChargeDischarge.STOP,
                slave_id=self.master_slave_id,
            )

        await self._with_reconnect(_do)
