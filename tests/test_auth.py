"""Tests for the admin authentication layer (S04 T01).

Covers:
- AuthMiddleware disabled when ADMIN_PASSWORD_HASH absent (existing tests unaffected)
- AuthMiddleware blocks unauthenticated /api/state when ADMIN_PASSWORD_HASH set
- Exempt paths: /api/health, /api/auth/*, /api/setup/*
- POST /api/auth/login: correct password → 200 + ems_token cookie; wrong → 401
- Authenticated request with valid ems_token cookie → passes middleware
- get_health() degraded-mode bugfix: orchestrator=None returns 200 with status="offline"
- WS closes with code 4401 when auth enabled and no valid cookie

Pattern: httpx.AsyncClient + ASGITransport (K021), @pytest.mark.anyio, create_app() factory.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from backend.auth import AdminConfig, check_password, create_token, verify_token, ensure_jwt_secret
from backend.config import SystemConfig
from backend.unified_model import ControlState, UnifiedPoolState

# ---------------------------------------------------------------------------
# Bcrypt hash for "testpass" — generated once and reused across tests.
# Re-generating is slow (~0.2 s per call); a module-level constant avoids it.
# ---------------------------------------------------------------------------
from passlib.context import CryptContext as _CC

_BCRYPT = _CC(schemes=["bcrypt"], deprecated="auto")
_TEST_HASH: str = _BCRYPT.hash("testpass")
_WRONG_HASH: str = _BCRYPT.hash("other")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**overrides: Any) -> UnifiedPoolState:
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


class MockOrchestrator:
    def __init__(self, state: UnifiedPoolState | None = None) -> None:
        self._state = state
        self.sys_config = SystemConfig()

    def get_state(self) -> UnifiedPoolState | None:
        return self._state

    def get_last_error(self) -> str | None:
        return None

    def get_device_snapshot(self) -> dict:
        return {"huawei": {}, "victron": {}}

    def set_scheduler(self, *a: Any) -> None:
        pass

    def set_notifier(self, *a: Any) -> None:
        pass

    def set_evcc_monitor(self, *a: Any) -> None:
        pass


def _build_app(mock_orch: MockOrchestrator | None = None, *, env_patch: dict | None = None):
    """Build a test app using create_app().

    The lifespan is replaced with a no-op so hardware is never contacted.
    The orchestrator is injected directly onto app.state.
    """
    from fastapi import FastAPI

    from backend.api import api_router, get_orchestrator
    from backend.auth import AdminConfig, AuthMiddleware, auth_router
    from backend.setup_api import setup_router

    env = env_patch or {}

    with patch.dict("os.environ", env, clear=False):
        admin_cfg = AdminConfig.from_env()

        app = FastAPI(title="EMS-test")
        app.add_middleware(AuthMiddleware, admin_cfg=admin_cfg)
        app.include_router(api_router)
        app.include_router(setup_router)
        app.include_router(auth_router)

    # Inject state without lifespan.
    app.state.orchestrator = mock_orch
    app.state.setup_config_path = "/tmp/ems-test-config.json"  # prevent AttributeError in setup_api
    app.state.scheduler = None
    app.state.metrics_reader = None
    app.state.tariff_engine = None
    app.state.evcc_driver = None
    app.state.ha_mqtt_client = None
    app.state.ha_rest_client = None

    if mock_orch is not None:
        app.dependency_overrides[get_orchestrator] = lambda: mock_orch
    else:
        # Override get_orchestrator to return None.
        # - get_health() accepts None → returns 200 with status="offline"
        # - get_state() and other endpoints will crash with AttributeError on None,
        #   but our tests only check that the status code is not 401.
        app.dependency_overrides[get_orchestrator] = lambda: None

    return app


# ---------------------------------------------------------------------------
# Unit tests for auth helpers
# ---------------------------------------------------------------------------


def test_check_password_correct() -> None:
    assert check_password("testpass", _TEST_HASH) is True


def test_check_password_wrong() -> None:
    assert check_password("wrong", _TEST_HASH) is False


def test_verify_token_valid() -> None:
    token = create_token("mysecret")
    assert verify_token(token, "mysecret") is True


def test_verify_token_wrong_secret() -> None:
    token = create_token("mysecret")
    assert verify_token(token, "othersecret") is False


def test_verify_token_garbage() -> None:
    assert verify_token("notavalidtoken", "secret") is False


def test_admin_config_from_env_defaults() -> None:
    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("ADMIN_PASSWORD_HASH", None)
        os.environ.pop("JWT_SECRET", None)
        cfg = AdminConfig.from_env()
    assert cfg.password_hash == ""
    assert cfg.jwt_secret == "dev-secret-change-me"


# ---------------------------------------------------------------------------
# ensure_jwt_secret
# ---------------------------------------------------------------------------


def test_ensure_jwt_secret_uses_env_var(tmp_path, monkeypatch) -> None:
    """Explicit JWT_SECRET env var is always returned unchanged."""
    monkeypatch.setenv("JWT_SECRET", "my-explicit-secret")
    result = ensure_jwt_secret(str(tmp_path))
    assert result == "my-explicit-secret"
    # No file should be written when env var is present
    assert not (tmp_path / ".jwt_secret").exists()


def test_ensure_jwt_secret_generates_on_first_run(tmp_path, monkeypatch) -> None:
    """First call with no env var and no file generates and persists a secret."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    secret = ensure_jwt_secret(str(tmp_path))
    assert len(secret) == 64  # 32 bytes hex = 64 chars
    assert (tmp_path / ".jwt_secret").exists()
    assert (tmp_path / ".jwt_secret").read_text() == secret
    # Must also be injected into env for subsequent from_env() calls
    assert os.environ.get("JWT_SECRET") == secret


