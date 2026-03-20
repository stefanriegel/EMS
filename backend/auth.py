"""EMS authentication module (S04).

Provides:
- ``AdminConfig`` — dataclass for bcrypt hash + JWT secret (reads env vars)
- ``create_token`` / ``verify_token`` — JWT HS256 helpers
- ``check_password`` — bcrypt verify wrapper
- ``require_auth`` — FastAPI dependency (test-overrideable stub; real enforcement is in middleware)
- ``AuthMiddleware`` — Starlette middleware that guards all ``/api/*`` routes
- ``auth_router`` — APIRouter with POST /api/auth/login and POST /api/auth/logout

Auth is disabled (all requests pass through) when ``ADMIN_PASSWORD_HASH`` is
not set in the environment — this is the default dev mode and ensures all
existing tests run unchanged without any modifications.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)
_crypt = CryptContext(schemes=["bcrypt"], deprecated="auto")
_TOKEN_COOKIE = "ems_token"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class AdminConfig:
    """Runtime auth configuration read from environment variables.

    Fields
    ------
    password_hash
        Bcrypt hash of the admin password (``ADMIN_PASSWORD_HASH`` env var).
        Empty string → auth disabled.
    jwt_secret
        HMAC secret for signing JWTs (``JWT_SECRET`` env var).
        Defaults to a well-known dev placeholder — **must** be changed in
        production when ``ADMIN_PASSWORD_HASH`` is set.
    """

    password_hash: str = ""
    jwt_secret: str = "dev-secret-change-me"

    @classmethod
    def from_env(cls) -> "AdminConfig":
        return cls(
            password_hash=os.environ.get("ADMIN_PASSWORD_HASH", ""),
            jwt_secret=os.environ.get("JWT_SECRET", "dev-secret-change-me"),
        )


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def create_token(secret: str) -> str:
    """Create a signed HS256 JWT valid for 24 hours."""
    exp = datetime.now(tz=timezone.utc) + timedelta(hours=24)
    return jwt.encode({"sub": "admin", "exp": exp}, secret, algorithm="HS256")


def verify_token(token: str, secret: str) -> bool:
    """Return True iff *token* is a valid, non-expired HS256 JWT."""
    try:
        jwt.decode(token, secret, algorithms=["HS256"])
        return True
    except JWTError:
        return False


def check_password(plain: str, hashed: str) -> bool:
    """Return True iff *plain* matches the bcrypt *hashed* value."""
    try:
        return _crypt.verify(plain, hashed)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# FastAPI dependency (stub for test overrides)
# ---------------------------------------------------------------------------


def require_auth(request: Request) -> None:
    """FastAPI dependency — stub for ``dependency_overrides`` in tests only.

    Auth enforcement in production is handled by :class:`AuthMiddleware`.
    This function intentionally does nothing — it exists so tests can override
    it when they need to simulate authenticated/unauthenticated states without
    going through the middleware.
    """
    pass  # noqa: PIE790


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class AuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that enforces JWT cookie auth on ``/api/*`` routes.

    Exempt paths (always pass through, even when auth is enabled):
    - ``/api/health`` — liveness probe, must always be reachable
    - ``/api/auth/*`` — login/logout themselves cannot require auth
    - ``/api/setup/*`` — setup wizard is pre-auth by design

    Non-``/api/`` paths (static assets, React SPA) always pass through.

    When ``ADMIN_PASSWORD_HASH`` is absent, auth is disabled globally and
    all requests pass through — this is the default dev-mode behavior
    that keeps all existing tests working without any modifications.
    """

    def __init__(self, app, admin_cfg: AdminConfig) -> None:
        super().__init__(app)
        self._cfg = admin_cfg
        if admin_cfg.password_hash:
            logger.info("Auth middleware active")
            if admin_cfg.jwt_secret == "dev-secret-change-me":
                logger.warning(
                    "JWT_SECRET is default — set JWT_SECRET env var in production"
                )
        else:
            logger.info("Auth middleware disabled (no ADMIN_PASSWORD_HASH set)")

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Auth disabled — pass everything through.
        if not self._cfg.password_hash:
            return await call_next(request)

        path = request.url.path

        # Non-API paths (React SPA, static assets) — always pass.
        if not path.startswith("/api/"):
            return await call_next(request)

        # Exempt API paths.
        if (
            path == "/api/health"
            or path.startswith("/api/auth/")
            or path.startswith("/api/setup/")
        ):
            return await call_next(request)

        # All other /api/* — require a valid cookie.
        token = request.cookies.get(_TOKEN_COOKIE, "")
        if not verify_token(token, self._cfg.jwt_secret):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        return await call_next(request)


# ---------------------------------------------------------------------------
# Login / logout router
# ---------------------------------------------------------------------------


auth_router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    password: str


@auth_router.post("/login")
async def login(body: LoginRequest, request: Request) -> JSONResponse:  # noqa: ARG001
    """Authenticate and set an ``ems_token`` HttpOnly cookie.

    - When auth is disabled (no ``ADMIN_PASSWORD_HASH``), issues a token
      unconditionally so the frontend works in dev mode.
    - When auth is enabled, verifies the password against the bcrypt hash;
      returns 401 on mismatch.
    """
    cfg = AdminConfig.from_env()
    if not cfg.password_hash:
        # Dev mode — issue token without checking.
        token = create_token(cfg.jwt_secret)
    else:
        if not check_password(body.password, cfg.password_hash):
            return JSONResponse({"detail": "Incorrect password"}, status_code=401)
        token = create_token(cfg.jwt_secret)

    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        key=_TOKEN_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return resp


@auth_router.post("/logout")
async def logout() -> JSONResponse:
    """Clear the ``ems_token`` cookie."""
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(key=_TOKEN_COOKIE, httponly=True, samesite="lax")
    return resp
