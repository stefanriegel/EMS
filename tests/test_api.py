"""Tests for the FastAPI API layer (S03 T03).

Tests use ``httpx.AsyncClient`` with ``ASGITransport`` so the full ASGI stack
is exercised without network I/O.  The lifespan is bypassed — a
``MockOrchestrator`` is injected via ``app.dependency_overrides`` so tests
never require live hardware.

All async tests use ``@pytest.mark.anyio`` (K002 pattern).  The
``_get_or_create_event_loop()`` helper is used in any sync fixture that
touches asyncio objects (K005 pattern).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from backend.api import api_router, get_forecaster, get_orchestrator
from backend.config import SystemConfig
from backend.controller_model import CoordinatorState
from backend.unified_model import ControlState, UnifiedPoolState


# ---------------------------------------------------------------------------
# K005 helper — safe event loop acquisition in sync context
# ---------------------------------------------------------------------------


def _get_or_create_event_loop() -> asyncio.AbstractEventLoop:
    """Return the running event loop or create a new one.

    Required in Python 3.14 where ``asyncio.get_event_loop()`` raises
    ``RuntimeError`` in a sync (non-async) context when no loop is running.
    """
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


# ---------------------------------------------------------------------------
# Mock orchestrator stub
# ---------------------------------------------------------------------------


class MockOrchestrator:
    """Minimal stub for :class:`~backend.orchestrator.Orchestrator`.

    No pytest-mock needed — simple Python stub is sufficient for route tests.
    """

    def __init__(self, state: UnifiedPoolState | None = None) -> None:
        self._state = state
        self._last_error: str | None = None
        self.sys_config = SystemConfig()
        self._device_snapshot: dict = _make_device_snapshot()

    def get_state(self) -> UnifiedPoolState | None:
        return self._state

    def get_last_error(self) -> str | None:
        return self._last_error

    def get_working_mode(self) -> int | None:
        return None

    def get_device_snapshot(self) -> dict:
        return self._device_snapshot

    def get_integration_health(self) -> dict[str, dict]:
        return {}


def _make_state(**overrides: Any) -> UnifiedPoolState:
    """Return a fully-populated :class:`UnifiedPoolState` with sensible defaults.

    Keyword arguments override individual fields.
    """
    defaults: dict[str, Any] = dict(
        combined_soc_pct=62.5,
        huawei_soc_pct=50.0,
        victron_soc_pct=68.75,
        huawei_available=True,
        victron_available=True,
        control_state=ControlState.IDLE,
        huawei_discharge_setpoint_w=0,
        victron_discharge_setpoint_w=0,
        combined_power_w=0.0,
        huawei_charge_headroom_w=1500,
        victron_charge_headroom_w=2000.0,
        timestamp=time.monotonic(),
    )
    defaults.update(overrides)
    return UnifiedPoolState(**defaults)


def _make_device_snapshot(
    *,
    huawei_available: bool = True,
    victron_available: bool = True,
    master_pv_power_w: int | None = 3500,
    pack2_soc_pct: float | None = 72.0,
    pack2_power_w: int | None = -800,
) -> dict:
    """Return a fully-populated device snapshot dict with sensible defaults."""
    return {
        "huawei": {
            "available": huawei_available,
            "pack1_soc_pct": 55.0,
            "pack1_power_w": -1200,
            "pack2_soc_pct": pack2_soc_pct,
            "pack2_power_w": pack2_power_w,
            "total_soc_pct": 62.0,
            "total_power_w": -2000,
            "max_charge_w": 10000,
            "max_discharge_w": 10000,
            "master_pv_power_w": master_pv_power_w,
            "slave_pv_power_w": None,
        },
        "victron": {
            "available": victron_available,
            "soc_pct": 68.75,
            "battery_power_w": -500.0,
            "l1_power_w": -166.7,
            "l2_power_w": -166.7,
            "l3_power_w": -166.6,
            "l1_voltage_v": 230.1,
            "l2_voltage_v": 230.2,
            "l3_voltage_v": 230.0,
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_test_app(mock_orchestrator: MockOrchestrator) -> Any:
    """Build a test FastAPI app with a mock orchestrator injected via DI."""
    from backend.main import create_app

    # create_app() without arguments creates an app with the production lifespan.
    # We call FastAPI directly here to avoid starting the lifespan in tests.
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: mock_orchestrator
    return app


@pytest.fixture()
def mock_orch() -> MockOrchestrator:
    """A fresh MockOrchestrator with a valid state snapshot."""
    return MockOrchestrator(state=_make_state())


@pytest.fixture()
def mock_orch_no_state() -> MockOrchestrator:
    """A MockOrchestrator that hasn't completed its first poll (state=None)."""
    return MockOrchestrator(state=None)


@pytest.fixture()
def test_app(mock_orch: MockOrchestrator) -> Any:
    """Test app with a valid-state MockOrchestrator."""
    return _build_test_app(mock_orch)


@pytest.fixture()
def test_app_no_state(mock_orch_no_state: MockOrchestrator) -> Any:
    """Test app wired to a not-yet-ready orchestrator."""
    return _build_test_app(mock_orch_no_state)


