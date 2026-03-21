# Codebase Concerns

**Analysis Date:** 2026-03-21

## Tech Debt

### Assertions in Production Driver Code

**Issue:** Defensive assertions used instead of explicit validation in driver read methods.
- Files: `backend/drivers/huawei_driver.py` (7 asserts), `backend/drivers/victron_driver.py` (5 asserts), `backend/evcc_mqtt_driver.py` (1 assert)
- Impact: Assertions are compiled out with Python's `-O` flag. In production, if a driver is called before `connect()`, the assertion fails with `AssertionError` instead of a clear runtime exception. This violates Python's assertion conventions — assertions should only validate internal invariants, not validate preconditions.
- Example: `backend/drivers/huawei_driver.py:203` — `assert self._client is not None, "Driver not connected — call connect() first"`
- Fix approach: Replace all assertions with explicit precondition checks that raise `RuntimeError` or `ValueError`, e.g. `if self._client is None: raise RuntimeError("Driver not connected — call connect() first")`

### Broad Exception Catching (`Exception`)

**Issue:** Multiple fire-and-forget error handlers catch `Exception` base class without discriminating error types.
- Files: `backend/influx_writer.py` (3 catches), `backend/influx_reader.py` (3 catches), `backend/ha_statistics_reader.py` (6 catches), `backend/supervisor_client.py` (4 catches), `backend/main.py` (6 catches)
- Impact: Swallows programming errors (e.g., `KeyError`, `AttributeError`, `TypeError`) that should be surfaced. Makes debugging harder when integration failures are masked by overly broad exception handlers.
- Example: `backend/influx_writer.py:98-99` — `except Exception as exc: logger.warning("influx write failed: %s", exc)`
- Fix approach: Catch specific exception types — `influxdb_client` exceptions should be caught separately from `TypeError`/`ValueError`. Separate fire-and-forget handlers for I/O failures from handlers for data validation failures.

## Fragile Areas

### Driver Connection State Management

**Issue:** Drivers rely on manual `connect()`/`close()` lifecycle with implicit synchronous preconditions.
- Files: `backend/drivers/huawei_driver.py`, `backend/drivers/victron_driver.py`
- Why fragile: If `connect()` is called twice, or if `close()` is called without `connect()`, behavior is undefined. The Victron driver spawns a background paho MQTT thread on `connect()` — if this is called in the wrong order or context, thread management becomes fragile.
- Safe modification: Encapsulate lifecycle in a factory function that returns an already-connected driver, or use a state machine that explicitly validates transitions. Add tests that verify double-connect and out-of-order close are handled gracefully.
- Test coverage: `tests/test_victron_driver.py` and `tests/test_huawei_driver.py` exist but focus on happy path. Missing tests for:
  - Double `connect()` call
  - `close()` without `connect()`
  - Exception during `connect()` leaves partially-initialized state

### Influx Write Fire-and-Forget Pattern

**Issue:** InfluxDB writes silently fail without retry or fallback.
- Files: `backend/influx_writer.py`
- Why fragile: Any transient network issue (timeout, ECONNREFUSED, DNS resolution failure) logs a WARNING and continues. If InfluxDB is down for 5+ minutes, an entire window of metrics is lost with no recovery mechanism. The orchestrator continues normally, creating data gaps that are invisible until grafana queries are run.
- Safe modification: Implement a simple buffer (in-memory ring buffer or file-based queue) that retries failed writes on the next cycle. Or add metrics to track write failure rate and expose via `/api/health`.
- Test coverage: `tests/test_influx_writer.py` exists but all test cases use a real async client. Missing: unit tests for error paths (simulated connection failures, timeout).

### Consumption Forecaster Cold-Start

**Issue:** Forecaster falls back to a constant (`_seasonal_fallback_kwh = 10.0 kWh`) when HA DB has <14 days of history.
- Files: `backend/consumption_forecaster.py:99-`, `backend/config.py:585`
- Why fragile: On first deployment, the scheduler will use the same 10 kWh fallback for 14 days, regardless of actual household consumption. If a household uses 30 kWh/day, the battery will chronically undercharge. No warning is logged to operators beyond the INFO log — users may not notice for days.
- Safe modification: Expose the fallback constant as a configurable parameter (`HA_ML_FALLBACK_KWH`). Add an explicit WARNING log on first schedule computation if fallback is used: `"ConsumptionForecaster: no historical data yet, using fallback 10 kWh — forecast will improve after N days of data"`. Consider interpolating from a multi-level fallback (7.5 kWh for summer, 12 kWh for winter) based on month.
- Test coverage: `tests/test_consumption_forecaster.py` tests happy path but not cold-start behavior with insufficient data. Missing: explicit test_cold_start_uses_fallback.

### Home Assistant Statistics Reader — Missing Entities

