# Testing Patterns

**Analysis Date:** 2026-03-21

## Test Framework

### Runner

**Python:**
- Framework: `pytest` (version 8+)
- Config: `pyproject.toml` with:
  - `asyncio_mode = "auto"` (for pytest-asyncio)
  - `anyio_mode = "auto"` (for pytest-anyio)
- Location: `/Users/mustermann/Documents/coding/ems/tests/`

**TypeScript/React:**
- Framework: `Playwright` (version 1.58.2+)
- Config: `frontend/playwright.config.ts`
- Location: `/Users/mustermann/Documents/coding/ems/frontend/tests/`
- Base URL: `http://localhost:4173` (production build preview)

### Assertion Library

**Python:**
- `pytest` built-in assertions (`assert`)
- Optional: `unittest.mock` for mocking

**TypeScript:**
- `@playwright/test` — `expect()` and Playwright locators
  - Examples: `expect(page.locator(...)).toBeVisible()`, `expect(fs.existsSync(...)).toBe(true)`

### Run Commands

**Python:**
```bash
pytest tests/                    # Run all tests
pytest tests/test_auth.py       # Run specific test file
pytest tests/ -v                # Verbose output
pytest tests/ --tb=short        # Shorter traceback
pytest tests/ -k "test_check"   # Run tests matching pattern
```

**TypeScript/React:**
```bash
npm run test:playwright         # Run all Playwright tests (from frontend/)
playwright test                 # Run tests directly
playwright test --debug         # Debug mode
npm run preview                 # Start preview server (used by tests)
```

## Test File Organization

### Location

**Python:**
- Separate `tests/` directory at project root
- Mirrors backend structure where relevant:
  - `tests/test_auth.py` → `backend/auth.py`
  - `tests/test_config.py` → `backend/config.py`
  - `tests/drivers/test_huawei_driver.py` → `backend/drivers/huawei_driver.py`
- File path: `/Users/mustermann/Documents/coding/ems/tests/`

**TypeScript:**
- Co-located with source in `frontend/tests/`
- Naming: `{component-or-feature}.spec.ts`
  - Examples: `energy-flow.spec.ts`, `login.spec.ts`, `device-detail.spec.ts`
- File path: `/Users/mustermann/Documents/coding/ems/frontend/tests/`

### Naming

**Python:** `test_*.py`
- Examples: `test_auth.py`, `test_unified_model.py`, `test_evcc_client.py`

**TypeScript:** `*.spec.ts`
- Examples: `energy-flow.spec.ts`, `login.spec.ts`

### Structure

**Python test directory:**
```
tests/
├── __init__.py
├── test_auth.py
├── test_config.py
├── test_unified_model.py
├── test_evcc_client.py
├── test_notifier.py
├── drivers/
│   ├── __init__.py
│   ├── test_huawei_driver.py
│   └── test_victron_driver.py
└── [more test files...]
```

**TypeScript test directory:**
```
frontend/tests/
├── energy-flow.spec.ts
├── login.spec.ts
├── device-detail.spec.ts
├── loads-card.spec.ts
├── tariff-card.spec.ts
├── optimization-card.spec.ts
├── setup-wizard.spec.ts
├── loads-card-entities.spec.ts
└── screenshots/
    └── [test screenshot outputs]
```

## Test Structure

### Suite Organization

**Python pattern** (`test_unified_model.py`):
```python
"""Unit tests for UnifiedPoolState, ControlState, SystemConfig, and OrchestratorConfig.

No live hardware required.  All tests are pure dataclass/enum math — no async,
no network, no mocking of external drivers beyond the helper constructors below.

Coverage:
  - ``UnifiedPoolState.from_readings()`` — weighted-average SoC math (30/64/94)
  - ``ControlState`` enum — all four members present and string-serialisable
"""
from __future__ import annotations

import time
import pytest

from backend.config import OrchestratorConfig, SystemConfig
from backend.unified_model import ControlState, UnifiedPoolState

# Helpers at top of file
def _make_battery(**overrides) -> HuaweiBatteryData:
    """Return a fully-populated HuaweiBatteryData with sensible defaults."""
    defaults: dict = {
        "pack1_soc_pct": 60.0,
        "pack1_charge_discharge_power_w": 0,
        # ...
    }
    defaults.update(overrides)
    return HuaweiBatteryData(**defaults)

# Test classes for organization
class TestControlState:
    def test_all_members_present(self):
        members = {m.name for m in ControlState}
        assert members == {"IDLE", "DISCHARGE", "CHARGE", "HOLD", "GRID_CHARGE", "DISCHARGE_LOCKED"}

    def test_values_are_strings(self):
        for member in ControlState:
            assert isinstance(member.value, str)
```