# ---------------------------------------------------------------------------
# GET /api/state
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_state_returns_200_with_valid_state(test_app: Any) -> None:
    """GET /api/state returns 200 and all UnifiedPoolState fields when ready."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/state")

    assert resp.status_code == 200
    data = resp.json()

    # All expected fields present
    expected_keys = {
        "combined_soc_pct",
        "huawei_soc_pct",
        "victron_soc_pct",
        "huawei_available",
        "victron_available",
        "control_state",
        "huawei_discharge_setpoint_w",
        "victron_discharge_setpoint_w",
        "combined_power_w",
        "huawei_charge_headroom_w",
        "victron_charge_headroom_w",
        "timestamp",
    }
    assert expected_keys.issubset(data.keys()), (
        f"Missing keys: {expected_keys - set(data.keys())}"
    )

    # Spot-check values from _make_state() defaults
    assert data["combined_soc_pct"] == pytest.approx(62.5)
    assert data["huawei_available"] is True
    assert data["victron_available"] is True
    assert data["control_state"] == "IDLE"


@pytest.mark.anyio
async def test_get_state_returns_503_when_not_ready(test_app_no_state: Any) -> None:
    """GET /api/state returns 503 before the first poll cycle completes."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app_no_state), base_url="http://test"
    ) as client:
        resp = await client.get("/api/state")

    assert resp.status_code == 503
    assert "not yet ready" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_health_ok_when_both_available(test_app: Any) -> None:
    """GET /api/health returns status=ok when both drivers available."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["huawei_available"] is True
    assert data["victron_available"] is True
    assert "control_state" in data
    assert "last_error" in data
    assert "uptime_s" in data
    assert data["uptime_s"] >= 0.0


@pytest.mark.anyio
async def test_get_health_degraded_when_one_unavailable() -> None:
    """GET /api/health returns status=degraded when one driver is offline."""
    orch = MockOrchestrator(
        state=_make_state(huawei_available=False, victron_available=True)
    )
    app = _build_test_app(orch)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


@pytest.mark.anyio
async def test_get_health_offline_when_no_state(test_app_no_state: Any) -> None:
    """GET /api/health returns status=offline when orchestrator not yet ready."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app_no_state), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "offline"


@pytest.mark.anyio
async def test_get_health_exposes_last_error() -> None:
    """GET /api/health surfaces last_error from the orchestrator."""
    orch = MockOrchestrator(state=_make_state(huawei_available=False))
    orch._last_error = "Connection refused"
    app = _build_test_app(orch)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")

    assert resp.json()["last_error"] == "Connection refused"


@pytest.mark.anyio
async def test_get_health_exposes_huawei_working_mode() -> None:
    """GET /api/health includes huawei_working_mode from the orchestrator."""
    orch = MockOrchestrator(state=_make_state())
    # Override get_working_mode to return a specific value
    orch.get_working_mode = lambda: 3  # type: ignore[method-assign]
    app = _build_test_app(orch)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json()["huawei_working_mode"] == 3


@pytest.mark.anyio
async def test_get_health_working_mode_null_when_orchestrator_none() -> None:
    """GET /api/health returns huawei_working_mode=null in degraded mode (no orchestrator)."""
    from backend.main import create_app
    from fastapi import FastAPI
    from backend.api import api_router, get_orchestrator

    app = FastAPI(title="EMS-test-degraded")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: None

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json()["huawei_working_mode"] is None


# ---------------------------------------------------------------------------
# GET /api/config
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_config_returns_default_system_config(test_app: Any) -> None:
    """GET /api/config returns default SystemConfig field values."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/config")

    assert resp.status_code == 200
    data = resp.json()

    # All SystemConfig fields
    expected_keys = {
        "huawei_min_soc_pct",
        "huawei_max_soc_pct",
        "victron_min_soc_pct",
        "victron_max_soc_pct",
        "huawei_feed_in_allowed",
        "victron_feed_in_allowed",
    }
    assert expected_keys.issubset(data.keys()), (
        f"Missing keys: {expected_keys - set(data.keys())}"
    )

    assert data["huawei_min_soc_pct"] == pytest.approx(10.0)
    assert data["victron_min_soc_pct"] == pytest.approx(15.0)
    assert data["huawei_feed_in_allowed"] is False
    assert data["victron_feed_in_allowed"] is False


# ---------------------------------------------------------------------------
# POST /api/config
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_post_config_updates_config_and_reflects_in_get(test_app: Any, mock_orch: MockOrchestrator) -> None:
    """POST /api/config updates the orchestrator config; GET /api/config reflects the change."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        payload = {
            "huawei_min_soc_pct": 20.0,
            "huawei_max_soc_pct": 90.0,
            "victron_min_soc_pct": 25.0,
            "victron_max_soc_pct": 90.0,
            "huawei_feed_in_allowed": True,
            "victron_feed_in_allowed": False,
        }
        post_resp = await client.post("/api/config", json=payload)
        assert post_resp.status_code == 200
        post_data = post_resp.json()
        assert post_data["huawei_min_soc_pct"] == pytest.approx(20.0)
        assert post_data["huawei_feed_in_allowed"] is True

        # Verify the orchestrator's config was updated
        assert mock_orch.sys_config.huawei_min_soc_pct == pytest.approx(20.0)
        assert mock_orch.sys_config.huawei_feed_in_allowed is True

        # GET should now reflect the updated config
        get_resp = await client.get("/api/config")
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data["huawei_min_soc_pct"] == pytest.approx(20.0)
        assert get_data["huawei_feed_in_allowed"] is True


