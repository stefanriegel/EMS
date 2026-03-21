#!/usr/bin/env python3
"""Operator smoke test for the HA SQLite ML consumption forecaster.

Usage
-----
    python scripts/ha_forecast_smoke.py [--db-path /path/to/home-assistant_v2.db]

Defaults to ``$HA_DB_PATH`` env var, or ``/config/home-assistant_v2.db`` when
neither is set.

Exit codes
----------
0 — DB found and forecast completed (or cold-start fallback used)
1 — DB file not found at the given path
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Ensure the project root is on sys.path when run as a standalone script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.config import HaStatisticsConfig
from backend.consumption_forecaster import ConsumptionForecaster
from backend.ha_statistics_reader import HaStatisticsReader
from backend.influx_reader import _seasonal_fallback_kwh  # noqa: PLC2701


def _parse_args() -> argparse.Namespace:
    default_path = os.environ.get("HA_DB_PATH", "/config/home-assistant_v2.db")
    parser = argparse.ArgumentParser(
        description="Smoke-test the HA SQLite ML consumption forecaster.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db-path",
        default=default_path,
        help="Path to the Home Assistant SQLite database (home-assistant_v2.db).",
    )
    return parser.parse_args()


async def _run(db_path: str) -> int:
    print(f"DB path: {db_path}")

    if not os.path.isfile(db_path):
        print(f"ERROR: HA DB not found at {db_path!r}")
        print("Hint: set HA_DB_PATH or pass --db-path to point at your database.")
        return 1

    print(f"OK: DB found at {db_path!r}")

    # Build config from env (honours all HA_STAT_* overrides)
    cfg = HaStatisticsConfig.from_env()
    cfg.db_path = db_path  # override with CLI arg

    reader = HaStatisticsReader(db_path)

    # --- Schema version check ---
    schema_version = await reader.check_schema_version()
    print(f"Schema version: {schema_version if schema_version is not None else '(table absent — older HA)'}")

    # --- Row count probe per entity ---
    entities = {
        "outdoor_temp": cfg.outdoor_temp_entity,
        "heat_pump":    cfg.heat_pump_entity,
        "dhw":          cfg.dhw_entity,
    }
    row_counts: dict[str, int] = {}
    for label, entity_id in entities.items():
        rows = await reader.read_entity_hourly(entity_id, days=30)
        row_counts[label] = len(rows)
        print(f"Entity {label!r:16s} ({entity_id}): {len(rows):5d} hourly rows (last 30 days)")

    # --- Train and forecast ---
    min_days = cfg.min_training_days
    forecaster = ConsumptionForecaster(reader, cfg)
    print(f"\nMin training days required: {min_days}")

    await forecaster.train()

    forecast = await forecaster.query_consumption_history()

    if forecast.fallback_used:
        days_have = forecast.days_of_history
        days_need = max(0, min_days - days_have)
        print(
            f"\nCold-start fallback used (days_of_history={days_have} < min={min_days})."
        )
        print(
            f"  Need {days_need} more day(s) of HA history before ML models activate."
        )
        print(f"  Fallback value (seasonal): {forecast.today_expected_kwh:.2f} kWh")
        seasonal = _seasonal_fallback_kwh()
        print(f"  Seasonal fallback (direct call): {seasonal:.2f} kWh")
    else:
        print(f"\nML forecast:")
        print(f"  {forecaster.reasoning_text}")
        print(f"  today_expected_kwh : {forecast.today_expected_kwh:.2f} kWh")
        print(f"  days_of_history    : {forecast.days_of_history}")
        seasonal = _seasonal_fallback_kwh()
        print(f"  Seasonal fallback  : {seasonal:.2f} kWh  (for comparison)")

    return 0


def main() -> None:
    args = _parse_args()
    sys.exit(asyncio.run(_run(args.db_path)))


if __name__ == "__main__":
    main()
