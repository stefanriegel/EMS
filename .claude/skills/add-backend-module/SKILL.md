---
name: add-backend-module
description: Creates a new Python backend module with dataclass models, async methods, logging, and matching test file. Use when user says 'add module', 'new backend service', 'create driver', 'new client', or adds files to backend/. Do NOT use for frontend work, modifying existing modules, or adding API routes.
---
# Add Backend Module

## Critical

- Every module starts with `from __future__ import annotations` as the first code import
- Logger must use `logging.getLogger(__name__)` (or `logging.getLogger("ems.<name>")` for integration clients)
- All external I/O methods must be `async` and fire-and-forget: catch exceptions, log as WARNING, return `None` — never let integration failures crash the orchestrator
- Never log secrets (tokens, passwords). Only log host/port/bucket-level identifiers at INFO
- Config dataclasses use `from_env()` classmethod with `_require_env()` for mandatory vars and `os.environ.get()` with defaults for optional vars
- Test file must exist before the module is considered complete

## Instructions

### Step 1: Create the config dataclass in `backend/config.py`

Add a new `@dataclass` following the existing pattern:

```python
@dataclass
class FooConfig:
    """Connection config for Foo.

    Attributes:
        host: IP or hostname.
        port: TCP port (default 1234).
        timeout_s: Per-request timeout in seconds.
    """
    host: str
    port: int = 1234
    timeout_s: float = 10.0

    @classmethod
    def from_env(cls) -> "FooConfig":
        return cls(
            host=_require_env("FOO_HOST"),
            port=int(os.environ.get("FOO_PORT", "1234")),
        )
```

**Verify:** `_require_env()` is used for mandatory vars. Optional vars use `os.environ.get()` with string defaults cast to the correct type.

### Step 2: Create the module file at `backend/<module_name>.py`

Follow this structure exactly:

```python
"""One-line summary of what this module does.

Observability
-------------
- ``WARNING "foo operation failed: <exc>"`` — on HTTP/parse error.
- Returns ``None`` on any failure; callers check for ``None``.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx  # or other third-party import

from backend.config import FooConfig

logger = logging.getLogger(__name__)


class FooClient:
    """Async client for Foo.

    Parameters
    ----------
    config:
        :class:`~backend.config.FooConfig` instance.
    """

    def __init__(self, config: FooConfig) -> None:
        self._config = config

    async def get_something(self) -> SomeType | None:
        """Fetch data from Foo.

        Returns ``None`` on any error (logged as WARNING).
        """
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_s) as client:
                resp = await client.get(f"http://{self._config.host}:{self._config.port}/endpoint")
                resp.raise_for_status()
                return _parse(resp.json())
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            logger.warning("foo get_something failed: %s", exc)
            return None
```

**Verify:** Module docstring has Observability section. All async I/O methods return `T | None` and catch+log exceptions. No bare `except:` blocks.

### Step 3: Create the test file at `tests/test_<module_name>.py`

Follow the project test conventions:

```python
"""Tests for <module_name>."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.<module_name> import FooClient
from backend.config import FooConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(**overrides) -> FooConfig:
    defaults = dict(host="127.0.0.1", port=1234, timeout_s=1.0)
    defaults.update(overrides)
    return FooConfig(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_something_success():
    client = FooClient(_cfg())
    # mock httpx or inject test data
    result = await client.get_something()
    assert result is not None


@pytest.mark.anyio
async def test_get_something_returns_none_on_error():
    client = FooClient(_cfg(host="unreachable"))
    result = await client.get_something()
    assert result is None
```

**Verify:** All async tests use `@pytest.mark.anyio`. Helper functions use `_underscore_prefix`. Config is constructed directly (not from env) in tests.

### Step 4: Wire into lifespan (if needed)

If the module is used by the orchestrator, add construction in `backend/main.py` lifespan. Follow the existing optional-dependency pattern:

```python
foo_client: FooClient | None = None
try:
    foo_cfg = FooConfig.from_env()
    foo_client = FooClient(foo_cfg)
except KeyError:
    logger.info("FOO_HOST not set — Foo integration disabled")
```

**Verify:** Missing config logs INFO and sets client to `None`. The orchestrator/coordinator accepts the client as an optional parameter.

### Step 5: Run tests

```bash
python -m pytest tests/test_<module_name>.py -q
```

**Verify:** All tests pass. No import errors.

## Examples

**User says:** "Add a new weather API client module"

**Actions:**
1. Add `WeatherConfig` dataclass to `backend/config.py` with `from_env()` reading `WEATHER_HOST`
2. Create `backend/weather_client.py` with `WeatherClient` class, async `get_forecast()` returning `Forecast | None`, fire-and-forget error handling logging `"weather get_forecast failed: %s"`
3. Create `tests/test_weather_client.py` with `_cfg()` helper, `@pytest.mark.anyio` async tests for success and error paths
4. Wire `WeatherClient` into `backend/main.py` lifespan with `except KeyError` fallback
5. Run `python -m pytest tests/test_weather_client.py -q`

**Result:** `backend/weather_client.py` and `tests/test_weather_client.py` exist, tests pass, module follows all project conventions.

## Common Issues

- **`ModuleNotFoundError: No module named 'backend.new_module'`**: The file must be in `backend/` directory (not a subdirectory) and `backend/__init__.py` must exist. Check with `ls backend/__init__.py`.
- **`@pytest.mark.anyio` tests skipped or erroring**: Ensure `pytest-anyio` is installed (`pip install -e ".[dev]"`) and `pyproject.toml` has `anyio_mode = "auto"` under `[tool.pytest.ini_options]`.
- **`KeyError` on startup with optional integration**: Wrap `FooConfig.from_env()` in `try/except KeyError` in lifespan. Never let a missing optional config crash the app.
- **Tests pollute environment**: Never call `from_env()` in tests — construct config dataclasses directly with explicit values. Use `patch.dict(os.environ, ...)` only when testing `from_env()` itself.
- **`TypeError: object NoneType can't be used in 'await' expression`**: Your mock is missing `AsyncMock`. Use `AsyncMock(return_value=...)` not `MagicMock` for async methods.