**Patterns:**
- Module-level docstring: explains coverage and test scope
- Helper functions: prefixed with `_make_xxx()` for factories
- Test classes: organize related tests (e.g., `TestControlState`)
- Test methods: `test_xxx_yyy()` describing the specific case

**TypeScript/Playwright pattern** (`energy-flow.spec.ts`):
```typescript
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test('energy flow card is visible at 375px mobile viewport with no console errors', async ({ page }) => {
  const consoleErrors: string[] = [];

  // Setup: collect console errors
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  // Action: navigate
  await page.goto('/');

  // Assert: visibility
  await expect(page.locator('[data-testid="energy-flow-card"]')).toBeVisible({ timeout: 10_000 });

  // Assert: screenshot saved
  const screenshotDir = path.join(__dirname, 'screenshots');
  if (!fs.existsSync(screenshotDir)) fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotDir, 'energy-flow-375px.png'),
    fullPage: false,
  });
  expect(fs.existsSync(path.join(screenshotDir, 'energy-flow-375px.png'))).toBe(true);

  // Assert: no errors (filter known harmless noise)
  const realErrors = consoleErrors.filter(
    (e) =>
      !e.includes('WebSocket') &&
      !e.includes('ws://') &&
      !e.includes('Failed to load resource') &&
      !e.includes('/api/')
  );
  expect(realErrors).toHaveLength(0);
});
```

**Patterns:**
- Single `test()` block per scenario (not nested describe blocks)
- Setup phase: collect data, mock response handlers
- Action phase: navigate or interact
- Assert phase: check expectations
- Timeouts: explicit `{ timeout: 10_000 }` for async expectations

## Mocking

### Framework

**Python:**
- `unittest.mock` — `AsyncMock`, `MagicMock`, `patch`
- Pattern: `from unittest.mock import AsyncMock, MagicMock, patch`

**TypeScript:**
- Playwright: Native browser testing (no mocks) — tests against real React components
- HTTP mocking: Playwright intercept/abort patterns (if needed)
- No client-side mocking library (Vitest, Jest) in use

### Patterns

**Python async mocking** (`test_evcc_client.py`):
```python
from unittest.mock import AsyncMock, patch

async def test_get_state_success():
    """Full fixture round-trip."""
    client = EvccClient(config=EvccConfig())

    # Mock the httpx.AsyncClient.post method
    with patch("backend.evcc_client.httpx.AsyncClient.post") as mock_post:
        mock_resp = AsyncMock()
        mock_resp.json.return_value = EVCC_STATE_FIXTURE
        mock_post.return_value = mock_resp

        result = await client.get_state()

    # Assert
    assert result is not None
    assert result.res.status == "Optimal"
```

**Python sync mocking** (`test_auth.py`):
```python
from unittest.mock import patch

with patch.dict("os.environ", env, clear=False):
    admin_cfg = AdminConfig.from_env()
    # code uses patched environment
```

**Playwright page interactions** (not mocking, real component testing):
```typescript
// No mocks — tests interact with real React components
await page.goto('/');
await expect(page.locator('[data-testid="energy-flow-card"]')).toBeVisible();
```

### What to Mock

**Mock:**
- External HTTP clients: `httpx.AsyncClient`, `httpx.Client`
- File system operations for error paths
- Third-party services (EVCC, InfluxDB, MQTT)
- Time-dependent behavior: clock functions
- Environment variables: `os.environ` patches

