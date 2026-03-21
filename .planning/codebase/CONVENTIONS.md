# Coding Conventions

**Analysis Date:** 2026-03-21

## Naming Patterns

### Files

**Python:**
- `snake_case.py` — all lowercase with underscores
- Examples: `auth.py`, `config.py`, `unified_model.py`, `ha_statistics_reader.py`
- Test files: `test_*.py` (e.g., `test_auth.py`, `test_unified_model.py`)

**TypeScript/React:**
- `PascalCase.tsx` for components — e.g., `App.tsx`, `PoolOverview.tsx`, `EnergyFlowCard.tsx`
- `camelCase.ts` for utilities/hooks — e.g., `useEmsSocket.ts`, `useEmsState.ts`, `types.ts`
- Test files: `*.spec.ts` (e.g., `energy-flow.spec.ts`, `login.spec.ts`)

### Functions

**Python:**
- `snake_case` for all functions, async or sync
- Examples: `ensure_jwt_secret()`, `create_token()`, `check_password()`, `_require_env()`
- Private/internal: leading underscore, e.g., `_require_env()`, `_make_state()`
- Factory/builder pattern: `from_env()` classmethod on dataclasses
  - Examples: `HuaweiConfig.from_env()`, `AdminConfig.from_env()`, `InfluxConfig.from_env()`

**TypeScript/React:**
- `camelCase` for functions
- Examples: `connect()`, `setData()`, `handleFbPool()`, `get_orchestrator()`
- React hooks: `useXxx()` naming convention
  - Examples: `useEmsSocket()`, `useEmsState()`
- Event handlers: `handleXxx()` or `onXxx` for callbacks
  - Examples: `handleFbPool()`, `handleFbDevices()`, `onPool`, `onDevices`

### Variables

**Python:**
- `snake_case` for all module-level and local variables
- Constants: `UPPER_SNAKE_CASE` (rare in codebase, mostly dataclass fields)
- Type hints: Always present for function parameters and returns
  - Examples: `def create_token(secret: str) -> str:`, `async def get_state() -> UnifiedPoolState | None:`
- Examples: `retryCountRef`, `unmountedRef`, `config_dir`, `charge_slots`

**TypeScript:**
- `camelCase` for all variables and refs
- Refs: `xxxRef` suffix convention
  - Examples: `retryCountRef`, `wsRef`, `timerRef`, `unmountedRef`, `connectRef`
- State setters: `setXxx` convention (React pattern)
  - Examples: `setData`, `setConnected`, `setRetryCount`, `setLocation`
- Optional fields: `xxx | null` or `xxx | undefined`

### Types

**Python:**
- Enum names: `PascalCase` (class name) with `UPPER_SNAKE_CASE` members
  - Example: `class ControlState(str, Enum): IDLE = "IDLE"` (string-enum for JSON serialization)
- Dataclass names: `PascalCase`
  - Examples: `UnifiedPoolState`, `AdminConfig`, `HuaweiConfig`, `SystemConfig`
- Type annotations: Use `from __future__ import annotations` at module top for forward references
- Union types: `X | Y` syntax (Python 3.10+)

**TypeScript:**
- Interface names: `PascalCase`, no `I` prefix
  - Examples: `PoolState`, `DevicesPayload`, `WsPayload`
- Type aliases: `PascalCase`
  - Example: `type ControlState = "IDLE" | "CHARGE" | "DISCHARGE" | ...`
- Nullable: `X | null` (explicit null, not `undefined` for model types)

## Code Style

### Formatting

**Python:**
- Line length: 88 characters (implicit; no enforced config found, but code follows it)
- Indentation: 4 spaces
- Imports organized in groups (standard library, third-party, local) separated by blank lines
  - Example from `auth.py`:
    ```python
    import logging
    import os
    import secrets
    from dataclasses import dataclass
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from fastapi import APIRouter, Request
    from jose import JWTError, jwt
    from passlib.context import CryptContext
    from pydantic import BaseModel
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    ```

**TypeScript/React:**
- ESLint config: `frontend/eslint.config.js`
- Rules: recommended, React hooks, React refresh
- No Prettier config found; formatting is code-style only
- Import order: React/external first, then relative imports
  - Example from `App.tsx`:
    ```typescript
    import React, { useState, useEffect } from "react";
    import { Route, Switch, useLocation } from "wouter";
    import { useEmsSocket } from "./hooks/useEmsSocket";
    import { useEmsState } from "./hooks/useEmsState";
    import { EnergyFlowCard } from "./components/EnergyFlowCard";
    ```

### Linting

