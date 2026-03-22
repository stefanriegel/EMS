"""Charge schedule computation for the EMS battery pool (S03).

The :class:`Scheduler` fetches EVCC state, derives per-battery SoC targets,
selects the cheapest tariff window, and produces a :class:`ChargeSchedule`
stored on :attr:`Scheduler.active_schedule`.

Observability
-------------
- ``INFO "Scheduler wired — run_hour=N charge_window=M–K min"`` at startup
  (logged by ``main.py`` after construction).
- ``INFO "Scheduler.compute_schedule: evcc_state=None, marking schedule stale"``
  when EVCC is unreachable and a prior schedule exists.
- ``INFO "Scheduler.compute_schedule: evcc_state=None, no prior schedule — returning fallback"``
  when EVCC is unreachable and no prior schedule exists.
- ``INFO "Scheduler.compute_schedule: consumption fallback_used=True (cold-start or data gap) — today_expected_kwh=X"``
  on cold-start.
- ``INFO "Scheduler.compute_schedule: solar_kwh=X expected_kwh=Y charge_energy_kwh=Z huawei_target=H victron_target=V cost_eur=C"``
  on every successful run.
- ``WARNING "influx write_charge_schedule failed: ..."`` on InfluxDB write failure.

Inspection surfaces
-------------------
- ``Scheduler.active_schedule``  — the last computed schedule (or None).
- ``Scheduler.schedule_stale``   — True when EVCC was unreachable on last run.
- InfluxDB ``ems_schedule`` measurement.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from statistics import mean

from backend.config import OrchestratorConfig, SystemConfig
from backend.schedule_models import (
    ChargeSchedule,
    ChargeSlot,
    OptimizationReasoning,
)

logger = logging.getLogger("ems.scheduler")

# Price threshold in €/kWh below which a slot is considered "cheap".
_CHEAP_THRESHOLD_EUR_KWH = 0.15


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _make_fallback_schedule() -> ChargeSchedule:
    """Return a zero-charge fallback schedule for use when EVCC is unreachable.

    Returned schedule has ``stale=True``, ``slots=[]``, and all numeric
    reasoning fields set to zero.  Used when no prior ``active_schedule``
    exists and ``EvccClient.get_state()`` returns ``None``.

    Returns
    -------
    ChargeSchedule
        Minimal fallback schedule, always stale.
    """
    return ChargeSchedule(
        slots=[],
        stale=True,
        computed_at=datetime.now(tz=timezone.utc),
        reasoning=OptimizationReasoning(
            text="No EVCC data available — fallback schedule",
            tomorrow_solar_kwh=0.0,
            expected_consumption_kwh=0.0,
            charge_energy_kwh=0.0,
            cost_estimate_eur=0.0,
        ),
    )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class Scheduler:
    """Daily charge scheduler for the unified 94 kWh battery pool.

    Constructed once at application startup and stored on
    ``app.state.scheduler``.  Call :meth:`compute_schedule` nightly (default
    23:00 via ``SchedulerConfig.run_hour``) to refresh the charge plan.

    Parameters
    ----------
    evcc_client:
        Client for fetching EVCC/EVopt state; must implement
        ``async get_state() -> EvccState | None``.
    consumption_reader:
        InfluxDB reader for weekday-aware consumption history; must implement
        ``async query_consumption_history() -> ConsumptionForecast``.
    tariff_engine:
        Composite tariff engine for cheapest-window selection; must implement
        ``get_price_schedule(date) -> list[TariffSlot]``.
    sys_config:
        Per-system SoC limits (:class:`~backend.config.SystemConfig`).
    orch_config:
        Battery capacity and loop parameters
        (:class:`~backend.config.OrchestratorConfig`).
    """

    def __init__(
        self,
        evcc_client,
        consumption_reader,
        tariff_engine,
        sys_config: SystemConfig,
        orch_config: OrchestratorConfig,
    ) -> None:
        self._evcc_client = evcc_client
        self._consumption_reader = consumption_reader
        self._tariff_engine = tariff_engine
        self._sys_config = sys_config
        self._orch_config = orch_config
        self.active_schedule: ChargeSchedule | None = None
        self.schedule_stale: bool = False

    async def compute_schedule(self, writer=None) -> ChargeSchedule:
        """Compute a new charge schedule for the battery pool.

        Fetches the current EVCC/EVopt state and consumption history, derives
        per-battery SoC targets (EVopt timeseries or formula fallback), selects
        the cheapest tariff window for tomorrow, and returns a
        :class:`ChargeSchedule` with LUNA (Huawei) slot before Victron (D010).

        Parameters
        ----------
        writer:
            Optional :class:`~backend.influx_writer.InfluxMetricsWriter`.
            When provided, the computed schedule is written to InfluxDB
            (fire-and-forget: exceptions are logged but never raised).

        Returns
        -------
        ChargeSchedule
            The newly computed schedule, or the prior schedule (marked stale)
            if EVCC is unreachable, or a fallback schedule when no prior
            schedule exists.
        """
        # ------------------------------------------------------------------
        # 1. Fetch EVCC state
        # ------------------------------------------------------------------
        evcc_state = await self._evcc_client.get_state()

        if evcc_state is None:
            self.schedule_stale = True
            if self.active_schedule is not None:
                self.active_schedule.stale = True
                logger.info(
                    "Scheduler.compute_schedule: evcc_state=None, marking active schedule stale"
                )
                return self.active_schedule
            else:
                logger.info(
                    "Scheduler.compute_schedule: evcc_state=None, no prior schedule — returning fallback"
                )
                return _make_fallback_schedule()

        # ------------------------------------------------------------------
        # 2. Fetch consumption history
        # ------------------------------------------------------------------
        consumption = await self._consumption_reader.query_consumption_history()

        if consumption.fallback_used:
            logger.info(
                "Scheduler.compute_schedule: consumption fallback_used=True "
                "(cold-start or data gap) — today_expected_kwh=%.1f",
                consumption.today_expected_kwh,
            )

        # ------------------------------------------------------------------
        # 3. Derive solar forecast
        # ------------------------------------------------------------------
        solar_kwh = (
            evcc_state.solar.tomorrow_energy_wh / 1000.0
            if evcc_state.solar is not None
            else 0.0
        )

        # ------------------------------------------------------------------
        # 4. Derive SoC targets
        # ------------------------------------------------------------------
        total_capacity_kwh = (
            self._orch_config.huawei_capacity_kwh + self._orch_config.victron_capacity_kwh
        )

        if evcc_state.evopt is not None:
            # EVopt timeseries branch — targets encode the full delta from current SoC
            raw_huawei = evcc_state.evopt.get_huawei_target_soc_pct(
                self._orch_config.huawei_capacity_kwh
            )
            raw_victron = evcc_state.evopt.get_victron_target_soc_pct(
                self._orch_config.victron_capacity_kwh
            )
        else:
            # Formula fallback: proportion-split the net charge need
            # Predictive pre-charging (D-10, D-11, D-12 — OPT-04)
            if (
                evcc_state.solar is not None
                and solar_kwh >= consumption.today_expected_kwh * 1.2
            ):
                # Full solar coverage — skip grid charge entirely (D-10)
                net_charge_kwh = 0.0
            elif evcc_state.solar is not None and solar_kwh > 0:
                # Partial coverage — reduce target with 0.8 discount (D-11)
                net_charge_kwh = max(
                    0.0,
                    min(
                        consumption.today_expected_kwh - solar_kwh * 0.8,
                        total_capacity_kwh,
                    ),
                )
            else:
                # No solar forecast (EVCC offline) or zero solar — full charge (D-12)
                net_charge_kwh = max(
                    0.0,
                    min(
                        consumption.today_expected_kwh - solar_kwh,
                        total_capacity_kwh,
                    ),
                )
            if self._orch_config.huawei_capacity_kwh > 0 and total_capacity_kwh > 0:
                raw_huawei = (
                    net_charge_kwh
                    * (self._orch_config.huawei_capacity_kwh / total_capacity_kwh)
                    / self._orch_config.huawei_capacity_kwh
                ) * 100.0
            else:
                raw_huawei = 0.0

            if self._orch_config.victron_capacity_kwh > 0 and total_capacity_kwh > 0:
                raw_victron = (
                    net_charge_kwh
                    * (self._orch_config.victron_capacity_kwh / total_capacity_kwh)
                    / self._orch_config.victron_capacity_kwh
                ) * 100.0
            else:
                raw_victron = 0.0

        # ------------------------------------------------------------------
        # 5. Clamp to SystemConfig limits
        # ------------------------------------------------------------------
        huawei_target = max(
            self._sys_config.huawei_min_soc_pct,
            min(self._sys_config.huawei_max_soc_pct, raw_huawei),
        )
        victron_target = max(
            self._sys_config.victron_min_soc_pct,
            min(self._sys_config.victron_max_soc_pct, raw_victron),
        )

        # ------------------------------------------------------------------
        # 6. Select cheapest tariff window for tomorrow
        # ------------------------------------------------------------------
        tomorrow = date.today() + timedelta(days=1)
        all_slots = self._tariff_engine.get_price_schedule(tomorrow)

        cheap_slots = [
            s for s in all_slots if s.effective_rate_eur_kwh <= _CHEAP_THRESHOLD_EUR_KWH
        ]
        if not cheap_slots:
            # Fallback: single cheapest slot
            cheap_slots = [min(all_slots, key=lambda s: s.effective_rate_eur_kwh)]

        # Sort by start time to get a contiguous window
        cheap_slots = sorted(cheap_slots, key=lambda s: s.start)
        window_start = cheap_slots[0].start
        window_end = cheap_slots[-1].end

        # ------------------------------------------------------------------
        # 7. Cost estimate
        # ------------------------------------------------------------------
        # For EVopt path, use simple formula; for formula fallback, use
        # the solar-aware net_charge_kwh already computed in step 4
        if evcc_state.evopt is not None:
            charge_energy_kwh = max(0.0, consumption.today_expected_kwh - solar_kwh)
        else:
            charge_energy_kwh = net_charge_kwh
        avg_price = mean(s.effective_rate_eur_kwh for s in cheap_slots)
        cost_estimate_eur = charge_energy_kwh * avg_price

        # ------------------------------------------------------------------
        # 8. Build schedule (LUNA/Huawei first — D010)
        # ------------------------------------------------------------------
        huawei_slot = ChargeSlot(
            battery="huawei",
            target_soc_pct=huawei_target,
            start_utc=window_start,
            end_utc=window_end,
            grid_charge_power_w=5000,
        )
        victron_slot = ChargeSlot(
            battery="victron",
            target_soc_pct=victron_target,
            start_utc=window_start,
            end_utc=window_end,
            grid_charge_power_w=3000,
        )

        # Solar-aware reasoning text
        if evcc_state.evopt is None and evcc_state.solar is not None and charge_energy_kwh == 0.0:
            reasoning_text = (
                f"Solar forecast ({solar_kwh:.1f} kWh) covers expected consumption "
                f"({consumption.today_expected_kwh:.1f} kWh) — skipping grid charge"
            )
        elif evcc_state.evopt is None and evcc_state.solar is not None and solar_kwh > 0:
            reasoning_text = (
                f"Charging {charge_energy_kwh:.1f} kWh from grid "
                f"(solar: {solar_kwh:.1f} kWh forecast with 0.8 discount, "
                f"consumption: {consumption.today_expected_kwh:.1f} kWh expected)"
            )
        else:
            reasoning_text = (
                f"Charging {charge_energy_kwh:.1f} kWh from grid "
                f"(solar: {solar_kwh:.1f} kWh forecast, "
                f"consumption: {consumption.today_expected_kwh:.1f} kWh expected)"
            )

        reasoning = OptimizationReasoning(
            text=reasoning_text,
            tomorrow_solar_kwh=solar_kwh,
            expected_consumption_kwh=consumption.today_expected_kwh,
            charge_energy_kwh=charge_energy_kwh,
            cost_estimate_eur=cost_estimate_eur,
        )
        schedule = ChargeSchedule(
            slots=[huawei_slot, victron_slot],
            reasoning=reasoning,
            computed_at=datetime.now(tz=timezone.utc),
            stale=False,
        )

        # ------------------------------------------------------------------
        # 9. Update scheduler state
        # ------------------------------------------------------------------
        self.active_schedule = schedule
        self.schedule_stale = False

        logger.info(
            "Scheduler.compute_schedule: solar_kwh=%.1f expected_kwh=%.1f "
            "charge_energy_kwh=%.1f huawei_target=%.1f victron_target=%.1f cost_eur=%.2f",
            solar_kwh,
            consumption.today_expected_kwh,
            charge_energy_kwh,
            huawei_target,
            victron_target,
            cost_estimate_eur,
        )

        # ------------------------------------------------------------------
        # 10. Write to InfluxDB (fire-and-forget, belt-and-suspenders)
        # ------------------------------------------------------------------
        if writer is not None:
            try:
                await writer.write_charge_schedule(schedule)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Scheduler.compute_schedule: writer.write_charge_schedule failed: %s", exc
                )

        return schedule
