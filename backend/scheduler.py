"""Charge schedule computation for the EMS battery pool (S03).

The :class:`Scheduler` fetches EVCC state, derives per-battery SoC targets,
selects the cheapest tariff window, and produces a :class:`ChargeSchedule`
stored on :attr:`Scheduler.active_schedule`.

Observability
-------------
- ``INFO "Scheduler wired — run_hour=N charge_window=M–K min"`` at startup
  (logged by ``main.py`` after construction).
- ``INFO "Scheduler.compute_schedule: evcc_state=None, marking schedule stale"``
  when EVCC is unreachable.
- ``INFO "Scheduler.compute_schedule: solar_kwh=X expected_kwh=Y
  net_charge_kwh=Z huawei_target=H victron_target=V"`` on every successful run.
- ``WARNING "consumption history: fallback_used=True — using seasonal estimate"``
  on cold-start or InfluxDB failure.
- ``WARNING "influx write_charge_schedule failed: ..."`` on InfluxDB write failure
  (re-raised from the writer for belt-and-suspenders logging).

Inspection surfaces
-------------------
- ``Scheduler.active_schedule``  — the last computed schedule (or None).
- ``Scheduler.schedule_stale``   — True when EVCC was unreachable on last run.
- InfluxDB ``ems_schedule`` measurement.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.config import OrchestratorConfig, SystemConfig
from backend.schedule_models import (
    ChargeSchedule,
    ChargeSlot,
    OptimizationReasoning,
)

logger = logging.getLogger("ems.scheduler")


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

        .. note::
            This method is a stub in T01.  Full implementation is in T02.

        Parameters
        ----------
        writer:
            Optional :class:`~backend.influx_writer.InfluxMetricsWriter`.
            When provided, the computed schedule is written to InfluxDB
            (fire-and-forget).

        Raises
        ------
        NotImplementedError
            Always — implementation delivered in T02.
        """
        raise NotImplementedError(
            "compute_schedule() not yet implemented — see T02"
        )