**Mock via fixtures/factories (not unittest.mock):**
- Test dataclasses: Use `_make_xxx()` helper functions
- Mock orchestrator instances: Custom `MockOrchestrator` class
  - Example from `test_auth.py`:
    ```python
    class MockOrchestrator:
        def __init__(self, state: UnifiedPoolState | None = None) -> None:
            self._state = state
            self.sys_config = SystemConfig()

        def get_state(self) -> UnifiedPoolState | None:
            return self._state

        def get_last_error(self) -> str | None:
            return None
    ```

**Do NOT mock:**
- `dataclasses` (use instances directly)
- `Enum` members
- Pure calculation functions
- React components in Playwright tests (test real DOM)

### What NOT to Mock

- Dataclass instances — instantiate directly
- Enum types — test the actual enum
- Pure math functions — test directly
- Core business logic — test with real objects
- React components in E2E tests — test the real rendered output

## Fixtures and Factories

### Test Data

**Python helper functions** (`test_unified_model.py`):
```python
def _make_battery(**overrides) -> HuaweiBatteryData:
    """Return a fully-populated HuaweiBatteryData with sensible defaults."""
    defaults: dict = {
        "pack1_soc_pct": 60.0,
        "pack1_charge_discharge_power_w": 0,
        "pack1_status": 1,
        "pack2_soc_pct": 58.0,
        "pack2_charge_discharge_power_w": 0,
        "pack2_status": 1,
        "total_soc_pct": 59.0,
        "total_charge_discharge_power_w": 0,
        "max_charge_power_w": 5000,
        "max_discharge_power_w": 5000,
        "working_mode": 2,
    }
    defaults.update(overrides)
    return HuaweiBatteryData(**defaults)

# Usage in test
def test_soc_calculation():
    battery = _make_battery(pack1_soc_pct=50.0)  # Override one field
    assert battery.pack1_soc_pct == 50.0
```

**Python module-level fixtures** (`test_evcc_client.py`):
```python
# Timestamps for fixtures
_T0 = "2026-01-15T23:00:00+00:00"
_TS_8 = [
    "2026-01-15T23:00:00+00:00",
    "2026-01-15T23:15:00+00:00",
    # ... 8 slots
]

# Complex fixture dict (mirrors real API response)
EVCC_STATE_FIXTURE: dict = {
    "evopt": {
        "res": {
            "status": "Optimal",
            "objective_value": 42.5,
            "batteries": [
                {
                    "title": "Emma Akku 1",
                    "charging_power": [3000.0, 2000.0, 1000.0, 0.0, ...],
                    "state_of_charge": [0.50, 0.55, 0.58, 0.60, ...],
                },
                # ... more batteries
            ],
        },
    },
}

# Usage
async def test_parse_state():
    result = _parse_state(EVCC_STATE_FIXTURE)
    assert result.res.status == "Optimal"
```

**Playwright screenshot fixtures** (`energy-flow.spec.ts`):
```typescript
const screenshotDir = path.join(__dirname, 'screenshots');
if (!fs.existsSync(screenshotDir)) fs.mkdirSync(screenshotDir, { recursive: true });
await page.screenshot({
  path: path.join(screenshotDir, 'energy-flow-375px.png'),
  fullPage: false,
});
```

### Location

**Python:**
- Helpers defined at module top (after imports, before classes)
- Named `_make_xxx()` or `_build_xxx()`
- Placed in same test file where used
- Complex fixtures may live in separate `conftest.py` (not present here)

**TypeScript:**
- Imported at test top
- Constants defined at module level: `const __dirname = path.dirname(__filename);`
- Screenshot directory created lazily in test

## Coverage

### Requirements

- None enforced in `pyproject.toml` or `pytest` config
- Tests written for critical paths only (auth, config, orchestrator state)
- No coverage report generation configured

### View Coverage

```bash
# Python (manual with pytest-cov plugin if installed)
pip install pytest-cov
pytest tests/ --cov=backend --cov-report=html

# TypeScript (Playwright does not generate coverage)
# Tests are E2E only; coverage not applicable
```

## Test Types

### Unit Tests

**Python unit tests** (majority of test suite):
- Scope: Single function or method
- Dependencies: Mocked (external services, file I/O)
- Async: Supported via `@pytest.mark.anyio` or implicit `anyio_mode = "auto"`
- Examples:
  - `test_check_password_correct()` — tests `check_password()` with correct password
  - `test_verify_token_valid()` — tests `verify_token()` with valid JWT
  - `TestControlState.test_all_members_present()` — tests enum completeness