@pytest.mark.anyio
async def test_post_config_returns_422_on_invalid_soc(test_app: Any) -> None:
    """POST /api/config with huawei_min_soc_pct=150 returns 422."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/config",
            json={"huawei_min_soc_pct": 150.0},  # violates ge=0, le=100
        )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_post_config_returns_422_on_negative_soc(test_app: Any) -> None:
    """POST /api/config with victron_min_soc_pct=-5 returns 422."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/config",
            json={"victron_min_soc_pct": -5.0},
        )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_post_config_accepts_partial_body(test_app: Any) -> None:
    """POST /api/config with a partial body uses defaults for omitted fields."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/config",
            json={"victron_feed_in_allowed": True},
        )
    assert resp.status_code == 200
    data = resp.json()
    # Only victron_feed_in_allowed changed; others use Pydantic defaults
    assert data["victron_feed_in_allowed"] is True
    assert data["huawei_min_soc_pct"] == pytest.approx(10.0)  # default


# ---------------------------------------------------------------------------
# Smoke test — create_app() factory
# ---------------------------------------------------------------------------


def test_create_app_returns_app_with_correct_title() -> None:
    """create_app() returns a FastAPI app with title='EMS'."""
    from backend.main import create_app

    application = create_app()
    assert application.title == "EMS"


# ---------------------------------------------------------------------------
# Mock metrics reader stub
# ---------------------------------------------------------------------------


class MockMetricsReader:
    """Minimal async stub for :class:`~backend.influx_reader.InfluxMetricsReader`.

    Responses are configurable via constructor parameters so tests can inject
    any data shape without reaching for a full mock library.
    """

    def __init__(
        self,
        range_result: list[dict] | None = None,
        latest_result: dict | None = None,
    ) -> None:
        self._range_result: list[dict] = range_result if range_result is not None else []
        self._latest_result = latest_result

    async def query_range(self, measurement: str, start: str, stop: str) -> list[dict]:
        return self._range_result

    async def query_latest(self, measurement: str) -> dict | None:
        return self._latest_result


def _build_test_app_with_reader(
    mock_orchestrator: MockOrchestrator,
    mock_reader: MockMetricsReader | None,
) -> Any:
    """Build a test FastAPI app with both orchestrator and reader injected."""
    from backend.api import get_metrics_reader
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: mock_orchestrator
    app.dependency_overrides[get_metrics_reader] = lambda: mock_reader
    return app


# ---------------------------------------------------------------------------
# GET /api/metrics/range
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_metrics_range_returns_200_and_list() -> None:
    """GET /api/metrics/range returns 200 with a list of record dicts."""
    records = [
        {"time": "2026-01-01T00:00:00+00:00", "field": "combined_soc_pct", "value": 62.5},
        {"time": "2026-01-01T00:00:05+00:00", "field": "combined_soc_pct", "value": 63.0},
    ]
    reader = MockMetricsReader(range_result=records)
    orch = MockOrchestrator(state=_make_state())
    app = _build_test_app_with_reader(orch, reader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/metrics/range",
            params={"measurement": "ems_system", "start": "-1h", "stop": "now()"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["field"] == "combined_soc_pct"
    assert data[0]["value"] == pytest.approx(62.5)


@pytest.mark.anyio
async def test_metrics_range_returns_empty_list_when_no_data() -> None:
    """GET /api/metrics/range returns 200 with [] when reader returns no records."""
    reader = MockMetricsReader(range_result=[])
    orch = MockOrchestrator(state=_make_state())
    app = _build_test_app_with_reader(orch, reader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/metrics/range",
            params={"measurement": "ems_system", "start": "-1h", "stop": "now()"},
        )

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_metrics_range_returns_503_when_reader_none() -> None:
    """GET /api/metrics/range returns 503 when the reader is not available."""
    orch = MockOrchestrator(state=_make_state())
    app = _build_test_app_with_reader(orch, None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/metrics/range",
            params={"measurement": "ems_system", "start": "-1h", "stop": "now()"},
        )

    assert resp.status_code == 503
    assert "not available" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/metrics/latest
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_metrics_latest_returns_200_and_dict() -> None:
    """GET /api/metrics/latest returns 200 with a single record dict."""
    latest = {"time": "2026-01-01T12:00:00+00:00", "field": "combined_soc_pct", "value": 75.0}
    reader = MockMetricsReader(latest_result=latest)
    orch = MockOrchestrator(state=_make_state())
    app = _build_test_app_with_reader(orch, reader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/metrics/latest",
            params={"measurement": "ems_system"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert data["field"] == "combined_soc_pct"
    assert data["value"] == pytest.approx(75.0)


@pytest.mark.anyio
async def test_metrics_latest_returns_200_null_when_no_data() -> None:
    """GET /api/metrics/latest returns 200 with null when measurement has no data."""
    reader = MockMetricsReader(latest_result=None)
    orch = MockOrchestrator(state=_make_state())
    app = _build_test_app_with_reader(orch, reader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/metrics/latest",
            params={"measurement": "ems_system"},
        )

    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.anyio
async def test_metrics_latest_returns_503_when_reader_none() -> None:
    """GET /api/metrics/latest returns 503 when the reader is not available."""
    orch = MockOrchestrator(state=_make_state())
    app = _build_test_app_with_reader(orch, None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/metrics/latest",
            params={"measurement": "ems_system"},
        )

    assert resp.status_code == 503
    assert "not available" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/devices
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_devices_both_available() -> None:
    """GET /api/devices returns 200 with huawei.available and victron.available True."""
    orch = MockOrchestrator(state=_make_state())
    app = _build_test_app(orch)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/devices")

    assert resp.status_code == 200
    data = resp.json()

    assert data["huawei"]["available"] is True
    assert data["victron"]["available"] is True

    # All required huawei keys present
    huawei_keys = {
        "available", "pack1_soc_pct", "pack1_power_w",
        "pack2_soc_pct", "pack2_power_w",
        "total_soc_pct", "total_power_w",
        "max_charge_w", "max_discharge_w",
        "master_pv_power_w", "slave_pv_power_w",
    }
    assert huawei_keys.issubset(data["huawei"].keys()), (
        f"Missing huawei keys: {huawei_keys - set(data['huawei'].keys())}"
    )

    # All required victron keys present
    victron_keys = {
        "available", "soc_pct", "battery_power_w",
        "l1_power_w", "l2_power_w", "l3_power_w",
        "l1_voltage_v", "l2_voltage_v", "l3_voltage_v",
    }
    assert victron_keys.issubset(data["victron"].keys()), (
        f"Missing victron keys: {victron_keys - set(data['victron'].keys())}"
    )

    # slave_pv_power_w is always null
    assert data["huawei"]["slave_pv_power_w"] is None


@pytest.mark.anyio
async def test_get_devices_huawei_unavailable() -> None:
    """GET /api/devices returns huawei.available=False when mock returns it unavailable."""
    orch = MockOrchestrator(state=_make_state())
    orch._device_snapshot = _make_device_snapshot(huawei_available=False)
    app = _build_test_app(orch)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/devices")

    assert resp.status_code == 200
    data = resp.json()
    assert data["huawei"]["available"] is False
    assert data["victron"]["available"] is True


@pytest.mark.anyio
async def test_get_devices_master_none() -> None:
    """GET /api/devices returns master_pv_power_w=null and slave_pv_power_w=null when master is None."""
    orch = MockOrchestrator(state=_make_state())
    orch._device_snapshot = _make_device_snapshot(master_pv_power_w=None)
    app = _build_test_app(orch)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/devices")

    assert resp.status_code == 200
    data = resp.json()
    assert data["huawei"]["master_pv_power_w"] is None
    assert data["huawei"]["slave_pv_power_w"] is None


# ---------------------------------------------------------------------------
# WebSocket /api/ws/state
# ---------------------------------------------------------------------------


def test_ws_state_sends_first_frame() -> None:
    """Sync WS test: client receives first JSON frame from /api/ws/state within 8s.

    Uses ``starlette.testclient.TestClient`` (sync, no anyio) per plan contract.
    The frame must contain keys ``pool``, ``devices``, and ``tariff``.
    """
    from starlette.testclient import TestClient

    orch = MockOrchestrator(state=_make_state())
    # Build app with real ws route — need app.state.orchestrator accessible
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: orch
    # Inject orchestrator into app.state for the WS handler (it reads app.state directly)
    app.state.orchestrator = orch

    with TestClient(app).websocket_connect("/api/ws/state") as ws:
        data = ws.receive_json()

    assert "pool" in data, f"Missing 'pool' key in WS frame: {data}"
    assert "devices" in data, f"Missing 'devices' key in WS frame: {data}"
    assert "tariff" in data, f"Missing 'tariff' key in WS frame: {data}"
    assert "optimization" in data, f"Missing 'optimization' key in WS frame: {data}"

    # Verify device sub-structure is present
    assert "huawei" in data["devices"]
    assert "victron" in data["devices"]

    # Tariff fields present (null is expected when no engine configured)
    assert "effective_rate_eur_kwh" in data["tariff"]
    assert "octopus_rate_eur_kwh" in data["tariff"]
    assert "modul3_rate_eur_kwh" in data["tariff"]


# ---------------------------------------------------------------------------
# Optimization schedule endpoint
# ---------------------------------------------------------------------------


class MockScheduler:
    """Minimal stub for :class:`~backend.scheduler.Scheduler`."""

    def __init__(self, active_schedule=None) -> None:
        self.active_schedule = active_schedule


def _make_charge_schedule(*, stale: bool = False) -> "ChargeSchedule":
    """Return a minimal :class:`~backend.schedule_models.ChargeSchedule` for tests."""
    from datetime import datetime, timezone

    from backend.schedule_models import ChargeSchedule, ChargeSlot, OptimizationReasoning

    slot = ChargeSlot(
        battery="huawei",
        target_soc_pct=90.0,
        start_utc=datetime(2026, 1, 15, 1, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 1, 15, 5, 30, tzinfo=timezone.utc),
        grid_charge_power_w=3500,
    )
    reasoning = OptimizationReasoning(
        text="Charge overnight using cheap Octopus Go rate.",
        tomorrow_solar_kwh=12.5,
        expected_consumption_kwh=8.0,
        charge_energy_kwh=5.0,
        cost_estimate_eur=0.84,
    )
    return ChargeSchedule(
        slots=[slot],
        reasoning=reasoning,
        computed_at=datetime(2026, 1, 14, 22, 0, tzinfo=timezone.utc),
        stale=stale,
    )


@pytest.mark.anyio
async def test_get_optimization_schedule_returns_503_when_no_scheduler() -> None:
    """GET /api/optimization/schedule returns 503 when scheduler is not wired."""
    from backend.api import get_scheduler
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: MockOrchestrator(state=_make_state())
    app.dependency_overrides[get_scheduler] = lambda: None

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/optimization/schedule")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Scheduler not available"


@pytest.mark.anyio
async def test_get_optimization_schedule_returns_503_when_no_active_schedule() -> None:
    """GET /api/optimization/schedule returns 503 when scheduler has no schedule yet."""
    from backend.api import get_scheduler
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: MockOrchestrator(state=_make_state())
    app.dependency_overrides[get_scheduler] = lambda: MockScheduler(active_schedule=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/optimization/schedule")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "No active schedule"


@pytest.mark.anyio
async def test_get_optimization_schedule_returns_200_with_schedule() -> None:
    """GET /api/optimization/schedule returns 200 with all expected keys when schedule present."""
    import json

    from backend.api import get_scheduler
    from fastapi import FastAPI

    schedule = _make_charge_schedule()
    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: MockOrchestrator(state=_make_state())
    app.dependency_overrides[get_scheduler] = lambda: MockScheduler(active_schedule=schedule)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/optimization/schedule")

    assert resp.status_code == 200
    data = resp.json()

    # Required top-level keys
    for key in ("slots", "reasoning", "computed_at", "stale"):
        assert key in data, f"Missing key '{key}' in response: {data}"

    # computed_at must be a string (ISO format), not a raw datetime
    assert isinstance(data["computed_at"], str), "computed_at should be an ISO string"
    assert "2026-01-14" in data["computed_at"]

    # Slot datetime fields must be strings
    assert len(data["slots"]) == 1
    slot = data["slots"][0]
    assert isinstance(slot["start_utc"], str), "start_utc should be an ISO string"
    assert isinstance(slot["end_utc"], str), "end_utc should be an ISO string"

    # Confirm json.dumps does not raise TypeError (no raw datetime objects)
    json.dumps(data)  # raises if any value is not JSON-serialisable


@pytest.mark.anyio
async def test_get_optimization_schedule_stale_flag() -> None:
    """GET /api/optimization/schedule returns stale=True when schedule is stale."""
    from backend.api import get_scheduler
    from fastapi import FastAPI

    schedule = _make_charge_schedule(stale=True)
    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: MockOrchestrator(state=_make_state())
    app.dependency_overrides[get_scheduler] = lambda: MockScheduler(active_schedule=schedule)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/optimization/schedule")

    assert resp.status_code == 200
    assert resp.json()["stale"] is True


def test_ws_state_includes_optimization_key() -> None:
    """WS /api/ws/state frame includes 'optimization' key (null when no schedule)."""
    from starlette.testclient import TestClient

    from fastapi import FastAPI

    orch = MockOrchestrator(state=_make_state())
    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.state.orchestrator = orch
    # Wire a scheduler with no active schedule — optimization key should be present but null
    app.state.scheduler = MockScheduler(active_schedule=None)

    with TestClient(app).websocket_connect("/api/ws/state") as ws:
        data = ws.receive_json()

    assert "optimization" in data, f"Missing 'optimization' key in WS frame: {data}"
    # Value is null because active_schedule is None
    assert data["optimization"] is None


def test_ws_state_includes_loads_key() -> None:
    """WS /api/ws/state frame includes 'loads' key; value is null when no HA REST client."""
    from starlette.testclient import TestClient

    from fastapi import FastAPI

    orch = MockOrchestrator(state=_make_state())
    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.state.orchestrator = orch
    # No ha_rest_client on app.state → _build_loads_dict returns None

    with TestClient(app).websocket_connect("/api/ws/state") as ws:
        data = ws.receive_json()

    assert "loads" in data, f"Missing 'loads' key in WS frame: {data}"
    # Value is null because ha_rest_client is absent
    assert data["loads"] is None


# ---------------------------------------------------------------------------
# Helper: CoordinatorState-based mock for role/decision/integration tests
# ---------------------------------------------------------------------------


def _make_coordinator_state(**overrides: Any) -> CoordinatorState:
    """Return a fully-populated CoordinatorState with sensible defaults."""
    defaults: dict[str, Any] = dict(
        combined_soc_pct=62.5,
        huawei_soc_pct=50.0,
        victron_soc_pct=68.75,
        huawei_available=True,
        victron_available=True,
        control_state="IDLE",
        huawei_discharge_setpoint_w=0,
        victron_discharge_setpoint_w=0,
        combined_power_w=0.0,
        huawei_charge_headroom_w=1500,
        victron_charge_headroom_w=2000.0,
        timestamp=time.monotonic(),
        huawei_role="PRIMARY_DISCHARGE",
        victron_role="SECONDARY_DISCHARGE",
        pool_status="NORMAL",
    )
    defaults.update(overrides)
    return CoordinatorState(**defaults)


class MockCoordinator:
    """Minimal stub for :class:`~backend.coordinator.Coordinator` with decision/integration support."""

    def __init__(
        self,
        state: CoordinatorState | None = None,
        decisions: list[dict] | None = None,
        integration_health: dict[str, dict] | None = None,
    ) -> None:
        self._state = state
        self._decisions = decisions or []
        self._integration_health = integration_health or {}
        self._last_error: str | None = None
        self.sys_config = SystemConfig()
        self._device_snapshot: dict = _make_device_snapshot()

    def get_state(self) -> CoordinatorState | None:
        return self._state

    def get_last_error(self) -> str | None:
        return self._last_error

    def get_working_mode(self) -> int | None:
        return None

    def get_device_snapshot(self) -> dict:
        return self._device_snapshot

    def get_decisions(self, limit: int = 20) -> list[dict]:
        entries = self._decisions[-limit:]
        entries = list(reversed(entries))
        return entries

    def get_integration_health(self) -> dict[str, dict]:
        return self._integration_health


def _build_test_app_coordinator(mock_coord: MockCoordinator) -> Any:
    """Build a test app with a MockCoordinator injected."""
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: mock_coord
    return app


# ---------------------------------------------------------------------------
# GET /api/decisions
# ---------------------------------------------------------------------------


_SAMPLE_DECISIONS = [
    {
        "timestamp": "2026-03-22T10:00:00Z",
        "trigger": "role_change",
        "huawei_role": "PRIMARY_DISCHARGE",
        "victron_role": "SECONDARY_DISCHARGE",
        "p_target_w": -3000.0,
        "huawei_allocation_w": -2000.0,
        "victron_allocation_w": -1000.0,
        "pool_status": "NORMAL",
        "reasoning": "Huawei higher SoC, assigned primary",
    },
    {
        "timestamp": "2026-03-22T10:05:00Z",
        "trigger": "allocation_shift",
        "huawei_role": "PRIMARY_DISCHARGE",
        "victron_role": "SECONDARY_DISCHARGE",
        "p_target_w": -4000.0,
        "huawei_allocation_w": -2500.0,
        "victron_allocation_w": -1500.0,
        "pool_status": "NORMAL",
        "reasoning": "Demand increased, redistributed",
    },
]


@pytest.mark.anyio
async def test_decisions_endpoint_returns_list() -> None:
    """GET /api/decisions returns JSON array of decision entries."""
    coord = MockCoordinator(
        state=_make_coordinator_state(),
        decisions=_SAMPLE_DECISIONS,
    )
    app = _build_test_app_coordinator(coord)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/decisions")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    # Newest first
    assert data[0]["timestamp"] == "2026-03-22T10:05:00Z"
    # All required fields present
    for entry in data:
        for key in (
            "timestamp", "trigger", "huawei_role", "victron_role",
            "p_target_w", "huawei_allocation_w", "victron_allocation_w",
            "pool_status", "reasoning",
        ):
            assert key in entry, f"Missing key '{key}' in decision entry"


@pytest.mark.anyio
async def test_decisions_endpoint_limit_clamp() -> None:
    """GET /api/decisions?limit=200 clamps to 100."""
    # Create 5 decisions to verify the call works
    decisions = _SAMPLE_DECISIONS * 3  # 6 entries
    coord = MockCoordinator(
        state=_make_coordinator_state(),
        decisions=decisions,
    )
    app = _build_test_app_coordinator(coord)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/decisions", params={"limit": 200})

    assert resp.status_code == 200
    # Should have received at most 100 (but we only have 6)
    data = resp.json()
    assert len(data) <= 100


@pytest.mark.anyio
async def test_decisions_endpoint_503_no_coordinator() -> None:
    """GET /api/decisions returns 503 when coordinator is None (setup-only mode)."""
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: None

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/decisions")

    assert resp.status_code == 503
    assert "not running" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/health — integrations
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_includes_integrations() -> None:
    """GET /api/health includes 'integrations' key with service health dicts."""
    integration_health = {
        "influxdb": {"service": "influxdb", "available": True, "last_error": None, "last_seen": "2026-03-22T10:00:00Z"},
        "evcc": {"service": "evcc", "available": True, "last_error": None, "last_seen": "2026-03-22T10:00:00Z"},
        "ha_mqtt": {"service": "ha_mqtt", "available": False, "last_error": "Connection refused", "last_seen": None},
        "telegram": {"service": "telegram", "available": True, "last_error": None, "last_seen": None},
    }
    coord = MockCoordinator(
        state=_make_coordinator_state(),
        integration_health=integration_health,
    )
    app = _build_test_app_coordinator(coord)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    data = resp.json()
    assert "integrations" in data
    integrations = data["integrations"]
    assert "influxdb" in integrations
    assert integrations["influxdb"]["available"] is True
    assert integrations["ha_mqtt"]["available"] is False
    assert integrations["ha_mqtt"]["last_error"] == "Connection refused"


@pytest.mark.anyio
async def test_health_integrations_empty_no_coordinator() -> None:
    """GET /api/health returns integrations={} when coordinator is None."""
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: None

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    data = resp.json()
    assert "integrations" in data
    assert data["integrations"] == {}


# ---------------------------------------------------------------------------
# GET /api/state — role fields verification
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_state_includes_roles() -> None:
    """GET /api/state includes huawei_role, victron_role, pool_status keys."""
    coord = MockCoordinator(state=_make_coordinator_state(
        huawei_role="PRIMARY_DISCHARGE",
        victron_role="CHARGING",
        pool_status="DEGRADED",
    ))
    app = _build_test_app_coordinator(coord)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/state")

    assert resp.status_code == 200
    data = resp.json()
    assert data["huawei_role"] == "PRIMARY_DISCHARGE"
    assert data["victron_role"] == "CHARGING"
    assert data["pool_status"] == "DEGRADED"


# ---------------------------------------------------------------------------
# GET /api/devices — role and setpoint fields
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_devices_includes_role_and_setpoint() -> None:
    """GET /api/devices includes role, setpoint_w per system and pool_status at top level."""
    coord = MockCoordinator(state=_make_coordinator_state(
        huawei_role="PRIMARY_DISCHARGE",
        victron_role="HOLDING",
        pool_status="NORMAL",
        huawei_discharge_setpoint_w=-2000,
        victron_discharge_setpoint_w=0,
    ))
    app = _build_test_app_coordinator(coord)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/devices")

    assert resp.status_code == 200
    data = resp.json()

    # Per-system role and setpoint
    assert data["huawei"]["role"] == "PRIMARY_DISCHARGE"
    assert data["huawei"]["setpoint_w"] == -2000
    assert data["victron"]["role"] == "HOLDING"
    assert data["victron"]["setpoint_w"] == 0

    # Top-level pool_status
    assert data["pool_status"] == "NORMAL"


# ---------------------------------------------------------------------------
# GET /api/optimization/forecast — solar forecast endpoint
# ---------------------------------------------------------------------------


def _make_day_plans() -> list:
    """Return a list of DayPlan instances for testing."""
    from datetime import date, datetime, timezone

    from backend.schedule_models import ChargeSlot, DayPlan

    slot = ChargeSlot(
        battery="huawei",
        target_soc_pct=85.0,
        start_utc=datetime(2026, 3, 23, 1, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc),
        grid_charge_power_w=3000,
    )
    return [
        DayPlan(
            day_index=0,
            date=date(2026, 3, 23),
            solar_forecast_kwh=18.5,
            consumption_forecast_kwh=12.3,
            net_energy_kwh=6.2,
            confidence=1.0,
            charge_target_kwh=3.5,
            slots=[slot],
            advisory=False,
        ),
        DayPlan(
            day_index=1,
            date=date(2026, 3, 24),
            solar_forecast_kwh=22.1,
            consumption_forecast_kwh=11.8,
            net_energy_kwh=10.3,
            confidence=0.8,
            charge_target_kwh=0.0,
            slots=[],
            advisory=True,
        ),
        DayPlan(
            day_index=2,
            date=date(2026, 3, 25),
            solar_forecast_kwh=5.2,
            consumption_forecast_kwh=13.0,
            net_energy_kwh=-7.8,
            confidence=0.6,
            charge_target_kwh=8.0,
            slots=[],
            advisory=True,
        ),
    ]


class MockWeatherScheduler:
    """Stub for WeatherScheduler with active_day_plans support."""

    def __init__(
        self,
        active_schedule=None,
        active_day_plans=None,
    ) -> None:
        self.active_schedule = active_schedule
        self.active_day_plans = active_day_plans


@pytest.mark.anyio
async def test_get_optimization_forecast_returns_503_when_no_scheduler() -> None:
    """GET /api/optimization/forecast returns 503 when scheduler is None."""
    from backend.api import get_scheduler
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: MockOrchestrator(state=_make_state())
    app.dependency_overrides[get_scheduler] = lambda: None

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/optimization/forecast")

    assert resp.status_code == 503


@pytest.mark.anyio
async def test_get_optimization_forecast_returns_503_when_no_day_plans() -> None:
    """GET /api/optimization/forecast returns 503 when active_day_plans is None."""
    from backend.api import get_scheduler
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: MockOrchestrator(state=_make_state())
    app.dependency_overrides[get_scheduler] = lambda: MockWeatherScheduler(active_day_plans=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/optimization/forecast")

    assert resp.status_code == 503


@pytest.mark.anyio
async def test_get_optimization_forecast_returns_200_with_days() -> None:
    """GET /api/optimization/forecast returns 200 with per-day forecast data."""
    import json

    from backend.api import get_scheduler
    from fastapi import FastAPI

    day_plans = _make_day_plans()
    scheduler = MockWeatherScheduler(active_day_plans=day_plans)
    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: MockOrchestrator(state=_make_state())
    app.dependency_overrides[get_scheduler] = lambda: scheduler

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/optimization/forecast")

    assert resp.status_code == 200
    data = resp.json()
    assert "days" in data
    assert len(data["days"]) == 3

    day0 = data["days"][0]
    assert day0["date"] == "2026-03-23"
    assert day0["day_index"] == 0
    assert day0["solar_kwh"] == 18.5
    assert day0["consumption_kwh"] == 12.3
    assert day0["net_kwh"] == 6.2
    assert day0["confidence"] == 1.0
    assert day0["charge_target_kwh"] == 3.5
    assert day0["advisory"] is False

    # Date fields must be ISO strings, not raw date objects
    for day in data["days"]:
        assert isinstance(day["date"], str)

    # JSON-serialisable
    json.dumps(data)


@pytest.mark.anyio
async def test_optimization_schedule_includes_day_plans_when_available() -> None:
    """GET /api/optimization/schedule includes day_plans when WeatherScheduler has them."""
    from backend.api import get_scheduler
    from fastapi import FastAPI

    schedule = _make_charge_schedule()
    day_plans = _make_day_plans()
    scheduler = MockWeatherScheduler(
        active_schedule=schedule,
        active_day_plans=day_plans,
    )
    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: MockOrchestrator(state=_make_state())
    app.dependency_overrides[get_scheduler] = lambda: scheduler

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/optimization/schedule")

    assert resp.status_code == 200
    data = resp.json()
    assert "day_plans" in data
    assert len(data["day_plans"]) == 3

    dp0 = data["day_plans"][0]
    assert dp0["date"] == "2026-03-23"
    assert dp0["solar_kwh"] == 18.5
    assert "slots" in dp0


@pytest.mark.anyio
async def test_optimization_schedule_no_day_plans_for_plain_scheduler() -> None:
    """GET /api/optimization/schedule omits day_plans key when using plain Scheduler."""
    from backend.api import get_scheduler
    from fastapi import FastAPI

    schedule = _make_charge_schedule()
    # MockScheduler has no active_day_plans attribute
    scheduler = MockScheduler(active_schedule=schedule)
    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_orchestrator] = lambda: MockOrchestrator(state=_make_state())
    app.dependency_overrides[get_scheduler] = lambda: scheduler

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/optimization/schedule")

    assert resp.status_code == 200
    data = resp.json()
    assert "day_plans" not in data


# ===========================================================================
# Tests: /api/ml/status endpoint (FCST-05)
# ===========================================================================


@pytest.mark.anyio
async def test_get_ml_status_returns_200():
    """GET /api/ml/status returns 200 with expected JSON structure."""
    from unittest.mock import MagicMock

    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)

    mock_forecaster = MagicMock()
    mock_forecaster.get_ml_status.return_value = {
        "models": {
            "heat_pump": {
                "trained": True,
                "last_trained_at": "2025-06-15T12:00:00",
                "sample_count": 720,
                "feature_names": ["a", "b"],
                "sklearn_version": "1.8.0",
            },
            "dhw": {"trained": False, "last_trained_at": None, "sample_count": 0, "feature_names": [], "sklearn_version": "1.8.0"},
            "base_load": {"trained": True, "last_trained_at": "2025-06-15T12:00:00", "sample_count": 720, "feature_names": ["a", "b"], "sklearn_version": "1.8.0"},
        },
        "mape": {"current": 12.5, "history": [{"date": "2025-06-14", "mape": 12.5}], "days_tracked": 1},
        "days_of_history": 30,
        "min_training_days": 14,
    }
    app.dependency_overrides[get_forecaster] = lambda: mock_forecaster

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ml/status")

    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "mape" in data
    assert data["min_training_days"] == 14
    assert data["models"]["heat_pump"]["trained"] is True


@pytest.mark.anyio
async def test_get_ml_status_returns_503_when_not_ready():
    """GET /api/ml/status returns 503 when forecaster is None."""
    from fastapi import FastAPI

    app = FastAPI(title="EMS-test")
    app.include_router(api_router)
    app.dependency_overrides[get_forecaster] = lambda: None

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ml/status")

    assert resp.status_code == 503

