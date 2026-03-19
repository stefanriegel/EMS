#!/usr/bin/env python3
"""Live hardware probe for the Victron Multiplus II 3-phase system via MQTT.

Connects to the Venus OS dbus-flashmq MQTT broker, discovers the
``portalId`` and vebus ``instanceId``, subscribes to all telemetry topics,
waits for data to accumulate, then prints a table of all
``VictronSystemData`` fields.

Usage::

    python scripts/probe_victron.py --host 192.168.0.10 --port 1883

Options::

    --test-write   Write L1 AcPowerSetpoint=0 (safe no-op) and verify
                   the readback matches.

Exit codes
----------
- 0 — probe completed successfully
- 1 — connection failure, discovery timeout, or any unexpected error

Debugging tips
--------------
Set ``PYTHONLOG=DEBUG`` to see every MQTT message topic and value::

    PYTHONLOG=DEBUG python scripts/probe_victron.py --host 192.168.0.10

The module logger ``backend.drivers.victron_driver`` at DEBUG shows every
publish and subscribe call.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys

_DEFAULT_PORT = 1883
_DEFAULT_CONNECT_TIMEOUT = 3
_DEFAULT_DATA_TIMEOUT = 10


def _configure_logging() -> None:
    """Set up logging from PYTHONLOG env var (DEBUG/INFO/WARNING/ERROR)."""
    level_name = os.environ.get("PYTHONLOG", "ERROR").upper()
    level = getattr(logging, level_name, logging.ERROR)
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _check_tcp_reachable(host: str, port: int, timeout_s: float) -> None:
    """Verify TCP connectivity — raises on failure.

    Mirrors the pre-flight check in ``probe_huawei.py``.  Callers handle
    the exception and emit a structured error line to stderr.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout_s)
    try:
        s.connect((host, port))
    finally:
        s.close()


async def main(args: argparse.Namespace) -> int:
    """Probe logic — returns an integer exit code."""
    # Import here so the module is importable for unit testing even if paho is
    # not yet installed in the caller's environment.
    from backend.drivers.victron_driver import VictronDriver

    # --- Pre-flight: raw TCP reachability ---
    print(f"# Connecting to {args.host}:{args.port} …", flush=True)
    try:
        _check_tcp_reachable(args.host, args.port, args.connect_timeout)
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: {type(exc).__name__} connecting to"
            f" {args.host}:{args.port}: {exc}",
            file=sys.stderr,
        )
        return 1

    driver = VictronDriver(
        host=args.host,
        port=args.port,
        timeout_s=args.connect_timeout,
        discovery_timeout_s=args.data_timeout,
    )

    try:
        await driver.connect()
    except asyncio.TimeoutError:
        print(
            f"ERROR: TimeoutError connecting to {args.host}:{args.port}:"
            " discovery timed out (portalId or instanceId not found)",
            file=sys.stderr,
        )
        return 1
    except ConnectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: {type(exc).__name__} connecting to"
            f" {args.host}:{args.port}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"# portalId:    {driver._portal_id}", flush=True)
    print(f"# instanceId:  {driver._instance_id}", flush=True)

    # --- Subscribe to firmware version topic for warning check ---
    if driver._client is not None and driver._portal_id:
        driver._client.subscribe(
            f"N/{driver._portal_id}/system/0/FirmwareVersion"
        )

    # --- Wait for data to accumulate ---
    print(f"# Waiting {args.data_timeout}s for telemetry …", flush=True)
    await asyncio.sleep(args.data_timeout)

    state = driver.read_system_state()

    # --- Firmware version warning ---
    firmware = driver._state.get("firmware_version") or driver._state.get("FirmwareVersion")
    if firmware is not None:
        print(f"# FirmwareVersion: {firmware}")
        # Venus OS <3.21 has the AcPowerSetpoint negative-values bug
        try:
            major, minor, *_ = str(firmware).lstrip("v").split(".")
            if (int(major), int(minor)) < (3, 21):
                print(
                    "WARNING: Venus OS version is older than 3.21 —"
                    " negative AcPowerSetpoints (discharge commands) may be silently dropped.",
                    file=sys.stderr,
                )
        except (ValueError, AttributeError):
            pass
    else:
        print("# FirmwareVersion: (not yet received)")

    # --- Print data table ---
    _print_table(state)

    # --- Optional write test ---
    if args.test_write:
        print("\n# --- write test ---", flush=True)
        print("# Writing L1 AcPowerSetpoint = 0 …", flush=True)
        driver.write_ac_power_setpoint(1, 0.0)
        # Wait for readback
        await asyncio.sleep(2)
        state2 = driver.read_system_state()
        readback = state2.l1.setpoint_w
        if readback is not None and abs(readback) < 1.0:
            print(f"# Readback L1 setpoint_w = {readback:.1f} W  ✓ PASS")
        else:
            print(
                f"# Readback L1 setpoint_w = {readback!r}  ✗ FAIL"
                " (expected ≈ 0, got something else or None)",
                file=sys.stderr,
            )

    await driver.close()
    return 0


