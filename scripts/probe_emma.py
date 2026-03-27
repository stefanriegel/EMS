#!/usr/bin/env python3
"""Diagnostic probe for the EMMA Smart Energy Controller.

Reads all EmmaSnapshot fields, optionally writes ESS mode 6
(third-party dispatch), waits, reads back to confirm, then restores
the original mode.

Usage::

    python scripts/probe_emma.py
    python scripts/probe_emma.py --host 127.0.0.1 --port 502
    python scripts/probe_emma.py --host 127.0.0.1 --skip-write

Exit codes:
    0  All steps passed
    1  Connection failure or read failure
    2  Write/restore failed (ESS mode not confirmed)
"""
from __future__ import annotations

import argparse
import asyncio
import sys

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Probe EMMA Smart Energy Controller via Modbus TCP",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--host", default="127.0.0.1", help="EMMA Modbus TCP host")
    p.add_argument("--port", type=int, default=502, help="EMMA Modbus TCP port")
    p.add_argument(
        "--device-id", type=int, default=0, dest="device_id",
        help="Modbus device/unit ID for EMMA (always 0 in standard setups)",
    )
    p.add_argument(
        "--skip-write", action="store_true",
        help="Skip the ESS mode write/restore test (read-only probe)",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# PASS / FAIL helpers
# ---------------------------------------------------------------------------

_PASS = "\033[32m PASS\033[0m"
_FAIL = "\033[31m FAIL\033[0m"


def _check(label: str, cond: bool, detail: str = "") -> bool:
    status = _PASS if cond else _FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return cond


# ---------------------------------------------------------------------------
# Main probe
# ---------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> int:
    from backend.drivers.emma_driver import EmmaDriver, EmmaSnapshot

    print(f"\nEMMA probe: {args.host}:{args.port} (device_id={args.device_id})")
    print("=" * 60)

    driver = EmmaDriver(
        host=args.host,
        port=args.port,
        device_id=args.device_id,
        timeout_s=10.0,
    )

    # --- Connect ---
    print("\n[1] Connect")
    try:
        await driver.connect()
        print(f"  [{_PASS}] Connected to {args.host}:{args.port}")
    except Exception as exc:
        print(f"  [{_FAIL}] Connection failed: {exc}")
        return 1

    # --- Read all registers ---
    print("\n[2] Read all EmmaSnapshot fields")
    try:
        snap = await driver._read_all()
    except Exception as exc:
        print(f"  [{_FAIL}] Read failed: {exc}")
        await driver.close()
        return 1

    ok = True
    ok &= _check("pv_power_w is int",           isinstance(snap.pv_power_w, int),
                 f"{snap.pv_power_w} W")
    ok &= _check("load_power_w is int",          isinstance(snap.load_power_w, int),
                 f"{snap.load_power_w} W")
    ok &= _check("feed_in_power_w is int",       isinstance(snap.feed_in_power_w, int),
                 f"{snap.feed_in_power_w} W")
    ok &= _check("battery_power_w is int",       isinstance(snap.battery_power_w, int),
                 f"{snap.battery_power_w} W")
    ok &= _check("battery_soc_pct in [0,100]",   0.0 <= snap.battery_soc_pct <= 100.0,
                 f"{snap.battery_soc_pct:.1f}%")
    ok &= _check("pv_yield_today_kwh >= 0",      snap.pv_yield_today_kwh >= 0.0,
                 f"{snap.pv_yield_today_kwh:.2f} kWh")
    ok &= _check("consumption_today_kwh >= 0",   snap.consumption_today_kwh >= 0.0,
                 f"{snap.consumption_today_kwh:.2f} kWh")
    ok &= _check("charged_today_kwh >= 0",       snap.charged_today_kwh >= 0.0,
                 f"{snap.charged_today_kwh:.2f} kWh")
    ok &= _check("discharged_today_kwh >= 0",    snap.discharged_today_kwh >= 0.0,
                 f"{snap.discharged_today_kwh:.2f} kWh")
    ok &= _check("chargeable_energy_kwh >= 0",   snap.chargeable_energy_kwh >= 0.0,
                 f"{snap.chargeable_energy_kwh:.2f} kWh")
    ok &= _check("dischargeable_energy_kwh >= 0",snap.dischargeable_energy_kwh >= 0.0,
                 f"{snap.dischargeable_energy_kwh:.2f} kWh")
    ok &= _check("ess_control_mode in {2,5,6}",  snap.ess_control_mode in {2, 5, 6},
                 f"mode={snap.ess_control_mode}")

    if not ok:
        print("\n  ⚠️  Some field checks failed — check register map vs firmware version")

    print(f"\n  Full snapshot:")
    for field_name, value in snap.__dict__.items():
        print(f"    {field_name:35s} = {value}")

    # --- ESS mode write / restore ---
    if args.skip_write:
        print("\n[3] ESS mode write/restore  (SKIPPED — --skip-write)")
        await driver.close()
        return 0 if ok else 1

    original_mode = snap.ess_control_mode
    print(f"\n[3] ESS mode write/restore (original mode={original_mode})")

    write_ok = True

    # Write mode 6
    try:
        await driver.write_ess_mode(6)
        print(f"  [ OK ] write_ess_mode(6) succeeded")
    except Exception as exc:
        print(f"  [{_FAIL}] write_ess_mode(6) failed: {exc}")
        write_ok = False

    if write_ok:
        await asyncio.sleep(2.0)
        try:
            readback = await driver._read_all()
            write_ok &= _check("ESS mode reads back as 6",
                               readback.ess_control_mode == 6,
                               f"got {readback.ess_control_mode}")
        except Exception as exc:
            print(f"  [{_FAIL}] readback after write failed: {exc}")
            write_ok = False

    # Restore original mode
    try:
        await driver.write_ess_mode(original_mode)
        print(f"  [ OK ] write_ess_mode({original_mode}) (restore) succeeded")
    except Exception as exc:
        print(f"  [{_FAIL}] restore write_ess_mode({original_mode}) failed: {exc}")
        write_ok = False

    if write_ok:
        await asyncio.sleep(2.0)
        try:
            restored = await driver._read_all()
            write_ok &= _check(
                f"ESS mode restored to {original_mode}",
                restored.ess_control_mode == original_mode,
                f"got {restored.ess_control_mode}",
            )
        except Exception as exc:
            print(f"  [{_FAIL}] readback after restore failed: {exc}")
            write_ok = False

    await driver.close()

    print("\n" + "=" * 60)
    all_ok = ok and write_ok
    print(f"Result: {'ALL PASS ✅' if all_ok else 'SOME CHECKS FAILED ❌'}")
    return 0 if all_ok else 2


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(asyncio.run(main(args)))