**Pattern:**
```python
def test_check_password_correct() -> None:
    assert check_password("testpass", _TEST_HASH) is True

def test_check_password_wrong() -> None:
    assert check_password("wrong", _TEST_HASH) is False
```

### Integration Tests

**Python integration tests** (auth, config, orchestration):
- Scope: Multiple components together (e.g., middleware + router)
- Setup: Full app creation via `_build_app()` factory
- Communication: HTTPx async client via `ASGITransport`
- Example: `test_auth_middleware_blocks_unauthenticated_api()` tests AuthMiddleware with actual FastAPI app
- Pattern from `test_auth.py`:
  ```python
  @pytest.mark.anyio
  async def test_auth_middleware_disabled_by_default():
      """Auth disabled when ADMIN_PASSWORD_HASH absent."""
      app = _build_app(mock_orch=MockOrchestrator(_make_state()))
      async with httpx.AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as client:
          resp = await client.get("/api/state")
      assert resp.status_code == 200
  ```

### E2E Tests

**TypeScript/Playwright E2E tests:**
- Scope: Full user workflows (login, dashboard render, WebSocket reconnect)
- Environment: Real browser, production build preview at `localhost:4173`
- No backend: Tests run against `npm run preview` (static HTML build)
- Test disconnection and degraded modes
- Example: `energy-flow.spec.ts` tests that card renders without backend
  ```typescript
  test('energy flow card is visible at 375px mobile viewport with no console errors', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('[data-testid="energy-flow-card"]')).toBeVisible({ timeout: 10_000 });
  });
  ```

## Common Patterns

### Async Testing

**Python async tests** (`test_evcc_client.py`):
```python
# K007: anyio_mode = "auto" auto-collects async def test_* without explicit @pytest.mark.anyio
async def test_get_state_success():
    """Full fixture round-trip."""
    client = EvccClient(config=EvccConfig())

    with patch("backend.evcc_client.httpx.AsyncClient.post") as mock_post:
        mock_resp = AsyncMock()
        mock_resp.json.return_value = EVCC_STATE_FIXTURE
        mock_post.return_value = mock_resp

        result = await client.get_state()

    assert result is not None
```

**Explicit marker (if needed):**
```python
@pytest.mark.anyio
async def test_explicit_anyio():
    # Must have anyio_mode = "auto" in pyproject.toml
    pass
```

### Error Testing

**Python exception testing:**
```python
def test_require_env_missing():
    """Raises KeyError when env var absent or empty."""
    with pytest.raises(KeyError):
        _require_env("NONEXISTENT_VAR")

def test_require_env_empty_string():
    """Raises KeyError when env var is empty string (treated as missing)."""
    with patch.dict("os.environ", {"MY_VAR": ""}, clear=False):
        with pytest.raises(KeyError):
            _require_env("MY_VAR")
```

**TypeScript error filtering** (`energy-flow.spec.ts`):
```typescript
// Collect console errors
const consoleErrors: string[] = [];
page.on('console', (msg) => {
  if (msg.type() === 'error') consoleErrors.push(msg.text());
});

// Filter out known harmless noise
const realErrors = consoleErrors.filter(
  (e) =>
    !e.includes('WebSocket') &&
    !e.includes('ws://') &&
    !e.includes('Failed to load resource') &&
    !e.includes('/api/')
);

// Assert no real errors
expect(realErrors).toHaveLength(0);
```

### FastAPI App Factory Pattern

**Python HTTPx + ASGITransport** (`test_auth.py`):
```python
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

    # Inject state without lifespan
    app.state.orchestrator = mock_orch
    app.state.setup_config_path = "/tmp/ems-test-config.json"
    # ... more state

    if mock_orch is not None:
        app.dependency_overrides[get_orchestrator] = lambda: mock_orch

    return app

# Usage in test
@pytest.mark.anyio
async def test_auth_login_correct_password():
    app = _build_app(mock_orch=MockOrchestrator(_make_state()))
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/auth/login", json={"password": "testpass"})
    assert resp.status_code == 200
```

---

*Testing analysis: 2026-03-21*
