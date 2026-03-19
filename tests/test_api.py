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

from backend.api import api_router, get_orchestrator
from backend.config import SystemConfig
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

    def get_state(self) -> UnifiedPoolState | None:
        return self._state

    def get_last_error(self) -> str | None:
        return self._last_error


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
