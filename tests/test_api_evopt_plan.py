"""Tests for GET /api/v1/plan — EVopt-compatible charge schedule endpoint (T02).

Tests use ``httpx.AsyncClient`` with ``ASGITransport`` so the full ASGI stack
is exercised without network I/O.  The scheduler is injected via
``app.dependency_overrides[get_scheduler]`` following the same pattern as
``tests/test_api.py``.

All async tests use ``@pytest.mark.anyio`` (K007 pattern).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from backend.api import api_router, get_scheduler
from backend.evcc_client import _parse_state
from backend.schedule_models import (
    ChargeSchedule,
    ChargeSlot,
    OptimizationReasoning,
)


# ---------------------------------------------------------------------------
# Helpers — minimal schedule fixtures
# ---------------------------------------------------------------------------

_COMPUTED_AT = datetime(2026, 3, 20, 22, 0, 0, tzinfo=timezone.utc)

# A charge slot that starts at 22:30 UTC and ends at 06:00 UTC next day
_HUAWEI_SLOT = ChargeSlot(
    battery="huawei",
    target_soc_pct=90.0,
    start_utc=datetime(2026, 3, 20, 22, 30, 0, tzinfo=timezone.utc),
    end_utc=datetime(2026, 3, 21, 6, 0, 0, tzinfo=timezone.utc),
    grid_charge_power_w=2500,
)
_VICTRON_SLOT = ChargeSlot(
    battery="victron",
    target_soc_pct=85.0,
    start_utc=datetime(2026, 3, 20, 23, 0, 0, tzinfo=timezone.utc),
    end_utc=datetime(2026, 3, 21, 5, 0, 0, tzinfo=timezone.utc),
    grid_charge_power_w=1500,
)
_REASONING = OptimizationReasoning(
    text="Test schedule",
    tomorrow_solar_kwh=8.5,
    expected_consumption_kwh=12.0,
    charge_energy_kwh=10.5,
    cost_estimate_eur=1.23,
)


def _make_schedule(
    slots: list[ChargeSlot] | None = None,
    *,
    stale: bool = False,
    evopt_status: str = "Optimal",
) -> ChargeSchedule:
    """Return a minimal ``ChargeSchedule`` with sensible defaults."""
    reasoning = OptimizationReasoning(
        text="Test schedule",
        tomorrow_solar_kwh=8.5,
        expected_consumption_kwh=12.0,
        charge_energy_kwh=10.5,
        cost_estimate_eur=1.23,
        evopt_status=evopt_status,
    )
    if slots is None:
        slots = [_HUAWEI_SLOT, _VICTRON_SLOT]
    return ChargeSchedule(
        slots=slots,
        reasoning=reasoning,
        computed_at=_COMPUTED_AT,
        stale=stale,
    )


class _MockScheduler:
    """Minimal scheduler stub — only exposes ``active_schedule``."""

    def __init__(self, schedule: ChargeSchedule | None) -> None:
        self.active_schedule = schedule


def _build_test_app(scheduler: _MockScheduler | None) -> Any:
    """Build a minimal test FastAPI app with scheduler injected via DI."""
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test-evopt")
    app.include_router(api_router)
    app.dependency_overrides[get_scheduler] = lambda: scheduler
    return app


# ---------------------------------------------------------------------------
# Tests — 503 paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_plan_503_when_no_scheduler() -> None:
    """GET /api/v1/plan returns 503 when scheduler dependency returns None."""
    app = _build_test_app(scheduler=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    assert resp.status_code == 503


@pytest.mark.anyio
async def test_get_plan_503_when_no_active_schedule() -> None:
    """GET /api/v1/plan returns 503 when scheduler has no active_schedule."""
    app = _build_test_app(scheduler=_MockScheduler(schedule=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    assert resp.status_code == 503


@pytest.mark.anyio
async def test_get_plan_503_response_body() -> None:
    """503 response body contains {'detail': {'status': 'Unavailable'}}."""
    app = _build_test_app(scheduler=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    body = resp.json()
    assert "detail" in body
    assert body["detail"].get("status") == "Unavailable"


# ---------------------------------------------------------------------------
# Tests — 200 happy path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_plan_200_when_schedule_present() -> None:
    """GET /api/v1/plan returns 200 when an active schedule exists."""
    app = _build_test_app(scheduler=_MockScheduler(schedule=_make_schedule()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_plan_response_has_res_key() -> None:
    """Response JSON has a top-level 'res' key."""
    app = _build_test_app(scheduler=_MockScheduler(schedule=_make_schedule()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    assert "res" in resp.json()


@pytest.mark.anyio
async def test_get_plan_res_has_required_keys() -> None:
    """res dict contains status, objective_value, batteries, and details."""
    app = _build_test_app(scheduler=_MockScheduler(schedule=_make_schedule()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    res = resp.json()["res"]
    for key in ("status", "objective_value", "batteries", "details"):
        assert key in res, f"Missing key in res: {key!r}"
    assert "timestamp" in res["details"]


@pytest.mark.anyio
async def test_get_plan_batteries_have_correct_titles() -> None:
    """Batteries list contains entries titled 'Emma Akku 1' and 'Victron'."""
    app = _build_test_app(scheduler=_MockScheduler(schedule=_make_schedule()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    titles = {b["title"] for b in resp.json()["res"]["batteries"]}
    assert "Emma Akku 1" in titles
    assert "Victron" in titles


@pytest.mark.anyio
async def test_get_plan_battery_timeseries_length() -> None:
    """Each battery's charging_power, discharging_power, state_of_charge have length 96."""
    app = _build_test_app(scheduler=_MockScheduler(schedule=_make_schedule()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    res = resp.json()["res"]
    ts_len = len(res["details"]["timestamp"])
    assert ts_len == 96

    for bat in res["batteries"]:
        assert len(bat["charging_power"]) == 96, f"{bat['title']}: charging_power length != 96"
        assert len(bat["discharging_power"]) == 96, f"{bat['title']}: discharging_power length != 96"
        assert len(bat["state_of_charge"]) == 96, f"{bat['title']}: state_of_charge length != 96"
        assert len(bat["charging_power"]) == ts_len


@pytest.mark.anyio
async def test_get_plan_charging_power_in_slot_window() -> None:
    """charging_power > 0 for at least one slot within the charge window."""
    app = _build_test_app(scheduler=_MockScheduler(schedule=_make_schedule()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    res = resp.json()["res"]
    huawei = next(b for b in res["batteries"] if b["title"] == "Emma Akku 1")
    assert any(p > 0 for p in huawei["charging_power"]), (
        "Expected at least one slot with charging_power > 0 for Emma Akku 1"
    )
    victron = next(b for b in res["batteries"] if b["title"] == "Victron")
    assert any(p > 0 for p in victron["charging_power"]), (
        "Expected at least one slot with charging_power > 0 for Victron"
    )


@pytest.mark.anyio
async def test_get_plan_charging_power_zero_outside_window() -> None:
    """charging_power == 0 for slots outside the charge window."""
    # Use a narrow slot: 1 hour window (4 slots)
    narrow_slot = ChargeSlot(
        battery="huawei",
        target_soc_pct=90.0,
        start_utc=datetime(2026, 3, 21, 0, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 3, 21, 1, 0, 0, tzinfo=timezone.utc),
        grid_charge_power_w=3000,
    )
    schedule = _make_schedule(slots=[narrow_slot])
    app = _build_test_app(scheduler=_MockScheduler(schedule=schedule))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    res = resp.json()["res"]
    timestamps = res["details"]["timestamp"]
    huawei = next(b for b in res["batteries"] if b["title"] == "Emma Akku 1")
    powers = huawei["charging_power"]

    # Classify slots as inside or outside window
    slot_start = datetime(2026, 3, 21, 0, 0, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 3, 21, 1, 0, 0, tzinfo=timezone.utc)
    inside_count = 0
    outside_zero = True
    for ts_str, power in zip(timestamps, powers):
        ts = datetime.fromisoformat(ts_str)
        if slot_start <= ts < slot_end:
            inside_count += 1
            assert power == 3000.0, f"Expected 3000W inside window at {ts_str}, got {power}"
        else:
            if power != 0.0:
                outside_zero = False

    assert inside_count == 4, f"Expected 4 slots in 1-hour window, got {inside_count}"
    assert outside_zero, "Expected charging_power == 0 for all slots outside the window"


@pytest.mark.anyio
async def test_get_plan_round_trip_parse_state() -> None:
    """_parse_state({'evopt': response}) returns EvoptResult without raising.

    This is the core R038 contract: the EVopt JSON produced by EMS must be
    parseable by the same ``_parse_state`` function used to read EVopt from EVCC.
    """
    schedule = _make_schedule(evopt_status="Optimal")
    app = _build_test_app(scheduler=_MockScheduler(schedule=schedule))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    assert resp.status_code == 200
    response_json = resp.json()

    # Round-trip: wrap in {"evopt": ...} as EVCC would present it to _parse_state
    evcc_state = _parse_state({"evopt": response_json})
    assert evcc_state.evopt is not None, "_parse_state returned evopt=None — round-trip failed"
    assert evcc_state.evopt.status == schedule.reasoning.evopt_status
    assert len(evcc_state.evopt.batteries) == 2  # huawei + victron


@pytest.mark.anyio
async def test_get_plan_stale_schedule_returns_200() -> None:
    """Stale schedule (stale=True) still returns HTTP 200, not 503."""
    stale_schedule = _make_schedule(stale=True)
    assert stale_schedule.stale is True
    app = _build_test_app(scheduler=_MockScheduler(schedule=stale_schedule))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    assert resp.status_code == 200


@pytest.mark.anyio
async def test_get_plan_res_status_reflects_evopt_status() -> None:
    """res.status reflects schedule.reasoning.evopt_status."""
    for evopt_status in ("Optimal", "Heuristic", "Unavailable"):
        schedule = _make_schedule(evopt_status=evopt_status)
        app = _build_test_app(scheduler=_MockScheduler(schedule=schedule))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/plan")

        assert resp.json()["res"]["status"] == evopt_status, (
            f"Expected status={evopt_status!r}, got {resp.json()['res']['status']!r}"
        )


@pytest.mark.anyio
async def test_get_plan_two_huawei_slots_merged_by_max() -> None:
    """Two huawei slots at different times are both reflected; overlapping max is used."""
    slot_a = ChargeSlot(
        battery="huawei",
        target_soc_pct=80.0,
        start_utc=datetime(2026, 3, 21, 0, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 3, 21, 1, 0, 0, tzinfo=timezone.utc),
        grid_charge_power_w=2000,
    )
    slot_b = ChargeSlot(
        battery="huawei",
        target_soc_pct=90.0,
        start_utc=datetime(2026, 3, 21, 2, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 3, 21, 3, 0, 0, tzinfo=timezone.utc),
        grid_charge_power_w=3000,
    )
    schedule = _make_schedule(slots=[slot_a, slot_b])
    app = _build_test_app(scheduler=_MockScheduler(schedule=schedule))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/plan")

    res = resp.json()["res"]
    # Only one "Emma Akku 1" battery entry should exist (merged)
    huawei_entries = [b for b in res["batteries"] if b["title"] == "Emma Akku 1"]
    assert len(huawei_entries) == 1, "Expected exactly one Emma Akku 1 entry after merge"

    huawei = huawei_entries[0]
    timestamps = res["details"]["timestamp"]
    # slot_a covers 4 slots starting at 00:00, slot_b covers 4 slots starting at 02:00
    slot_a_start = datetime(2026, 3, 21, 0, 0, 0, tzinfo=timezone.utc)
    slot_a_end = datetime(2026, 3, 21, 1, 0, 0, tzinfo=timezone.utc)
    slot_b_start = datetime(2026, 3, 21, 2, 0, 0, tzinfo=timezone.utc)
    slot_b_end = datetime(2026, 3, 21, 3, 0, 0, tzinfo=timezone.utc)
    for ts_str, power in zip(timestamps, huawei["charging_power"]):
        ts = datetime.fromisoformat(ts_str)
        if slot_a_start <= ts < slot_a_end:
            assert power == 2000.0
        elif slot_b_start <= ts < slot_b_end:
            assert power == 3000.0
        else:
            assert power == 0.0
