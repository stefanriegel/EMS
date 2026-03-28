from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections import deque
from datetime import datetime, timezone

from backend.config import OrchestratorConfig, SupervisoryConfig, SystemConfig
from backend.controller_model import BatteryRole, ControllerCommand, ControllerSnapshot
from backend.interventions import InterventionResult, evaluate_interventions
from backend.supervisor_model import (
    BatteryState,
    InterventionRecord,
    Observation,
    SupervisorState,
)

logger = logging.getLogger(__name__)

_MAX_INTERVENTION_HISTORY = 100


class Supervisor:
    """Supervisory EMS: observe batteries, intervene only on trigger."""

    def __init__(
        self,
        huawei_ctrl,
        victron_ctrl,
        supervisory_config: SupervisoryConfig,
        orch_config: OrchestratorConfig,
        sys_config: SystemConfig,
        writer=None,
    ) -> None:
        self._h_ctrl = huawei_ctrl
        self._v_ctrl = victron_ctrl
        self._sup_config = supervisory_config
        self._orch_config = orch_config
        self._sys_config = sys_config
        self._writer = writer

        self._state: SupervisorState | None = None
        self._huawei_state = BatteryState.AUTONOMOUS
        self._victron_state = BatteryState.AUTONOMOUS
        self._interventions: deque[InterventionRecord] = deque(maxlen=_MAX_INTERVENTION_HISTORY)
        self._cross_charge_clear_count = 0
        self._balancing_active = False
        self._task: asyncio.Task | None = None
        self._scheduler = None
        self._notifier = None
        self._ha_mqtt = None
        self._last_error: str | None = None

    # --- Injected services ---

    def set_scheduler(self, scheduler) -> None:
        self._scheduler = scheduler

    def set_notifier(self, notifier) -> None:
        self._notifier = notifier

    def set_ha_mqtt_client(self, client) -> None:
        self._ha_mqtt = client

    # --- Lifecycle ---

    async def start(self) -> None:
        logger.info("Supervisor starting (interval=%.1fs)", self._sup_config.observation_interval_s)
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Supervisor stopped")

    # --- Public accessors ---

    def get_state(self) -> SupervisorState | None:
        return self._state

    def get_interventions(self, limit: int = 20) -> list[dict]:
        items = list(self._interventions)[-limit:]
        return [dataclasses.asdict(r) for r in items]

    def get_last_error(self) -> str | None:
        return self._last_error

    # --- Internal loop ---

    async def _loop(self) -> None:
        while True:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Supervisor cycle failed")
                self._last_error = "cycle_exception"
            await asyncio.sleep(self._sup_config.observation_interval_s)

    async def _run_cycle(self) -> None:
        obs = await self._observe()

        # Get active charge slot from scheduler
        active_slot = None
        if self._scheduler is not None:
            active_slot = self._scheduler.active_charge_slot()

        # Evaluate interventions
        result = evaluate_interventions(
            obs=obs,
            min_soc_pct=self._sup_config.min_soc_pct,
            min_soc_hysteresis_pct=self._sup_config.min_soc_hysteresis_pct,
            soc_balance_threshold_pct=self._sup_config.soc_balance_threshold_pct,
            soc_balance_hysteresis_pct=self._sup_config.soc_balance_hysteresis_pct,
            huawei_state=self._huawei_state,
            victron_state=self._victron_state,
            active_slot=active_slot,
            cross_charge_clear_count=self._cross_charge_clear_count,
            balancing_active=self._balancing_active,
        )

        # Apply state changes (write to controllers only on change)
        await self._apply_result(result, obs)

        # Update internal state
        self._huawei_state = result.huawei_state
        self._victron_state = result.victron_state
        self._balancing_active = result.balancing_active

        # Track cross-charge debounce
        has_hold = any(
            a.target_state == BatteryState.HELD for a in result.actions
        )
        if has_hold:
            self._cross_charge_clear_count = 0
        else:
            self._cross_charge_clear_count += 1

        # Record interventions
        now = datetime.now(tz=timezone.utc).isoformat()
        for action in result.actions:
            record = InterventionRecord(
                timestamp=now,
                intervention_type=self._classify_action(action, result),
                target_system=action.target_system,
                action=action.target_state,
                reason=self._describe_action(action, obs),
            )
            self._interventions.append(record)

        # Build supervisor state
        self._state = SupervisorState(
            pool_soc_pct=obs.pool_soc,
            huawei_soc_pct=obs.huawei_soc_pct,
            victron_soc_pct=obs.victron_soc_pct,
            soc_delta=obs.soc_delta,
            huawei_state=result.huawei_state,
            victron_state=result.victron_state,
            huawei_available=obs.huawei_available,
            victron_available=obs.victron_available,
            true_consumption_w=obs.true_consumption_w,
            pv_power_w=obs.pv_power_w,
            active_interventions=list(self._interventions)[-5:],
            timestamp=time.monotonic(),
            grid_charge_slot_active=active_slot is not None,
        )
        self._last_error = None

    async def _observe(self) -> Observation:
        h_snap = await self._h_ctrl.poll()
        v_snap = await self._v_ctrl.poll()
        return Observation(
            huawei_soc_pct=h_snap.soc_pct,
            victron_soc_pct=v_snap.soc_pct,
            huawei_power_w=h_snap.power_w,
            victron_power_w=v_snap.power_w,
            pv_power_w=float(h_snap.pv_input_power_w or 0),
            emma_load_power_w=float(h_snap.master_active_power_w or 0),
            victron_consumption_w=float(v_snap.consumption_w or 0),
            huawei_available=h_snap.available,
            victron_available=v_snap.available,
            timestamp=time.monotonic(),
        )

    async def _apply_result(self, result: InterventionResult, obs: Observation) -> None:
        """Write commands to controllers only when state changes."""
        if result.huawei_state != self._huawei_state or result.huawei_max_discharge_w is not None:
            cmd = self._build_huawei_command(result)
            await self._h_ctrl.execute(cmd)

        if result.victron_state != self._victron_state or result.victron_target_soc_pct is not None:
            cmd = self._build_victron_command(result)
            await self._v_ctrl.execute(cmd)

    def _build_huawei_command(self, result: InterventionResult) -> ControllerCommand:
        if result.huawei_state == BatteryState.HELD:
            return ControllerCommand(role=BatteryRole.HOLDING, target_watts=0)
        if result.huawei_state == BatteryState.GRID_CHARGING:
            return ControllerCommand(
                role=BatteryRole.GRID_CHARGE,
                target_watts=float(result.huawei_charge_power_w or 3000),
            )
        if result.huawei_max_discharge_w is not None:
            return ControllerCommand(
                role=BatteryRole.PRIMARY_DISCHARGE,
                target_watts=float(-result.huawei_max_discharge_w),
            )
        return ControllerCommand(role=BatteryRole.HOLDING, target_watts=0)

    def _build_victron_command(self, result: InterventionResult) -> ControllerCommand:
        if result.victron_state == BatteryState.HELD:
            return ControllerCommand(role=BatteryRole.HOLDING, target_watts=0)
        if result.victron_state == BatteryState.GRID_CHARGING:
            return ControllerCommand(
                role=BatteryRole.GRID_CHARGE,
                target_watts=float(result.victron_charge_power_w or 5000),
            )
        if result.victron_target_soc_pct is not None:
            return ControllerCommand(
                role=BatteryRole.PRIMARY_DISCHARGE,
                target_watts=0,
            )
        return ControllerCommand(role=BatteryRole.HOLDING, target_watts=0)

    def _classify_action(self, action, result: InterventionResult) -> str:
        if action.target_state == BatteryState.GRID_CHARGING:
            return "grid_charge_window"
        if action.target_state == BatteryState.HELD:
            return "min_soc_guard"
        if action.max_discharge_power_w is not None or action.target_soc_pct is not None:
            return "soc_balance"
        return "unknown"

    def _describe_action(self, action, obs: Observation) -> str:
        if action.target_state == BatteryState.HELD:
            soc = obs.huawei_soc_pct if action.target_system == "huawei" else obs.victron_soc_pct
            return f"{action.target_system} held: SoC={soc:.1f}%"
        if action.target_state == BatteryState.GRID_CHARGING:
            return f"{action.target_system} grid charging: target={action.target_soc_pct}%"
        if action.max_discharge_power_w is not None:
            return f"{action.target_system} throttled to {action.max_discharge_power_w}W (SoC balance)"
        return f"{action.target_system} → {action.target_state}"
