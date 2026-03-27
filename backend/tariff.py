"""Composite tariff engine — merges Octopus Go supply rates with §14a EnWG
Modul 3 Netzgebühren to produce a unified effective electricity price.

All internal time arithmetic uses minutes-from-midnight integers [0, 1440) to
avoid the midnight-crossing pitfall with ``datetime.time`` comparisons (the
Octopus Go peak window wraps midnight: it is active in [0, 30) and [330,
1440), so ``time(5, 30) > time(0, 30)`` cannot represent it as a single
half-open interval).

Observability:
    - :class:`CompositeTariffEngine` logs the validated window schedule to
      ``logging.getLogger("ems.tariff")`` at DEBUG level on successful
      construction, and logs the rejected config at ERROR level on
      :exc:`ValueError`.
    - ``get_effective_price`` and ``get_price_schedule`` log at DEBUG level
      so unexpected outputs are traceable without changing production log
      verbosity.
    - Construction failure state is exposed as a ``ValueError`` with a message
      identifying the first uncovered or double-covered minute — allowing the
      lifespan startup hook to surface the misconfiguration immediately.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .tariff_models import Modul3Config, OctopusGoConfig, TariffSlot

log = logging.getLogger("ems.tariff")


class CompositeTariffEngine:
    """Composite electricity price engine for Octopus Go + Modul 3.

    Args:
        octopus: Octopus Go supply tariff configuration.
        modul3: §14a EnWG Modul 3 Netzgebühren configuration.

    Raises:
        ValueError: If the Modul 3 windows do not exactly partition [0, 1440)
            minutes — i.e. any minute is uncovered or covered more than once.
    """

    def __init__(self, octopus: OctopusGoConfig, modul3: Modul3Config) -> None:
        self._octopus = octopus
        self._modul3 = modul3
        self._validate_modul3()
        log.debug(
            "CompositeTariffEngine initialised: octopus_tz=%s modul3_tz=%s windows=%d",
            octopus.timezone,
            modul3.timezone,
            len(modul3.windows),
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_modul3(self) -> None:
        """Validate that Modul3 windows partition exactly [0, 1440) minutes.

        Sorts by start_min and checks for gaps and overlaps sequentially.
        Raises a descriptive :exc:`ValueError` identifying the first
        problematic minute so misconfiguration can be fixed quickly.
        """
        windows = sorted(self._modul3.windows, key=lambda w: w.start_min)
        cursor = 0
        for w in windows:
            if w.start_min > cursor:
                log.error(
                    "Modul3 config invalid: gap at minutes %d–%d", cursor, w.start_min
                )
                raise ValueError(
                    f"Modul3 windows have a gap: minutes {cursor}–{w.start_min} "
                    f"(i.e. {cursor // 60:02d}:{cursor % 60:02d}–"
                    f"{w.start_min // 60:02d}:{w.start_min % 60:02d}) are uncovered"
                )
            if w.start_min < cursor:
                log.error(
                    "Modul3 config invalid: overlap at minute %d (window starts at %d)",
                    cursor,
                    w.start_min,
                )
                raise ValueError(
                    f"Modul3 windows overlap at minute {w.start_min} "
                    f"(previous window already covered up to minute {cursor})"
                )
            cursor = w.end_min

        if cursor != 1440:
            log.error(
                "Modul3 config invalid: coverage ends at minute %d, expected 1440",
                cursor,
            )
            raise ValueError(
                f"Modul3 windows end at minute {cursor} but must cover up to "
                f"1440 (i.e. through midnight); "
                f"{1440 - cursor} minute(s) uncovered"
            )

    # ------------------------------------------------------------------
    # Rate lookups (pure, no I/O)
    # ------------------------------------------------------------------

    def _octopus_rate_at(self, minute: int) -> float:
        """Return the Octopus Go rate (€/kWh) for ``minute`` minutes past midnight.

        The off-peak window is ``[off_peak_start_min, off_peak_end_min)``.
        All other minutes are peak.
        """
        cfg = self._octopus
        if cfg.off_peak_start_min <= minute < cfg.off_peak_end_min:
            return cfg.off_peak_rate_eur_kwh
        return cfg.peak_rate_eur_kwh

    def _modul3_rate_at(self, minute: int) -> float:
        """Return the Modul 3 grid-fee adder (€/kWh) for ``minute`` past midnight.

        Iterates the validated window list; the list is guaranteed to cover
        every minute in [0, 1440) so a ``RuntimeError`` here would indicate
        a validation bug.
        """
        for w in self._modul3.windows:
            if w.start_min <= minute < w.end_min:
                return w.rate_eur_kwh
        # Should never happen after validation — expose as a clear error
        raise RuntimeError(
            f"Modul3 windows do not cover minute {minute} — this is a validation bug"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_effective_price(self, dt: datetime) -> float:
        """Return the composite electricity price in €/kWh for a given instant.

        Args:
            dt: The instant to price.  May be timezone-aware or naive.
                Naive datetimes are interpreted as wall-clock times and
                localised to ``octopus.timezone`` first.

        Returns:
            ``octopus_rate_eur_kwh + modul3_rate_eur_kwh`` at ``dt``.
        """
        oct_tz = ZoneInfo(self._octopus.timezone)
        m3_tz = ZoneInfo(self._modul3.timezone)

        # Localise to each config's timezone for wall-clock minute extraction
        if dt.tzinfo is None:
            dt_oct = dt.replace(tzinfo=oct_tz)
        else:
            dt_oct = dt.astimezone(oct_tz)

        dt_m3 = dt_oct.astimezone(m3_tz)

        oct_minute = dt_oct.hour * 60 + dt_oct.minute
        m3_minute = dt_m3.hour * 60 + dt_m3.minute

        octopus_rate = self._octopus_rate_at(oct_minute)
        modul3_rate = self._modul3_rate_at(m3_minute)
        effective = octopus_rate + modul3_rate

        log.debug(
            "get_effective_price dt=%s oct_min=%d m3_min=%d "
            "oct_rate=%.4f m3_rate=%.4f effective=%.4f",
            dt.isoformat(),
            oct_minute,
            m3_minute,
            octopus_rate,
            modul3_rate,
            effective,
        )
        return effective

    def get_price_schedule(self, target_date: date) -> list[TariffSlot]:
        """Return all distinct price slots for a given calendar date.

        The slots are contiguous and together cover exactly 24 wall-clock
        hours from midnight to midnight (in ``octopus.timezone``).  On DST
        transition days the UTC duration of the day may differ from 1440
        minutes, but the wall-clock boundaries are always correct.

        Args:
            target_date: The calendar date to build a schedule for.

        Returns:
            List of :class:`TariffSlot` objects sorted by ``start``, with no
            gaps and no overlaps, spanning ``00:00``–``24:00`` in
            ``octopus.timezone``.
        """
        oct_tz = ZoneInfo(self._octopus.timezone)
        m3_tz = ZoneInfo(self._modul3.timezone)

        # Collect all boundary minutes from both configs (in Octopus timezone).
        # Octopus Go: off_peak_start_min and off_peak_end_min.
        # Modul3: every window start_min, converted from Berlin to London.
        boundary_minutes: set[int] = {0, 1440}
        boundary_minutes.add(self._octopus.off_peak_start_min)
        boundary_minutes.add(self._octopus.off_peak_end_min)

        # Convert each Modul3 window boundary from Berlin wall-clock to London
        # wall-clock on this specific date to account for timezone offset
        # differences (Berlin is UTC+1/+2, London is UTC+0/+1).
        for w in self._modul3.windows:
            for minute in (w.start_min, w.end_min):
                # Build a tz-aware datetime in Berlin for this boundary
                bnd_hour, bnd_min = divmod(minute, 60)
                if bnd_hour == 24:
                    # end_min==1440 → midnight of next day in Berlin
                    bnd_dt_m3 = datetime.combine(
                        target_date + timedelta(days=1), time(0, 0), tzinfo=m3_tz
                    )
                else:
                    bnd_dt_m3 = datetime.combine(
                        target_date, time(bnd_hour, bnd_min), tzinfo=m3_tz
                    )
                # Convert to London and get minutes-from-midnight (clamped)
                bnd_dt_oct = bnd_dt_m3.astimezone(oct_tz)
                # Only include boundaries that fall within this day's window
                oct_ref = datetime.combine(target_date, time(0, 0), tzinfo=oct_tz)
                delta_s = (bnd_dt_oct - oct_ref).total_seconds()
                delta_min = int(delta_s / 60)
                if 0 <= delta_min <= 1440:
                    boundary_minutes.add(delta_min)

        sorted_boundaries = sorted(boundary_minutes)

        slots: list[TariffSlot] = []
        oct_midnight = datetime.combine(target_date, time(0, 0), tzinfo=oct_tz)

        for i in range(len(sorted_boundaries) - 1):
            start_min = sorted_boundaries[i]
            end_min = sorted_boundaries[i + 1]

            slot_start = oct_midnight + timedelta(minutes=start_min)
            slot_end = oct_midnight + timedelta(minutes=end_min)

            # Use the midpoint minute for rate lookups to avoid ambiguity at
            # boundaries (the start of each interval determines the rate).
            mid_min = start_min  # inclusive start → correct lookup point

            oct_rate = self._octopus_rate_at(mid_min % 1440)

            # Modul3 lookup: convert slot start to Berlin
            slot_start_m3 = slot_start.astimezone(m3_tz)
            m3_minute = slot_start_m3.hour * 60 + slot_start_m3.minute
            m3_rate = self._modul3_rate_at(m3_minute)

            effective = oct_rate + m3_rate
            slots.append(
                TariffSlot(
                    start=slot_start,
                    end=slot_end,
                    octopus_rate_eur_kwh=oct_rate,
                    modul3_rate_eur_kwh=m3_rate,
                    effective_rate_eur_kwh=effective,
                )
            )

        log.debug(
            "get_price_schedule date=%s slots=%d first_start=%s last_end=%s",
            target_date.isoformat(),
            len(slots),
            slots[0].start.isoformat() if slots else "n/a",
            slots[-1].end.isoformat() if slots else "n/a",
        )
        return slots


class EvccTariffEngine:
    """Tariff engine driven entirely by EVCC's grid-price timeseries.

    EVCC already computes the fully-inclusive import price (supply rate +
    all grid fees).  This engine caches the latest ``GridPriceSeries``
    received from EVCC and exposes the same ``get_effective_price`` /
    ``get_price_schedule`` interface as ``CompositeTariffEngine``, falling
    back to the composite engine when no EVCC prices are available.

    The scheduler calls :meth:`update` after each successful EVCC fetch;
    the engine is otherwise stateless.

    Parameters
    ----------
    fallback:
        :class:`CompositeTariffEngine` used when no EVCC prices have
        been received yet.
    """

    def __init__(self, fallback: "CompositeTariffEngine") -> None:
        self._fallback = fallback
        self._grid_prices: "GridPriceSeries | None" = None  # type: ignore[name-defined]

    # ------------------------------------------------------------------
    # Delegate attributes so existing api.py / WS code keeps working
    # ------------------------------------------------------------------

    @property
    def _octopus(self):  # type: ignore[return]
        return self._fallback._octopus

    @property
    def _modul3(self):  # type: ignore[return]
        return self._fallback._modul3

    def _octopus_rate_at(self, minute: int) -> float:
        return self._fallback._octopus_rate_at(minute)

    def _modul3_rate_at(self, minute: int) -> float:
        return self._fallback._modul3_rate_at(minute)

    # ------------------------------------------------------------------
    # Mutator — called by the scheduler
    # ------------------------------------------------------------------

    def update(self, grid_prices: "GridPriceSeries") -> None:  # type: ignore[name-defined]
        """Replace the cached EVCC grid-price series."""
        self._grid_prices = grid_prices

    # ------------------------------------------------------------------
    # Public tariff API
    # ------------------------------------------------------------------

    def get_effective_price(self, dt: datetime) -> float:
        """Return the effective import price in €/kWh at *dt*.

        Looks up *dt* in the cached EVCC timeseries.  Falls back to the
        composite engine when no series is available.
        """
        gp = self._grid_prices
        if gp is None or not gp.import_eur_kwh:
            log.debug("EvccTariffEngine: no EVCC prices — fallback for dt=%s", dt)
            return self._fallback.get_effective_price(dt)

        from datetime import timezone as _tz
        dt_utc = dt if dt.tzinfo is not None else dt.replace(tzinfo=_tz.utc)

        # Walk timeseries to find current slot
        price = gp.import_eur_kwh[-1]  # default: last known
        for i, ts in enumerate(gp.slot_timestamps_utc):
            if i + 1 < len(gp.slot_timestamps_utc):
                if ts <= dt_utc < gp.slot_timestamps_utc[i + 1]:
                    price = gp.import_eur_kwh[i]
                    break
            elif ts <= dt_utc:
                price = gp.import_eur_kwh[i]

        log.debug("EvccTariffEngine: dt=%s price=%.6f", dt, price)
        return price

    def get_price_schedule(self, target_date: date) -> list[TariffSlot]:
        """Return tariff slots for *target_date* from EVCC prices.

        Aligns the EVCC 15-minute timeseries to the requested date,
        merges contiguous slots at the same price into half-hour blocks,
        and falls back to :class:`CompositeTariffEngine` when no EVCC
        data covers the date.
        """
        gp = self._grid_prices
        if gp is None or not gp.import_eur_kwh:
            log.debug("EvccTariffEngine: no EVCC prices — fallback for date=%s", target_date)
            return self._fallback.get_price_schedule(target_date)

        from datetime import timezone as _tz
        oct_tz = ZoneInfo(self._fallback._octopus.timezone)
        day_start = datetime.combine(target_date, time(0, 0), tzinfo=oct_tz)
        day_end = day_start + timedelta(days=1)

        # Filter EVCC slots that fall within target_date
        slots: list[TariffSlot] = []
        for i, ts in enumerate(gp.slot_timestamps_utc):
            ts_local = ts.astimezone(oct_tz)
            if not (day_start <= ts_local < day_end):
                continue
            # Determine slot end: next slot start or 15 min
            if i + 1 < len(gp.slot_timestamps_utc):
                slot_end = gp.slot_timestamps_utc[i + 1].astimezone(oct_tz)
                # Cap at day boundary
                if slot_end > day_end:
                    slot_end = day_end
            else:
                slot_end = ts_local + timedelta(minutes=15)

            price = gp.import_eur_kwh[i]
            slots.append(TariffSlot(
                start=ts_local,
                end=slot_end,
                octopus_rate_eur_kwh=price,
                modul3_rate_eur_kwh=0.0,
                effective_rate_eur_kwh=price,
            ))

        if not slots:
            log.debug("EvccTariffEngine: no slots for date=%s — fallback", target_date)
            return self._fallback.get_price_schedule(target_date)

        log.debug("EvccTariffEngine: date=%s slots=%d from EVCC", target_date, len(slots))
        return slots