**Issue:** Reader silently returns `[]` when a requested `statistic_id` does not exist in HA.
- Files: `backend/ha_statistics_reader.py:72-96`
- Why fragile: If HA entity `sensor.ems_esp_boiler_aussentemperatur` is renamed or deleted, the forecaster has no outdoor temperature data and falls back to seasonal constant. This is invisible to operators — the logs show a single WARNING but the scheduler continues with degraded accuracy.
- Safe modification: Add an `entity_missing` field to `ConsumptionForecast` or return a tuple `(data, missing_entities: list[str])`. Expose missing entities in `/api/v1/status` so the web UI can alert operators to misconfigured sensors. On startup, validate all configured entities exist and emit ERROR if not.
- Test coverage: `tests/test_ha_statistics_reader.py` exists but tests only valid entity IDs. Missing: test_missing_entity_returns_empty_list and test_returns_consistent_length_across_calls.

## Scaling Limits

### Orchestrator Control Loop Interval

**Issue:** Orchestrator polls drivers every 5 seconds with no backpressure or rate-limiting.
- Files: `backend/orchestrator.py:155` — `loop_interval_s: float = 5.0`
- Current capacity: Each poll is I/O-bound (Modbus TCP + MQTT subscriptions). At 5s intervals, a single orchestrator instance is limited to systems with sub-second response latency. If either driver's `read_*` method blocks for >4s (network timeout, overloaded gateway), the next cycle is skipped.
- Limit: Beyond 2–3 systems being polled (Huawei + Victron + 1 auxillary sensor), cycle timing becomes unreliable. No built-in timeout on per-cycle execution time.
- Scaling path: Add an explicit cycle timeout (e.g., 4s) that cancels long-running polls. Monitor cycle execution time and log WARNING if a cycle takes >3s. Consider parallel polling of independent systems via `asyncio.gather` with a timeout wrapper.

### HA Statistics Reader — Full Table Scan

**Issue:** `read_entity_hourly()` does a full table scan with no index optimization hints.
- Files: `backend/ha_statistics_reader.py:95-120`
- Current capacity: HA's `statistics` table can grow to 100k+ rows for power sensors. A full scan without a `statistic_id` index is O(n). With 2–3 entities queried daily, typical latency is <50ms. At 20+ entities the query becomes slow.
- Limit: Beyond 15 sensor entities, daily 90-day queries can take >500ms, potentially blocking the nightly scheduler loop.
- Scaling path: Query HA's schema to determine whether a `statistic_id` index exists. If not, emit a WARNING and suggest the operator create one: `CREATE INDEX idx_stats_sid ON statistics(statistic_id)`. Cache query results in memory to avoid re-reading the same entity within a 60-minute window.

### InfluxDB Query Load

**Issue:** Scheduler queries InfluxDB for historical consumption every night without pagination.
- Files: `backend/influx_reader.py:135-165`
- Current capacity: A 90-day query at 5-minute resolution yields ~25k points. InfluxDB typically returns these in <500ms. Cluster InfluxDB can handle 100s of such queries/minute.
- Limit: With >10 EMS instances writing to the same InfluxDB, nightly scheduler queries can pile up and timeout if the instance has limited resources.
- Scaling path: Add explicit query timeout (e.g., 5s) to `query_*` methods. If query times out, fall back to the seasonal constant and log WARNING. Consider aggregate rollups (e.g., query hourly instead of 5-minute resolution).

## Security Considerations

### Token Logging

**Issue:** While InfluxDB tokens are correctly excluded from logs, MQTT passwords and HA tokens are not explicitly guarded.
- Files: `backend/config.py:394,407,479-481`, `backend/main.py:240,245`
- Risk: If DEBUG logging is enabled, environment variables may be dumped in tracebacks or verbose output. If passwords appear in error messages from `paho-mqtt` or `httpx`, they will be logged.
- Current mitigation: Tokens are stored in private attributes (`InfluxConfig._token`), but no explicit scrubbing of exception messages. Config classes define fields as strings without `repr` masking.
- Recommendations:
  1. Override `__repr__` on `InfluxConfig`, `HaMqttConfig`, `HaRestConfig` to mask `token`/`password` fields.
  2. Add a log record filter that scrubs lines containing `"token=", "password=", "INFLUXDB_TOKEN"` before they are written.
  3. Document that passwords/tokens must never be set via command-line arguments (only env vars).

### Admin Password Hash Storage

