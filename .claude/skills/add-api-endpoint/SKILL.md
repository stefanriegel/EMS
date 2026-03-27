---
name: add-api-endpoint
description: Adds a new FastAPI REST endpoint to backend/api.py with dependency injection, proper error responses (422/503), and async test using httpx.AsyncClient + ASGITransport. Use when user says 'add endpoint', 'new route', 'create API', or 'expose data via REST'. Do NOT use for WebSocket changes or frontend work. Follows get_orchestrator/get_scheduler DI pattern and @pytest.mark.anyio test convention.
---
# Add API Endpoint

## Critical

- All endpoints live in `backend/api.py` on the `api_router = APIRouter(prefix="/api")` — never create a second router file.
- Dependencies are injected via `Depends(get_orchestrator)`, `Depends(get_tariff_engine)`, or `Depends(get_scheduler)`. Never access app state directly in route handlers — always use the `Depends()` injection pattern defined in `backend/api.py`.
- Return `503` when a dependency is not yet ready (e.g., `orchestrator.get_state()` returns `None`). Return `400` for invalid user input. Let Pydantic handle `422` for request body validation.
- Tests use `httpx.AsyncClient` with `httpx.ASGITransport` — never `TestClient` from Starlette.
- Tests use `@pytest.mark.anyio` — never `@pytest.mark.asyncio`.

## Instructions

1. **Define the route in `backend/api.py`.**
   Add the endpoint under the appropriate section comment block. Follow this exact pattern:
   ```python
   @api_router.get("/your-endpoint")
   async def get_your_endpoint(
       orchestrator: Coordinator = Depends(get_orchestrator),
   ) -> dict[str, Any]:
       """One-line description.

       Raises
       ------
       HTTPException(503)
           If the orchestrator is not ready.
       """
       state = orchestrator.get_state()
       if state is None:
           raise HTTPException(status_code=503, detail="Coordinator not yet ready")
       return {"key": state.some_field}
   ```
   - Import types at the top: `from backend.some_module import SomeType`
   - Use `dict[str, Any]` as return type (not Pydantic response models)
   - For POST endpoints, define a Pydantic `BaseModel` with `Field()` constraints in the "Pydantic request / response models" section
   - Verify: the route decorator uses `api_router`, not `app`

2. **Choose the right dependency function.**
   - `get_orchestrator(request) -> Coordinator` — for state, config, decisions, device data
   - `get_tariff_engine(request) -> CompositeTariffEngine` — for tariff/pricing data
   - `get_scheduler(request) -> Scheduler | None` — for charge schedule data (returns `None` if not wired)
   - If you need a new dependency, follow the existing pattern in `backend/api.py`:
     ```python
     def get_thing(request: Request) -> ThingType:
         return getattr(request.app.state, "thing", None)
     ```
   - Verify: the attribute is set during lifespan startup in `backend/main.py`

3. **Handle optional dependencies gracefully.**
   If the dependency might be `None` (like `get_scheduler`), type the parameter as `X | None` and return 503:
   ```python
   async def get_schedule(
       scheduler: Scheduler | None = Depends(get_scheduler),
   ) -> dict[str, Any]:
       if scheduler is None:
           raise HTTPException(status_code=503, detail="Scheduler not available")
   ```
   - Verify: the endpoint returns valid JSON in both success and error paths

4. **Write the test in `tests/test_api.py`.**
   Follow this exact structure:
   ```python
   @pytest.mark.anyio
   async def test_get_your_endpoint_returns_200(test_app: Any) -> None:
       """GET /api/your-endpoint returns 200 with expected data."""
       async with httpx.AsyncClient(
           transport=httpx.ASGITransport(app=test_app), base_url="http://test"
       ) as client:
           resp = await client.get("/api/your-endpoint")

       assert resp.status_code == 200
       data = resp.json()
       assert "key" in data
   ```
   - Use the `test_app` fixture for normal state, `test_app_no_state` for 503 tests
   - For custom mock data, build an inline app with `_build_test_app(MockOrchestrator(state=_make_state(...)))`
   - For new DI overrides, add to the `app.dependency_overrides` dict in `_build_test_app` or inline
   - Verify: run `python -m pytest tests/test_api.py -q` — all tests pass

5. **Test the error path.**
   ```python
   @pytest.mark.anyio
   async def test_get_your_endpoint_returns_503_when_not_ready(test_app_no_state: Any) -> None:
       """GET /api/your-endpoint returns 503 before first poll."""
       async with httpx.AsyncClient(
           transport=httpx.ASGITransport(app=test_app_no_state), base_url="http://test"
       ) as client:
           resp = await client.get("/api/your-endpoint")

       assert resp.status_code == 503
   ```
   - Verify: both success and error tests pass

## Examples

**User says:** "Add an endpoint to expose device snapshot data"

**Actions:**
1. Add to `backend/api.py`:
   ```python
   @api_router.get("/devices")
   async def get_devices(
       orchestrator: Coordinator = Depends(get_orchestrator),
   ) -> dict[str, Any]:
       """Return per-device hardware snapshot."""
       return orchestrator.get_device_snapshot()
   ```
2. Add to `tests/test_api.py`:
   ```python
   @pytest.mark.anyio
   async def test_get_devices_returns_200(test_app: Any) -> None:
       async with httpx.AsyncClient(
           transport=httpx.ASGITransport(app=test_app), base_url="http://test"
       ) as client:
           resp = await client.get("/api/devices")
       assert resp.status_code == 200
       data = resp.json()
       assert "huawei" in data
       assert "victron" in data
   ```
3. Run `python -m pytest tests/test_api.py -q` — passes.

**Result:** `GET /api/devices` returns `{"huawei": {...}, "victron": {...}}`

## Common Issues

- **`AttributeError: 'State' object has no attribute 'xxx'`**: The dependency function reads an attribute that the lifespan in `backend/main.py` never sets. Add the attribute assignment in the lifespan, or use `getattr()` with a `None` fallback and handle it.
- **Test gets `422` instead of expected response**: Your POST test is missing required fields from the Pydantic model. Send all required fields in the JSON body.
- **Test gets `404`**: The test app doesn't include `api_router`. Use `_build_test_app()` which calls `app.include_router(api_router)`.
- **`ImportError: cannot import name 'get_xxx' from 'backend.api'`**: You defined the dependency function but forgot to export it or the test imports the wrong name. Check the exact function name in `backend/api.py`.
- **Test hangs or fails with event loop errors**: Make sure the test is decorated with `@pytest.mark.anyio` (not `asyncio`) and the function is `async def`.