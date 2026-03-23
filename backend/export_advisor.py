"""ExportAdvisor -- decides whether PV surplus should be exported or stored.

Compares the fixed feed-in tariff rate against upcoming import costs and
forecasted consumption to make an economically optimal decision.  The advisor
only handles *surplus PV* -- it never suggests discharging batteries to grid.

Decision logic:
    1. If combined SoC < 90 %: STORE (batteries not full enough)
    2. If forecaster unavailable or fallback used: STORE (conservative)
    3. Compute forward reserve (kWh needed in next 6 expensive hours)
    4. If available battery kWh minus forward reserve > 0: EXPORT
    5. Otherwise: STORE

Observability:
    Logger name: ``ems.export_advisor``
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.config import SystemConfig
    from backend.consumption_forecaster import ConsumptionForecaster
    from backend.tariff import CompositeTariffEngine

logger = logging.getLogger("ems.export_advisor")

# Total pool capacity in kWh (Huawei 30 + Victron 64)
_TOTAL_POOL_KWH: float = 94.0

# SoC threshold below which we always STORE
_SOC_THRESHOLD_PCT: float = 90.0

# Number of forward-looking hours for reserve calculation
_FORWARD_HOURS: int = 6


class ExportDecision(str, Enum):
    """Whether PV surplus should be stored in batteries or exported to grid."""

    STORE = "STORE"
    """Keep charging batteries with surplus PV."""

    EXPORT = "EXPORT"
    """Allow surplus PV to flow to grid (earning feed-in tariff)."""


@dataclass
class ExportAdvice:
    """Result of an export advisory computation.

    Attributes:
        decision: STORE or EXPORT.
        reasoning: Human-readable explanation including all key factors.
        feed_in_rate: Fixed feed-in tariff rate in EUR/kWh.
        import_rate: Current import electricity rate in EUR/kWh.
        forecast_demand_kwh: Forecasted demand for the forward window in kWh.
        battery_soc_pct: Combined battery pool SoC percentage.
    """

    decision: ExportDecision
    reasoning: str
    feed_in_rate: float
    import_rate: float
    forecast_demand_kwh: float
    battery_soc_pct: float


class ExportAdvisor:
    """Advisory engine for PV surplus export vs. store decisions.

    Parameters
    ----------
    tariff_engine:
        Composite tariff engine for import price lookups.
    forecaster:
        Consumption forecaster, or ``None`` if unavailable.
    sys_config:
        System configuration containing ``feed_in_rate_eur_kwh``.
    """

    def __init__(
        self,
        tariff_engine: "CompositeTariffEngine",
        forecaster: "ConsumptionForecaster | None",
        sys_config: "SystemConfig",
    ) -> None:
        self._tariff_engine = tariff_engine
        self._forecaster = forecaster
        self._sys_config = sys_config
        self._cached_forecast = None  # type: ignore[assignment]

    def advise(
        self,
        combined_soc_pct: float,
        huawei_soc_pct: float,
        victron_soc_pct: float,
        now: datetime,
    ) -> ExportAdvice:
        """Compute an export/store recommendation for the current instant.

        Parameters
        ----------
        combined_soc_pct:
            Weighted combined SoC of both battery systems (0-100).
        huawei_soc_pct:
            Huawei battery SoC percentage.
        victron_soc_pct:
            Victron battery SoC percentage.
        now:
            Current wall-clock time (timezone-aware).

        Returns
        -------
        ExportAdvice
            Always returns a result -- never raises.
        """
        feed_in_rate = self._sys_config.feed_in_rate_eur_kwh
        import_rate = self._tariff_engine.get_effective_price(now)

        # --- Gate 1: SoC threshold ---
        if combined_soc_pct < _SOC_THRESHOLD_PCT:
            reasoning = (
                f"SoC {combined_soc_pct:.1f}% < {_SOC_THRESHOLD_PCT:.0f}% threshold; "
                f"feed-in={feed_in_rate:.4f}, import={import_rate:.4f}, "
                f"demand=n/a, soc={combined_soc_pct:.1f}%"
            )
            logger.debug("ExportAdvisor: STORE (SoC below threshold) — %s", reasoning)
            return ExportAdvice(
                decision=ExportDecision.STORE,
                reasoning=reasoning,
                feed_in_rate=feed_in_rate,
                import_rate=import_rate,
                forecast_demand_kwh=0.0,
                battery_soc_pct=combined_soc_pct,
            )

        # --- Gate 2: Forecaster availability ---
        if self._forecaster is None:
            reasoning = (
                f"Forecaster unavailable, defaulting to STORE; "
                f"feed-in={feed_in_rate:.4f}, import={import_rate:.4f}, "
                f"demand=unknown, soc={combined_soc_pct:.1f}%"
            )
            logger.info("ExportAdvisor: STORE (forecaster unavailable)")
            return ExportAdvice(
                decision=ExportDecision.STORE,
                reasoning=reasoning,
                feed_in_rate=feed_in_rate,
                import_rate=import_rate,
                forecast_demand_kwh=0.0,
                battery_soc_pct=combined_soc_pct,
            )

        # --- Gate 3: Forecast quality ---
        if self._cached_forecast is None or self._cached_forecast.fallback_used:
            reason_detail = (
                "no forecast cached" if self._cached_forecast is None
                else "forecast fallback used"
            )
            reasoning = (
                f"{reason_detail}, defaulting to STORE; "
                f"feed-in={feed_in_rate:.4f}, import={import_rate:.4f}, "
                f"demand=uncertain, soc={combined_soc_pct:.1f}%"
            )
            logger.info("ExportAdvisor: STORE (%s)", reason_detail)
            return ExportAdvice(
                decision=ExportDecision.STORE,
                reasoning=reasoning,
                feed_in_rate=feed_in_rate,
                import_rate=import_rate,
                forecast_demand_kwh=0.0,
                battery_soc_pct=combined_soc_pct,
            )

        # --- Forward reserve calculation ---
        forward_reserve_kwh = self._compute_forward_reserve_kwh(now, feed_in_rate)
        available_kwh = (combined_soc_pct / 100.0) * _TOTAL_POOL_KWH
        surplus_kwh = available_kwh - forward_reserve_kwh

        if surplus_kwh > 0:
            decision = ExportDecision.EXPORT
            logger.info(
                "ExportAdvisor: EXPORT — surplus=%.1f kWh "
                "(available=%.1f, reserve=%.1f)",
                surplus_kwh, available_kwh, forward_reserve_kwh,
            )
        else:
            decision = ExportDecision.STORE
            logger.info(
                "ExportAdvisor: STORE — deficit=%.1f kWh "
                "(available=%.1f, reserve=%.1f)",
                surplus_kwh, available_kwh, forward_reserve_kwh,
            )

        reasoning = (
            f"feed-in={feed_in_rate:.4f}, import={import_rate:.4f}, "
            f"forecast_demand={forward_reserve_kwh:.1f} kWh, "
            f"soc={combined_soc_pct:.1f}%, "
            f"available={available_kwh:.1f} kWh, surplus={surplus_kwh:.1f} kWh"
        )

        return ExportAdvice(
            decision=decision,
            reasoning=reasoning,
            feed_in_rate=feed_in_rate,
            import_rate=import_rate,
            forecast_demand_kwh=forward_reserve_kwh,
            battery_soc_pct=combined_soc_pct,
        )

    def _compute_forward_reserve_kwh(
        self,
        now: datetime,
        feed_in_rate: float,
    ) -> float:
        """Estimate kWh needed from batteries in the next expensive hours.

        Looks at the next ``_FORWARD_HOURS`` of the tariff schedule and counts
        hours where the import rate exceeds the feed-in rate.  For those hours,
        estimates consumption from the cached forecast.

        Returns
        -------
        float
            Estimated kWh the batteries should reserve for expensive hours.
        """
        if self._cached_forecast is None:
            return 0.0

        try:
            schedule = self._tariff_engine.get_price_schedule(now.date())
        except Exception:
            logger.warning("ExportAdvisor: failed to get price schedule")
            return 0.0

        # Count expensive hours in the forward window
        expensive_hours = 0
        for slot in schedule:
            # Only consider slots that start within the forward window
            if slot.start < now:
                continue
            hours_ahead = (slot.start - now).total_seconds() / 3600
            if hours_ahead > _FORWARD_HOURS:
                break
            if slot.effective_rate_eur_kwh > feed_in_rate:
                slot_duration_h = (slot.end - slot.start).total_seconds() / 3600
                expensive_hours += slot_duration_h

        # Estimate consumption per hour from the daily forecast
        hourly_consumption_kwh = self._cached_forecast.today_expected_kwh / 24.0

        return expensive_hours * hourly_consumption_kwh

    async def refresh_forecast(self) -> None:
        """Update the cached consumption forecast from the forecaster.

        Safe to call even if forecaster is ``None`` -- returns silently.
        On error, logs a warning and keeps the stale cache.
        """
        if self._forecaster is None:
            return

        try:
            self._cached_forecast = await self._forecaster.query_consumption_history()
            logger.debug(
                "ExportAdvisor: forecast refreshed — today_expected=%.1f kWh",
                self._cached_forecast.today_expected_kwh,
            )
        except Exception:
            logger.warning(
                "ExportAdvisor: failed to refresh forecast, keeping stale cache",
                exc_info=True,
            )