**Python:**
- No explicit linter config in `pyproject.toml`; code follows PEP 8 conventions
- Docstring style: NumPy-style docstrings (Google-compatible)
  - Examples from `config.py`:
    ```python
    def ensure_jwt_secret(config_dir: str) -> str:
        """Return a persistent JWT secret, generating one on first call.

        Resolution order (first wins):

        1. ``JWT_SECRET`` env var — explicit override always wins.
        2. Persisted secret file at ``<config_dir>/.jwt_secret``.
        3. Generate a cryptographically random 32-byte hex secret...

        Parameters
        ----------
        config_dir:
            Directory where the secret file is written.

        Returns
        -------
        str
            The resolved secret string.
        """
    ```

**TypeScript:**
- ESLint enabled: `frontend/eslint.config.js` with `@eslint/js`, `typescript-eslint`
- Run: `npm run lint` in frontend directory
- Config extends: `js.configs.recommended`, `tseslint.configs.recommended`, `reactHooks.configs.flat.recommended`
- No auto-fixing; developers fix manually before commit

## Import Organization

### Order

1. Future imports (Python): `from __future__ import annotations`
2. Standard library (stdlib)
3. Third-party packages
4. Local/relative imports (preceded by blank line)

**Python example** (`auth.py`):
```python
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)
```

### Path Aliases

**TypeScript:**
- No path aliases configured; all imports are relative
- Pattern: `./` for siblings, `../` for parent directories
  - Examples:
    - `import { useEmsSocket } from "./hooks/useEmsSocket";` (sibling directory)
    - `import type { PoolState, DevicesPayload } from "./types";` (same directory)

**Python:**
- No aliases; always use `from backend.xxx import yyy` for absolute imports
  - Examples:
    - `from backend.auth import AdminConfig, create_token`
    - `from backend.drivers.huawei_models import HuaweiBatteryData`

## Error Handling

### Patterns

**Python:**
- Explicit exception catching, never bare `except:`
  - Example from `auth.py`:
    ```python
    def check_password(plain: str, hashed: str) -> bool:
        """Return True iff *plain* matches the bcrypt *hashed* value."""
        try:
            return _crypt.verify(plain, hashed)
        except Exception:  # noqa: BLE001
            return False
    ```
  - `# noqa: BLE001` suppresses the linter rule when deliberately catching broad exceptions
- Return `None` or a falsy value rather than raising when error is handled gracefully
  - Example from `config.py`:
    ```python
    def _require_env(key: str) -> str:
        """Return the value of *key* from the environment.

        Raises :class:`KeyError` if the variable is absent **or empty**.
        """
        value = os.environ.get(key, "")
        if not value:
            raise KeyError(key)
        return value
    ```
- Use `logging` for non-fatal issues (warnings, debug info)
  - Example from `auth.py`:
    ```python
    try:
        secret_path.write_text(secret)
        secret_path.chmod(0o600)
        logger.info("JWT secret: generated and saved to %s", secret_path)
    except OSError as exc:
        logger.warning(
            "JWT secret: could not persist to %s (%s) — secret will change on restart",
            secret_path, exc,
        )
    ```

**TypeScript/React:**
- Try-catch for JSON parsing and async operations
  - Example from `useEmsSocket.ts`:
    ```typescript
    ws.onmessage = (event: MessageEvent) => {
      if (unmountedRef.current) return;
      try {
        const parsed = JSON.parse(event.data as string) as WsPayload;
        setData(parsed);
      } catch (err) {
        console.warn("[useEmsSocket] failed to parse message:", err);
      }
    };
    ```
- Graceful degradation: Components render null or fallback UI on error
  - Example from `App.tsx`:
    ```typescript
    .catch(() => {
      // No backend available (preview/test environment) — stay on current route.
    });
    ```

### Logging

**Framework:** Python standard library `logging`

**Patterns:**
- Module-level logger: `logger = logging.getLogger(__name__)`
- Log levels: `logger.debug()`, `logger.info()`, `logger.warning()`
- Always include relevant context in log messages
  - Example: `logger.info("JWT secret: generated and saved to %s", secret_path)`
  - Example: `logger.warning("JWT secret: could not persist to %s (%s) — secret will change on restart", secret_path, exc)`

**TypeScript:**
- Console logging for observability and debugging
- Pattern: `console.log("[HookName] message")` with timestamp context
  - Example from `useEmsSocket.ts`:
    ```typescript
    const ts = new Date().toISOString();
    console.log(`[useEmsSocket] connecting to ${url} at ${ts}`);
    console.log(`[useEmsSocket] reconnect attempt ${attempt} at ${ts}`);
    console.log(`[useEmsSocket] connected at ${new Date().toISOString()}`);
    console.warn("[useEmsSocket] socket error at ...");
    ```

## Comments

### When to Comment

- **Why, not what**: Comments explain *intent* and *non-obvious decisions*, not what the code does
- Module docstrings: Always present, describe purpose and public API
  - Examples: `backend/auth.py`, `backend/config.py`
- Function docstrings: Always present for public functions, rarely for private helpers
  - Exception: simple helpers may be inlined with short comments