**Issue:** Admin password hash is read from `ADMIN_PASSWORD_HASH` environment variable and stored in memory.
- Files: `backend/auth.py:127-143`, `backend/config.py:611-621`
- Risk: If a core dump is taken or memory is inspected, the hash is readable. The hash itself is not secret, but its presence indicates whether authentication is enabled.
- Current mitigation: Hash is bcrypt (slow), so even if extracted it cannot be quickly reversed. No plaintext passwords are stored.
- Recommendations:
  1. Document that `ADMIN_PASSWORD_HASH` should only be set on machines with restricted access (e.g., Home Assistant Supervisor).
  2. Consider removing the hash from `AdminConfig.__repr__` to prevent accidental logging.
  3. Add a startup check that emits WARNING if `ADMIN_PASSWORD_HASH` is set to a well-known or empty value.

## Missing Critical Features

### Graceful InfluxDB Downgrade Path

**Issue:** No way to migrate data if InfluxDB becomes unavailable or is removed after being configured.
- Files: `backend/influx_writer.py`, `backend/influx_reader.py`
- Problem: If InfluxDB is turned off (e.g., to save resources), scheduler falls back to seasonal constant and loses all historical context. There is no config option to temporarily disable InfluxDB writes without losing the client or breaking assumptions.
- Blocks: Users cannot test the system without InfluxDB or transition to a different timeseries store without code changes.
- Fix approach:
  1. Already partially addressed by `InfluxConfig.enabled` flag — ensure this is checked before instantiating the client in `main.py`.
  2. Verify that scheduler gracefully handles `writer=None` (currently passes writer to `compute_schedule`).
  3. Add explicit test: `test_scheduler_works_without_influx` that runs with `writer=None`.

### Hysteresis Dead-Band Not Configurable Per System

**Issue:** Single `hysteresis_w` parameter applies to both Huawei and Victron systems.
- Files: `backend/config.py:158-160`, `backend/orchestrator.py:800-850` (apply methods)
- Problem: Huawei's Modbus response time is ~500ms, Victron's AC phase response is <100ms. A 200W dead-band can cause unnecessary Victron oscillations while being too narrow for Huawei. No way to tune per-system.
- Blocks: Advanced users cannot optimize control stability for their specific hardware.
- Fix approach: Add `huawei_hysteresis_w` and `victron_hysteresis_w` config fields (with fallback to the single `hysteresis_w` for backward compatibility). Update apply logic to use system-specific thresholds.

### No Health Check for Driver Initialization Failures

**Issue:** If both drivers fail to connect at startup, lifespan raises an exception and the app crashes. No degraded-mode operation.
- Files: `backend/main.py:200-320` (lifespan startup)
- Problem: A misconfigured `HUAWEI_HOST` or `VICTRON_HOST` will crash the entire app. The API cannot return `/api/health` or any status, forcing the operator to SSH in and check logs.
- Blocks: Troubleshooting is harder on remote Home Assistant instances where SSH is disabled.
- Fix approach:
  1. Wrap driver connection in try/except in the lifespan.
  2. If a driver fails to connect, log ERROR and set a flag `app.state.huawei_failed_init = True` but continue.
  3. Return 503 Service Unavailable from `/api/health` if either driver failed init.
  4. Return 200 OK from `/api/health` if at least one driver is connected (degraded mode).
  5. Document the behavior: "If both drivers fail, the API returns 503; if one fails, available data is provided."

## Known Bugs

### Victron MQTT Discovery Timeout Race Condition

**Issue:** If keepalive message is published before subscribing to the discovery topic, discovery can hang.
- Files: `backend/drivers/victron_driver.py:142-159`
- Symptoms: `asyncio.TimeoutError` after waiting `discovery_timeout_s` (default 15s) for portalId.
- Trigger: Race condition — if paho's background thread publishes the Serial message between unsubscribe (step 0) and subscribe (step 1), the message is missed.
- Workaround: Increase `VICTRON_DISCOVERY_TIMEOUT_S` env var or manually restart the addon.
- Fix approach:
  1. Ensure subscription happens *before* any background thread activity (lock or sequence).
  2. Add test: `test_discovery_with_message_already_published` that simulates receiving the message before we subscribe.
  3. Consider using a message cache in the paho on_message callback so late arrivals don't cause timeout.

### Scheduler Charge Window Boundary Calculation

**Issue:** Charge window boundaries use fixed minutes-from-midnight with no DST adjustment.
- Files: `backend/config.py:337-348` (default 30–300 min), `backend/scheduler.py:120-150` (window selection)
- Symptoms: On DST transitions (Mar 25 in EU), the effective charge window shifts by 1 hour relative to local time.
- Trigger: Occurs every spring/autumn when clocks change.
- Workaround: Manually update `SCHEDULER_CHARGE_START_MIN` and `SCHEDULER_CHARGE_END_MIN` on DST transition.
- Fix approach:
  1. Store charge windows as local time ranges (e.g., "00:30–05:00 Europe/London") rather than minutes from midnight.
  2. Convert to minutes-from-midnight at runtime using `pytz` or `zoneinfo` to handle DST.
  3. Add test: `test_charge_window_across_dst_transition` with mock `datetime.now()`.