def _print_table(state) -> None:
    """Print a two-column table of all VictronSystemData fields."""
    rows = [
        ("battery_soc_pct",     f"{state.battery_soc_pct:.1f}",     "%"),
        ("battery_power_w",     f"{state.battery_power_w:.1f}",      "W"),
        ("battery_current_a",   f"{state.battery_current_a:.2f}",    "A"),
        ("battery_voltage_v",   f"{state.battery_voltage_v:.2f}",    "V"),
        ("charge_power_w",      f"{state.charge_power_w:.1f}",       "W"),
        ("discharge_power_w",   f"{state.discharge_power_w:.1f}",    "W"),
        ("l1.power_w",          _fmt(state.l1.power_w),              "W"),
        ("l1.current_a",        _fmt(state.l1.current_a),            "A"),
        ("l1.voltage_v",        _fmt(state.l1.voltage_v),            "V"),
        ("l1.setpoint_w",       _fmt_opt(state.l1.setpoint_w),       "W"),
        ("l2.power_w",          _fmt(state.l2.power_w),              "W"),
        ("l2.current_a",        _fmt(state.l2.current_a),            "A"),
        ("l2.voltage_v",        _fmt(state.l2.voltage_v),            "V"),
        ("l2.setpoint_w",       _fmt_opt(state.l2.setpoint_w),       "W"),
        ("l3.power_w",          _fmt(state.l3.power_w),              "W"),
        ("l3.current_a",        _fmt(state.l3.current_a),            "A"),
        ("l3.voltage_v",        _fmt(state.l3.voltage_v),            "V"),
        ("l3.setpoint_w",       _fmt_opt(state.l3.setpoint_w),       "W"),
        ("ess_mode",            str(state.ess_mode),                  ""),
        ("system_state",        str(state.system_state),              ""),
        ("vebus_state",         str(state.vebus_state),               ""),
    ]
    col_w = max(len(r[0]) for r in rows)
    print(f"\n{'Field':<{col_w}}  {'Value':>12}  Unit")
    print("-" * (col_w + 18))
    for field, value, unit in rows:
        print(f"{field:<{col_w}}  {value:>12}  {unit}")


def _fmt(v: float) -> str:
    return f"{v:.2f}" if v is not None else "None"


def _fmt_opt(v) -> str:
    return f"{v:.2f}" if v is not None else "None"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="probe_victron",
        description=(
            "Live hardware probe for the Victron Multiplus II 3-phase system "
            "via Venus OS MQTT.  Discovers portalId and instanceId, subscribes "
            "to all telemetry topics, waits for data, then prints a field table. "
            "Exits 0 on success; exits 1 on connection/discovery failure."
        ),
    )
    parser.add_argument(
        "--host",
        required=True,
        help="IP address or hostname of the Venus OS MQTT broker",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"TCP port (default: {_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=_DEFAULT_CONNECT_TIMEOUT,
        dest="connect_timeout",
        help=f"TCP connection timeout in seconds (default: {_DEFAULT_CONNECT_TIMEOUT})",
    )
    parser.add_argument(
        "--data-timeout",
        type=float,
        default=_DEFAULT_DATA_TIMEOUT,
        dest="data_timeout",
        help=(
            f"Seconds to wait for telemetry data after connecting "
            f"(default: {_DEFAULT_DATA_TIMEOUT})"
        ),
    )
    parser.add_argument(
        "--test-write",
        action="store_true",
        help=(
            "After reading data, write L1 AcPowerSetpoint=0 (safe no-op) "
            "and verify the readback matches (exercises the write path)"
        ),
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
        print(
            f"ERROR: {type(exc).__name__} — probe aborted: {exc}",
            file=sys.stderr,
        )
        exit_code = 1

    sys.exit(exit_code)