def test_ensure_jwt_secret_reuses_persisted_file(tmp_path, monkeypatch) -> None:
    """Second call loads the same secret from the file."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    secret_file = tmp_path / ".jwt_secret"
    secret_file.write_text("persisted-secret-value")

    result = ensure_jwt_secret(str(tmp_path))
    assert result == "persisted-secret-value"
    assert os.environ.get("JWT_SECRET") == "persisted-secret-value"


def test_ensure_jwt_secret_ignores_dev_default(tmp_path, monkeypatch) -> None:
    """The dev-secret-change-me placeholder is treated as unset."""
    monkeypatch.setenv("JWT_SECRET", "dev-secret-change-me")
    result = ensure_jwt_secret(str(tmp_path))
    # Should generate a new secret, not use the placeholder
    assert result != "dev-secret-change-me"
    assert len(result) == 64


def test_ensure_jwt_secret_persisted_wins_over_dev_default(tmp_path, monkeypatch) -> None:
    """Persisted file is used even when env var is the dev default."""
    monkeypatch.setenv("JWT_SECRET", "dev-secret-change-me")
    (tmp_path / ".jwt_secret").write_text("from-file-secret")
    result = ensure_jwt_secret(str(tmp_path))
    assert result == "from-file-secret"


def test_ensure_jwt_secret_two_calls_same_secret(tmp_path, monkeypatch) -> None:
    """Two consecutive calls return the same secret."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    first = ensure_jwt_secret(str(tmp_path))
    monkeypatch.delenv("JWT_SECRET", raising=False)
    second = ensure_jwt_secret(str(tmp_path))
    assert first == second


