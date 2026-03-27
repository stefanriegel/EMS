"""HA command handling mixin for the Coordinator.

Extracted from coordinator.py to keep the main file focused on the
control-loop logic.  All methods in HaCommandsMixin access coordinator
state through ``self`` — valid because the only concrete user of this
mixin is the ``Coordinator`` class.

CTRL-07..CTRL-10: handle HA MQTT commands for min-SoC, dead-band,
ramp-rate, mode override, force grid-charge, and Supervisor persistence.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class HaCommandsMixin:
    """Mixin that adds HA command handling to Coordinator.

    Accesses the following instance attributes set up by Coordinator.__init__:
    - self._sys_config (SystemConfig)
    - self._self_tuner
    - self._huawei_deadband_w / _victron_deadband_w (int)
    - self._huawei_ramp_w_per_cycle / _victron_ramp_w_per_cycle (int)
    - self._mode_override (str | None)
    - self._mode_timeout_handle (asyncio.TimerHandle | None)
    - self._supervisor_client
    - self._ha_mqtt_client
    - self._state (CoordinatorState | None)
    """

    # ------------------------------------------------------------------
    # HA command handling (CTRL-07..CTRL-10)
    # ------------------------------------------------------------------

    # Entity min/max/step from ha_mqtt_client entity definitions
    _NUMBER_RANGES: dict[str, tuple[float, float]] = {
        "min_soc_huawei": (10, 100),
        "min_soc_victron": (10, 100),
        "deadband_huawei": (50, 1000),
        "deadband_victron": (50, 500),
        "ramp_rate": (100, 2000),
    }

    _VALID_MODES = {"AUTO", "HOLD", "GRID_CHARGE", "DISCHARGE_LOCKED"}

    def _handle_ha_command(self, entity_id: str, payload: str) -> None:
        """Handle an incoming HA MQTT command.

        Dispatches to the appropriate handler based on entity_id.
        Called from the asyncio event loop thread (via call_soon_threadsafe
        in ha_mqtt_client._on_message).

        Parameters
        ----------
        entity_id:
            The entity identifier (e.g. 'min_soc_huawei', 'control_mode').
        payload:
            The command payload as a string.
        """
        handlers = {
            "min_soc_huawei": self._cmd_min_soc_huawei,
            "min_soc_victron": self._cmd_min_soc_victron,
            "deadband_huawei": self._cmd_deadband_huawei,
            "deadband_victron": self._cmd_deadband_victron,
            "ramp_rate": self._cmd_ramp_rate,
            "control_mode": self._cmd_control_mode,
            "force_grid_charge": self._cmd_force_grid_charge,
            "reset_to_auto": self._cmd_reset_to_auto,
        }
        handler = handlers.get(entity_id)
        if handler is None:
            logger.warning("HA command: unknown entity_id=%s", entity_id)
            return
        handler(payload)
        self._trigger_state_echo()

    def _clamp_number(self, entity_id: str, value: float) -> float:
        """Clamp a number value to the entity's min/max range."""
        lo, hi = self._NUMBER_RANGES.get(entity_id, (value, value))
        return max(lo, min(hi, value))

    def _cmd_min_soc_huawei(self, payload: str) -> None:
        val = self._clamp_number("min_soc_huawei", float(payload))
        self._sys_config.huawei_min_soc_pct = val
        logger.info("HA command: huawei_min_soc_pct = %.1f", val)
        self._persist_to_supervisor("huawei_min_soc_pct", val)
        if self._self_tuner is not None:
            self._self_tuner.mark_ha_override("huawei_min_soc")

    def _cmd_min_soc_victron(self, payload: str) -> None:
        val = self._clamp_number("min_soc_victron", float(payload))
        self._sys_config.victron_min_soc_pct = val
        logger.info("HA command: victron_min_soc_pct = %.1f", val)
        self._persist_to_supervisor("victron_min_soc_pct", val)
        if self._self_tuner is not None:
            self._self_tuner.mark_ha_override("victron_min_soc")

    def _cmd_deadband_huawei(self, payload: str) -> None:
        val = int(self._clamp_number("deadband_huawei", float(payload)))
        self._huawei_deadband_w = val
        logger.info("HA command: huawei_deadband_w = %d", val)
        self._persist_to_supervisor("huawei_deadband_w", val)
        if self._self_tuner is not None:
            self._self_tuner.mark_ha_override("huawei_deadband_w")

    def _cmd_deadband_victron(self, payload: str) -> None:
        val = int(self._clamp_number("deadband_victron", float(payload)))
        self._victron_deadband_w = val
        logger.info("HA command: victron_deadband_w = %d", val)
        self._persist_to_supervisor("victron_deadband_w", val)
        if self._self_tuner is not None:
            self._self_tuner.mark_ha_override("victron_deadband_w")

    def _cmd_ramp_rate(self, payload: str) -> None:
        val = int(self._clamp_number("ramp_rate", float(payload)))
        self._huawei_ramp_w_per_cycle = val
        self._victron_ramp_w_per_cycle = val
        logger.info("HA command: ramp_rate_w = %d", val)
        self._persist_to_supervisor("ramp_rate_w", val)
        if self._self_tuner is not None:
            self._self_tuner.mark_ha_override("ramp_rate_w")

    def _cmd_control_mode(self, payload: str) -> None:
        mode = payload.strip().upper()
        if mode not in self._VALID_MODES:
            logger.warning("HA command: invalid control mode '%s'", payload)
            return
        if mode == "AUTO":
            self._mode_override = None
        else:
            self._mode_override = mode
        # Cancel any pending timeout when mode is explicitly set
        if self._mode_timeout_handle is not None:
            self._mode_timeout_handle.cancel()
            self._mode_timeout_handle = None
        logger.info("HA command: control_mode = %s (override=%s)", mode, self._mode_override)

    def _cmd_force_grid_charge(self, payload: str) -> None:
        self._mode_override = "GRID_CHARGE"
        # Cancel any existing timeout
        if self._mode_timeout_handle is not None:
            self._mode_timeout_handle.cancel()
            self._mode_timeout_handle = None
        # Schedule 60-minute auto-timeout
        try:
            loop = asyncio.get_running_loop()
            self._mode_timeout_handle = loop.call_later(
                3600, self._clear_mode_override
            )
        except RuntimeError:
            # No running loop (e.g. in tests) — skip timeout scheduling
            pass
        logger.info("Force grid charge activated, auto-timeout in 60 minutes")

    def _cmd_reset_to_auto(self, payload: str) -> None:
        self._mode_override = None
        if self._mode_timeout_handle is not None:
            self._mode_timeout_handle.cancel()
            self._mode_timeout_handle = None
        logger.info("Reset to auto: mode override cleared")

    def _clear_mode_override(self) -> None:
        """Timeout callback: clear mode override after force_grid_charge expires."""
        self._mode_override = None
        self._mode_timeout_handle = None
        logger.info("Grid charge auto-timeout: mode override cleared")
        self._trigger_state_echo()

    def _trigger_state_echo(self) -> None:
        """Publish current state immediately for HA feedback."""
        if self._ha_mqtt_client is not None and self._state is not None:
            extra = self._build_controllable_extra_fields()
            # Fire-and-forget: schedule the async publish in the event loop
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._ha_mqtt_client.publish(self._state, extra_fields=extra)
                )
            except RuntimeError:
                # No running loop — call synchronously for testing
                pass

    def _build_controllable_extra_fields(self) -> dict:
        """Build extra fields dict with controllable entity values."""
        return {
            "control_mode_override": self._mode_override or "AUTO",
            "huawei_min_soc_pct": self._sys_config.huawei_min_soc_pct,
            "victron_min_soc_pct": self._sys_config.victron_min_soc_pct,
            "huawei_deadband_w": self._huawei_deadband_w,
            "victron_deadband_w": self._victron_deadband_w,
            "ramp_rate_w": self._huawei_ramp_w_per_cycle,
        }

    def _persist_to_supervisor(self, key: str, value) -> None:
        """Fire-and-forget Supervisor options persistence (read-merge-write)."""
        if self._supervisor_client is None:
            logger.debug("No Supervisor client, skipping persistence for %s", key)
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._persist_to_supervisor_async(key, value))
        except RuntimeError:
            logger.debug("No running loop, skipping persistence for %s", key)

    async def _persist_to_supervisor_async(self, key: str, value) -> None:
        """Async worker for Supervisor options read-merge-write."""
        try:
            options = await self._supervisor_client.get_addon_options()
            if options is None:
                options = {}
            options[key] = value
            await self._supervisor_client.set_addon_options(options)
            logger.info("Supervisor: persisted %s=%s", key, value)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supervisor: persistence failed for %s — %s", key, exc)
