#!/usr/bin/env python3
"""Live EVCC MQTT probe — inspect broker topics without a running EMS.

Connects to the EVCC MQTT broker, subscribes to ``evcc/site/batteryMode`` and
``evcc/loadpoints/1/#``, prints every arriving message with a timestamp for
``--timeout`` seconds, then disconnects and exits.

Usage::

    python scripts/probe_evcc_mqtt.py --host 192.168.0.10
    python scripts/probe_evcc_mqtt.py --host 192.168.0.10 --port 1883 --timeout 60

Options::

    --host      IP address or hostname of the EVCC MQTT broker (required)
    --port      TCP port (default: 1883)
    --timeout   Seconds to listen for messages before disconnecting (default: 30)

Expected output format::

    # Connecting to 192.168.0.10:1883 …
    # Connected.
    # Listening for 30s (Ctrl+C to stop early) …
    2026-03-19 10:15:02.341  evcc/site/batteryMode          →  normal
    2026-03-19 10:15:02.344  evcc/loadpoints/1/mode         →  pv
    2026-03-19 10:15:02.347  evcc/loadpoints/1/chargePower  →  3450.0
    # Done (3 messages received).
    # batteryMode: normal  — EVCC battery integration NOT active (no hold command seen)

Exit codes
----------
- 0 — completed successfully (even if zero messages were received)
- 1 — TCP connection failure or unexpected error

Debugging tips
--------------
Set ``PYTHONLOG=DEBUG`` to see verbose paho internals::

    PYTHONLOG=DEBUG python scripts/probe_evcc_mqtt.py --host 192.168.0.10
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import os
import socket
import sys

_DEFAULT_PORT = 1883
_DEFAULT_TIMEOUT = 30


def _configure_logging() -> None:
    """Set up logging from PYTHONLOG env var (DEBUG/INFO/WARNING/ERROR)."""
    level_name = os.environ.get("PYTHONLOG", "ERROR").upper()
    level = getattr(logging, level_name, logging.ERROR)
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _check_tcp_reachable(host: str, port: int, timeout_s: float = 3.0) -> None:
    """Verify TCP connectivity — raises OSError/ConnectionRefusedError on failure.

    Mirrors the pre-flight check used in ``probe_victron.py`` and
    ``probe_huawei.py``.  Callers handle the exception and emit a structured
    error line before exiting 1.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout_s)
    try:
        s.connect((host, port))
    finally:
        s.close()


async def main(args: argparse.Namespace) -> int:
    """Probe logic — returns an integer exit code."""
    from backend.evcc_mqtt_driver import EvccMqttDriver

    # --- Pre-flight: raw TCP reachability check ---
    print(f"# Connecting to {args.host}:{args.port} …", flush=True)
    try:
        _check_tcp_reachable(args.host, args.port)
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: {type(exc).__name__} connecting to"
            f" {args.host}:{args.port}: {exc}",
            file=sys.stderr,
        )
        return 1

    # --- Message collection ---
    messages: list[tuple[str, str]] = []   # (topic, payload)

    # Patch the driver's _on_message to also forward to our printer
    driver = EvccMqttDriver(host=args.host, port=args.port)

    # Wrap the driver's _on_message so we can intercept and print each message
    _original_on_message = driver._on_message

    def _printing_on_message(client, userdata, message) -> None:  # type: ignore[no-untyped-def]
        payload = message.payload.decode("utf-8", errors="replace").strip()
        topic = message.topic
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"{ts}  {topic:<40}  →  {payload}", flush=True)
        messages.append((topic, payload))
        _original_on_message(client, userdata, message)

    driver._client.on_message = _printing_on_message
    driver._on_message = _printing_on_message  # type: ignore[method-assign]

    await driver.connect()

    if not driver.evcc_available:
        # connect() returned but availability is still False — this can happen
        # briefly before the CONNACK arrives; give paho a moment.
        await asyncio.sleep(1.0)

    if not driver.evcc_available:
        print(
            f"ERROR: Connected to broker but EVCC did not send CONNACK."
            f"  Check that {args.host}:{args.port} is an EVCC MQTT broker.",
            file=sys.stderr,
        )
        await driver.close()
        return 1

    print("# Connected.", flush=True)
    print(
        f"# Listening for {args.timeout}s (Ctrl+C to stop early) …",
        flush=True,
    )

    await asyncio.sleep(args.timeout)

    await driver.close()

    print(f"# Done ({len(messages)} messages received).", flush=True)

    # --- EVCC battery-integration summary ---
    battery_mode_values = [
        payload for topic, payload in messages
        if topic == "evcc/site/batteryMode"
    ]
    if battery_mode_values:
        last_mode = battery_mode_values[-1]
        if "hold" in battery_mode_values:
            print(
                f"# batteryMode: {last_mode}"
                f"  — EVCC battery integration ACTIVE (hold command seen)",
                flush=True,
            )
        else:
            print(
                f"# batteryMode: {last_mode}"
                f"  — EVCC battery integration NOT active (no hold command seen)",
                flush=True,
            )
    else:
        print(
            "# batteryMode: (no messages received — is EVCC publishing to this broker?)",
            flush=True,
        )

    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="probe_evcc_mqtt",
        description=(
            "Live EVCC MQTT probe.  Connects to the EVCC broker, subscribes to "
            "evcc/site/batteryMode and evcc/loadpoints/1/#, prints arriving messages "
            "with timestamps for --timeout seconds, then disconnects.  "
            "Exits 0 on success; exits 1 on connection failure."
        ),
    )
    parser.add_argument(
        "--host",
        required=True,
        help="IP address or hostname of the EVCC MQTT broker",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"TCP port (default: {_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT,
        help=f"Seconds to listen for messages (default: {_DEFAULT_TIMEOUT})",
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
        exit_code = 0  # clean exit on Ctrl+C
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: {type(exc).__name__} — probe aborted: {exc}",
            file=sys.stderr,
        )
        exit_code = 1

    sys.exit(exit_code)