# ---------------------------------------------------------------------------
# Middleware integration tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_auth_disabled_passes_all_requests() -> None:
    """No ADMIN_PASSWORD_HASH set → auth disabled → /api/state is reached (not intercepted with 401).

    Uses a MockOrchestrator with state=None so get_state raises 503 (clean, not a crash).
    """
    app = _build_app(mock_orch=MockOrchestrator(state=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/state")
    # Auth disabled → not 401. 503 = orchestrator not ready (expected with state=None).
    assert resp.status_code == 503
    assert "Unauthorized" not in resp.text


@pytest.mark.anyio
async def test_auth_blocks_unauthenticated_request() -> None:
    """ADMIN_PASSWORD_HASH set → GET /api/state without cookie returns 401."""
    app = _build_app(
        mock_orch=MockOrchestrator(state=_make_state()),
        env_patch={"ADMIN_PASSWORD_HASH": _TEST_HASH},
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/state")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Unauthorized"}


@pytest.mark.anyio
async def test_health_exempt_when_auth_enabled() -> None:
    """GET /api/health returns 200 even when auth is enabled and no cookie is set."""
    app = _build_app(
        mock_orch=None,  # degraded mode
        env_patch={"ADMIN_PASSWORD_HASH": _TEST_HASH},
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "offline"


@pytest.mark.anyio
async def test_setup_status_exempt_when_auth_enabled() -> None:
    """GET /api/setup/status returns 200 (not 401) when auth is enabled."""
    app = _build_app(
        mock_orch=None,
        env_patch={"ADMIN_PASSWORD_HASH": _TEST_HASH},
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/setup/status")
    # 200 or 422 is fine; the important thing is NOT 401.
    assert resp.status_code != 401


# ---------------------------------------------------------------------------
# Login / logout endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_login_wrong_password_returns_401() -> None:
    """POST /api/auth/login with wrong password → 401."""
    app = _build_app(
        mock_orch=None,
        env_patch={"ADMIN_PASSWORD_HASH": _TEST_HASH},
    )
    with patch.dict("os.environ", {"ADMIN_PASSWORD_HASH": _TEST_HASH}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/auth/login", json={"password": "wrongpass"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect password"


@pytest.mark.anyio
async def test_login_correct_password_sets_cookie() -> None:
    """POST /api/auth/login with correct password → 200 + ems_token cookie."""
    app = _build_app(
        mock_orch=None,
        env_patch={"ADMIN_PASSWORD_HASH": _TEST_HASH},
    )
    with patch.dict("os.environ", {"ADMIN_PASSWORD_HASH": _TEST_HASH}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/auth/login", json={"password": "testpass"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # Cookie must be set in the Set-Cookie header.
    assert "ems_token" in resp.headers.get("set-cookie", "")


@pytest.mark.anyio
async def test_authenticated_request_passes() -> None:
    """GET /api/state with a valid ems_token cookie passes middleware (returns 503, not 401)."""
    secret = "test-secret"
    token = create_token(secret)
    env = {"ADMIN_PASSWORD_HASH": _TEST_HASH, "JWT_SECRET": secret}
    # Use MockOrchestrator with state=None so get_state raises 503 cleanly.
    app = _build_app(mock_orch=MockOrchestrator(state=None), env_patch=env)
    with patch.dict("os.environ", env, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            cookies={"ems_token": token},
        ) as client:
            resp = await client.get("/api/state")
    # Passed middleware → 503 (orchestrator not ready), not 401.
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_logout_clears_cookie() -> None:
    """POST /api/auth/logout → 200 + Set-Cookie that clears ems_token."""
    app = _build_app(mock_orch=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    set_cookie = resp.headers.get("set-cookie", "")
    assert "ems_token" in set_cookie


# ---------------------------------------------------------------------------
# get_health() degraded-mode bugfix
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_health_degraded_mode() -> None:
    """GET /api/health returns 200 with status='offline' when orchestrator is None.

    This proves the S04 bugfix — previously the endpoint would crash when
    orchestrator was None because it used Depends(get_orchestrator) which
    returns app.state.orchestrator directly.
    """
    app = _build_app(mock_orch=None)  # orchestrator is None on app.state
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "offline"
    assert data["huawei_available"] is False
    assert data["victron_available"] is False
    assert data["last_error"] is None


# ---------------------------------------------------------------------------
# WebSocket auth test
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ws_closes_with_4401_when_auth_enabled() -> None:
    """WS /api/ws/state closes with code 4401 when auth enabled and no valid cookie."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    env = {"ADMIN_PASSWORD_HASH": _TEST_HASH}
    app = _build_app(
        mock_orch=MockOrchestrator(state=_make_state()),
        env_patch=env,
    )
    # TestClient is sync; run it with the env patch active at connection time.
    with patch.dict("os.environ", env, clear=False):
        client = TestClient(app, raise_server_exceptions=False)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/ws/state") as ws:
                ws.receive_json()  # should not reach here — WS closed immediately
    assert exc_info.value.code == 4401
