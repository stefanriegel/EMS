#!/usr/bin/env python3
"""Live hardware probe script for Huawei SUN2000 / LUNA2000 via Modbus TCP.

Connects to the Modbus TCP proxy, reads every relevant register from both
slave IDs, and prints structured output.  Exits 0 even if individual
registers are unreadable (each register read is caught individually and
printed as an ``ERROR:`` line).  Exits 1 only on connection failure.

Intended use
------------
Run this against real hardware to retire the unit-ID / register-access risk
before wiring the driver into the orchestrator::

    python scripts/probe_huawei.py --host 192.168.0.10 --port 502

Set PYTHONLOG=DEBUG to see every Modbus call::

    PYTHONLOG=DEBUG python scripts/probe_huawei.py --host 192.168.0.10

Live-hardware verification checklist (manual UAT)
--------------------------------------------------
After running the probe:

1. ``slave_id=2 input_power`` shows a non-negative integer (watts) — PV DC input
2. ``slave_id=2 storage_unit_1_state_of_capacity`` shows 0–100 (% SoC)
3. ``slave_id=2 storage_unit_2_state_of_capacity`` shows 0–100 or ERROR (single-pack OK)
4. ``slave_id=8 active_power`` shows a non-zero integer when the slave is generating

Exit codes
----------
- 0 — probe completed (may include ERROR lines for individual registers)
- 1 — connection failure (cannot reach the Modbus proxy at all)
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import socket
import sys
from typing import Any

from huawei_solar import AsyncHuaweiSolar

_DEFAULT_CONNECT_TIMEOUT = 10


def _configure_logging() -> None:
    """Set up logging based on PYTHONLOG env var (DEBUG/INFO/WARNING/ERROR)."""
    level_name = os.environ.get("PYTHONLOG", "ERROR").upper()
    level = getattr(logging, level_name, logging.ERROR)
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _check_tcp_reachable(host: str, port: int, timeout_s: float) -> None:
    """Raise ConnectionRefusedError or OSError if the host:port is not reachable.

    This is a fast pre-flight check that avoids the huawei-solar library's
    internal reconnect loop (which logs to stderr and retries indefinitely
    if pymodbus connects at the TCP layer but the socket is immediately reset).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect((host, port))
    finally:
        sock.close()


async def _probe_register(
    client: AsyncHuaweiSolar,
    slave_id: int,
    register: str,
) -> Any:
    """Read a single register and return its value, or None on failure.

    Errors are printed to stdout as ``ERROR: <register> on slave <id>: <exc>``
    so they appear in the probe output alongside successful reads.
    """
    try:
        results = await client.get_multiple([register], slave_id=slave_id)
        return results[0].value
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {register} on slave {slave_id}: {type(exc).__name__}: {exc}")
        return None


async def _probe_slave(
    client: AsyncHuaweiSolar,
    slave_id: int,
    registers: list[str],
) -> None:
    """Read and print every register from a single slave ID."""
    for register in registers:
        value = await _probe_register(client, slave_id, register)
        if value is not None:
            print(f"slave_id={slave_id} {register}: {value}")


async def main(args: argparse.Namespace) -> int:
    """Probe logic — returns an integer exit code."""
    slave_ids = [int(s.strip()) for s in args.slave_ids.split(",")]

    # --- Pre-flight: TCP reachability check ---
    # The huawei-solar library's internal reconnect loop logs tracebacks to
    # stderr and retries indefinitely when pymodbus's TCP connect returns False.
    # A raw socket probe detects this condition cleanly before invoking the library.
    print(f"# Connecting to {args.host}:{args.port} …", flush=True)
    try:
        _check_tcp_reachable(args.host, args.port, args.connect_timeout)
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: {type(exc).__name__} connecting to {args.host}:{args.port}: {exc}",
            file=sys.stderr,
        )
        return 1

    # --- Full Modbus connection ---
    try:
        client = await asyncio.wait_for(
            AsyncHuaweiSolar.create(args.host, args.port, timeout=args.connect_timeout),
            timeout=args.connect_timeout + 2,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: {type(exc).__name__} connecting to {args.host}:{args.port}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"# Connected — probing slave IDs: {slave_ids}", flush=True)

    try:
        for slave_id in slave_ids:
            print(f"\n# --- slave_id={slave_id} ---", flush=True)

            if slave_id == 2:  # master inverter ID for this installation (K059)
                # Master inverter: PV + device status
                await _probe_slave(client, slave_id, [
                    "state_1",
                    "pv_01_voltage",
                    "pv_01_current",
                    "pv_02_voltage",
                    "pv_02_current",
                    "input_power",
                    "active_power",
                ])
                # Battery pack 1 + system limits
                await _probe_slave(client, slave_id, [
                    "storage_unit_1_running_status",
                    "storage_unit_1_charge_discharge_power",
                    "storage_unit_1_state_of_capacity",
                    "storage_unit_1_working_mode_b",
                    "storage_maximum_charge_power",
                    "storage_maximum_discharge_power",
                ])
                # Battery pack 2 (absent on single-LUNA2000 — per-register errors normal)
                print("# Pack 2 (absent on single-LUNA2000 — ERROR here is normal):")
                await _probe_slave(client, slave_id, [
                    "storage_unit_2_state_of_capacity",
                    "storage_unit_2_running_status",
                    "storage_unit_2_charge_discharge_power",
                    "storage_state_of_capacity",
                    "storage_charge_discharge_power",
                ])
            else:
                # Slave inverter: PV only
                await _probe_slave(client, slave_id, [
                    "state_1",
                    "pv_01_voltage",
                    "pv_01_current",
                    "pv_02_voltage",
                    "pv_02_current",
                    "input_power",
                    "active_power",
                ])

        print("\n# Probe complete.")
        return 0

    finally:
        with contextlib.suppress(Exception):
            await client.stop()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="probe_huawei",
        description=(
            "Live hardware probe for Huawei SUN2000 / LUNA2000 via Modbus TCP. "
            "Reads all relevant registers from each requested slave ID, printing "
            "each result to stdout. "
            "Exits 1 on connection failure; exits 0 otherwise (even if some "
            "registers returned ERROR)."
        ),
    )
    parser.add_argument(
        "--host",
        required=True,
        help="IP address or hostname of the Modbus TCP proxy",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=502,
        help="TCP port (default: 502)",
    )
    parser.add_argument(
        "--slave-ids",
        default="2,8",
        help="Comma-separated Modbus slave IDs to probe (default: '2,8')",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=_DEFAULT_CONNECT_TIMEOUT,
        help=f"Connection timeout in seconds (default: {_DEFAULT_CONNECT_TIMEOUT})",
    )
    return parser


if __name__ == "__main__":
    _configure_logging()

    parser = _build_parser()
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\n# Interrupted.", file=sys.stderr)
        exit_code = 1
    except Exception as exc:  # noqa: BLE001
        # Unexpected top-level error — structured output to stderr, no traceback
        print(
            f"ERROR: {type(exc).__name__} — probe aborted: {exc}",
            file=sys.stderr,
        )
        exit_code = 1

    sys.exit(exit_code)