- Inline comments: Used sparingly for complex logic or surprising behavior
  - Example from `auth.py`:
    ```python
    if not cfg.password_hash:
        # Dev mode — issue token without checking.
        token = create_token(cfg.jwt_secret)
    ```

### JSDoc/TSDoc

**Usage:**
- React component JSDoc: Block comment above component function describing inputs and behavior
  - Example from `App.tsx`:
    ```typescript
    /**
     * FallbackConsumer — renders when WS has disconnected. Calls useEmsState()
     * and passes results up via callbacks. Kept as a child component so the hook
     * is called unconditionally within its own component scope.
     */
    function FallbackConsumer({
      onPool,
      onDevices,
    }: {
      onPool: (v: PoolState | null) => void;
      onDevices: (v: DevicesPayload | null) => void;
    }) {
    ```

**Python docstrings:**
- NumPy/Google style with sections: Parameters, Returns, Raises
  - Example from `config.py`:
    ```python
    @classmethod
    def from_env(cls) -> "HuaweiConfig":
        """Construct a :class:`HuaweiConfig` from environment variables.

        Required:
            ``HUAWEI_HOST`` — hostname or IP address of the Modbus proxy.

        Optional (with defaults):
            ``HUAWEI_PORT``             — TCP port (default 502).

        Raises:
            KeyError: if ``HUAWEI_HOST`` is not set.
        """
    ```

## Function Design

### Size

- Prefer smaller functions (< 30 lines) focused on a single responsibility
- Complex logic broken into helper functions with descriptive names
- Examples:
  - `ensure_jwt_secret()` — 50 lines including extensive logging/comments
  - `check_password()` — 5 lines
  - `verify_token()` — 6 lines

### Parameters

- **Python:** Use dataclass configs for large parameter sets
  - Example: `HuaweiConfig` bundles `host`, `port`, `master_slave_id`, `slave_slave_id`, `timeout_s`
  - Pattern: `from_env()` classmethod handles environment variable resolution
- **TypeScript:** Destructured object parameters for component props
  - Example from `FallbackConsumer`:
    ```typescript
    function FallbackConsumer({
      onPool,
      onDevices,
    }: {
      onPool: (v: PoolState | null) => void;
      onDevices: (v: DevicesPayload | null) => void;
    }) {
    ```

### Return Values

**Python:**
- Explicit return types on all functions
- Dataclass instances for complex returns: `UnifiedPoolState`, `AdminConfig`
- `None` for optional returns: `EvccConfig | None`, `ChargeSchedule | None`
- Boolean for predicates: `check_password() -> bool`, `verify_token() -> bool`

**TypeScript:**
- Explicit types for components and hooks
- Hooks return objects with state and setters
  - Example: `useEmsSocket()` returns `EmsSocketState { data, connected, retryCount }`
- Components implicitly return JSX or JSX | null

## Module Design

### Exports

**Python:**
- No barrel files; each module exports its main class/function
- Example structure:
  - `auth.py` → exports `AdminConfig`, `ensure_jwt_secret()`, `create_token()`, `verify_token()`, `check_password()`, `AuthMiddleware`, `auth_router`
  - `config.py` → exports multiple config dataclasses: `HuaweiConfig`, `VictronConfig`, `SystemConfig`, `OrchestratorConfig`, `InfluxConfig`, etc.
  - `unified_model.py` → exports `ControlState`, `UnifiedPoolState`

**TypeScript/React:**
- No barrel files; imports are always direct
  - Example: `import { PoolOverview } from "./components/PoolOverview";`
  - Not: `import { PoolOverview } from "./components";`
- Hooks exported directly from hook files
  - Example: `export function useEmsSocket(url: string): EmsSocketState { ... }`

### Organization Pattern

**Python modules follow a consistent structure:**
1. Module docstring (purpose, public API, dependencies)
2. `from __future__ import annotations` (if needed)
3. Imports (stdlib, third-party, local)
4. Module-level constants and utilities
5. Main classes/functions (public API)
6. Helper functions (prefixed with `_`)

**Example: `backend/auth.py`**
```
Docstring (long, explains JWT lifecycle and middleware behavior)
→ from __future__ import annotations
→ Imports (logging, os, secrets, dataclasses, datetime, fastapi, jose, passlib, starlette)
→ Module-level setup (logger, _crypt, _TOKEN_COOKIE, _SECRET_FILENAME)
→ Section 1: JWT secret lifecycle (ensure_jwt_secret function)
→ Section 2: Config (AdminConfig dataclass)
→ Section 3: Token helpers (create_token, verify_token, check_password)
→ Section 4: FastAPI dependency (require_auth)
→ Section 5: Middleware (AuthMiddleware class)
→ Section 6: Router (auth_router and login/logout endpoints)
```

---

*Convention analysis: 2026-03-21*