## Test Coverage Gaps

### Driver Failure Modes

**What's not tested:** Reconnection logic in `HuaweiDriver._with_reconnect()`.
- Files: `backend/drivers/huawei_driver.py:169-187`
- Risk: If the first attempt raises `ConnectionException` but the second (after reconnect) raises a different exception type (e.g., `asyncio.TimeoutError`), the error is not retried.
- Priority: High — connection failures are common in field deployments and double-failures are silent.
- Fix approach: Add test `test_huawei_reconnect_on_connection_exception` that mocks `_client.get_multiple()` to fail with `ConnectionException` once, then succeed. Verify reconnect is called exactly once.

### Orchestrator State Machine Transitions

**What's not tested:** Invalid state transitions or edge cases in the debounce state machine.
- Files: `backend/orchestrator.py:700-850` (state transition logic)
- Risk: Transitions between `CHARGE`, `DISCHARGE`, `HOLD` states may have off-by-one errors in debounce counting or hysteresis suppression.
- Priority: High — incorrect control state can lead to unnecessary power swings.
- Fix approach: Add parameterized test that exercises all 9 state transitions (`CHARGE→DISCHARGE`, `CHARGE→HOLD`, etc.) and verifies debounce counter increments/resets correctly.

### Consumption Forecaster ML Training

**What's not tested:** Behavior when HA statistics contain NaN or outlier values.
- Files: `backend/consumption_forecaster.py:140-200` (training loop)
- Risk: If a sensor reports NaN for power consumption, sklearn's `GradientBoostingRegressor` will either train on NaN (producing invalid predictions) or raise `ValueError`.
- Priority: Medium — depends on HA sensor reliability. Some users may have misconfigured sensors.
- Fix approach: Add validation in `_build_features()` to drop NaN/inf values and log WARNING. Add test `test_consumption_forecaster_with_nan_values` that includes NaN in the training data.

### WebSocket Broadcast Failure Handling

**What's not tested:** What happens if broadcasting to WebSocket clients fails.
- Files: `backend/ws_manager.py` (if it exists) — likely `backend/api.py:600-700` (WebSocket route handlers)
- Risk: If a single WebSocket client is slow or disconnected, broadcast may timeout and block the orchestrator loop.
- Priority: Medium — affects real-time UI responsiveness.
- Fix approach: Add timeout to broadcast operations (e.g., `asyncio.wait_for(..., timeout=1.0)`) and log WARNING on timeout. Test: `test_websocket_broadcast_timeout`.

## Dependencies at Risk

### scikit-learn Optional Dependency

**Issue:** scikit-learn is a heavy dependency (numpy + scipy + ~50 MB) but is only used for consumption forecasting (optional feature).
- Files: `backend/consumption_forecaster.py:157-158` (lazy import with try/except)
- Risk: Adds bloat to Home Assistant Supervisor addon (~50 MB) even if user doesn't enable the forecaster. Installation can fail on memory-constrained systems (e.g., older Raspberry Pi).
- Current mitigation: Already implemented as optional lazy import — code does not crash if sklearn is missing, but functionality is disabled.
- Migration plan: Keep as-is for now. If a lighter alternative is needed, evaluate `statsmodels` (for seasonal decomposition) or simple exponential smoothing (built-in).

### paho-mqtt 2.x Deprecation Warnings

**Issue:** paho-mqtt 2.x deprecated callback signatures and requires explicit `CallbackAPIVersion`.
- Files: `backend/drivers/victron_driver.py:135`, `backend/ha_mqtt_client.py`, `backend/setup_api.py:129`
- Risk: When paho-mqtt 3.0 is released, these callback signatures will break. Current code uses VERSION2 which is stable, but future versions may change.
- Current mitigation: Code is already updated to use `CallbackAPIVersion.VERSION2`. Comment at `setup_api.py:129` documents the requirement.
- Migration plan: No action needed for now. On paho 3.0 release, update callbacks to new signature (anticipated to be similar to VERSION2).

### influxdb-client Async API

**Issue:** `influxdb_client[async]` is built on top of the sync client with thread-pool wrapping, not a true async library.
- Files: `backend/influx_writer.py:29-30`, `backend/main.py:62`
- Risk: Each write operation spawns a thread, potentially creating hundreds of threads if write rate is high. No backpressure or queue limiting.
- Current mitigation: Writes are fire-and-forget with no retry, so queue cannot grow unbounded. Typical write rate is ~1/5s, creating only a few threads.
- Migration plan: If write volume exceeds 10/s, consider switching to `asyncio`-native client (e.g., `aiohttp` + InfluxDB line protocol) or using a background writer task with an explicit queue.

