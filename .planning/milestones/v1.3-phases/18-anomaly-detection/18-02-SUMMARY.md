---
phase: 18-anomaly-detection
plan: 02
subsystem: ml
tags: [anomaly-detection, coordinator-integration, rest-api, telegram-alerts]

# Dependency graph
requires:
  - phase: 18-anomaly-detection
    provides: AnomalyDetector class with check_cycle(), nightly_train(), get_events(), get_battery_health()
provides:
  - Coordinator per-cycle anomaly checking via _run_anomaly_check()
  - Nightly IsolationForest training in _nightly_scheduler_loop
  - GET /api/anomaly/events REST endpoint
  - Battery health in GET /api/ml/status response
  - Telegram anomaly alert categories (ALERT_ANOMALY_*)
affects: [dashboard, ha-mqtt-entities]

# Tech tracking
tech-stack:
  added: []
  patterns: [fire-and-forget anomaly check in coordinator loop, per-anomaly-type Telegram categories]

key-files:
  created: []
  modified:
    - backend/coordinator.py
    - backend/main.py
    - backend/api.py
    - backend/notifier.py
    - tests/test_anomaly_detector.py

key-decisions:
  - "Use send_alert(category, message) matching existing notifier API rather than plan's send(message, category=) signature"
  - "Anomaly category map as class-level dict on Coordinator for clean lookup"
  - "get_anomaly_detector dependency defined before get_ml_status to avoid forward reference"

patterns-established:
  - "Fire-and-forget anomaly check: _run_anomaly_check() swallows all exceptions at WARNING level"
  - "Per-anomaly-type Telegram categories enable distinct cooldown windows for different anomaly classes"

requirements-completed: [ANOM-08]

# Metrics
duration: 7min
completed: 2026-03-24
---

# Phase 18 Plan 02: Anomaly Detection Integration Summary

**AnomalyDetector wired into coordinator 5s loop, nightly scheduler, REST API (events + battery health), and Telegram alerts with per-type categories**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-24T00:16:24Z
- **Completed:** 2026-03-24T00:23:21Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Coordinator calls check_cycle() every 5s control cycle with fire-and-forget error handling
- Telegram notifications sent for warning/alert severity events using 4 anomaly-specific categories
- AnomalyDetector constructed in FastAPI lifespan with graceful degradation
- Nightly IsolationForest training scheduled in _nightly_scheduler_loop
- GET /api/anomaly/events returns recent anomaly events with limit parameter
- GET /api/ml/status extended with battery_health section from anomaly detector
- 4 new integration tests verifying both API endpoints

## Task Commits

Each task was committed atomically:

1. **Task 1: Coordinator + main.py + notifier wiring** - `414539b` (feat)
2. **Task 2: REST API endpoints for anomaly events and battery health** - `784db7c` (feat)

**Plan metadata:** pending

## Files Created/Modified
- `backend/coordinator.py` - Added _anomaly_detector field, set_anomaly_detector(), _run_anomaly_check() with Telegram notification dispatch
- `backend/main.py` - AnomalyDetector construction in lifespan, anomaly_detector param in _nightly_scheduler_loop, nightly training call
- `backend/api.py` - get_anomaly_detector dependency, GET /api/anomaly/events endpoint, battery_health in GET /api/ml/status
- `backend/notifier.py` - ALERT_ANOMALY_COMM, ALERT_ANOMALY_CONSUMPTION, ALERT_ANOMALY_SOC, ALERT_ANOMALY_EFFICIENCY constants
- `tests/test_anomaly_detector.py` - 4 API integration tests (events 200, events 503, ml/status with health, ml/status without)

## Decisions Made
- Used send_alert(category, message) to match existing notifier API signature rather than plan's send(message, category=) pattern
- Placed anomaly category map as class-level dict _ANOMALY_CATEGORY_MAP on Coordinator for clean lookup without per-call allocation
- Moved get_anomaly_detector dependency definition above get_ml_status endpoint to avoid Python forward reference

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Corrected notifier method signature**
- **Found during:** Task 1
- **Issue:** Plan specified `self._notifier.send(event.message, category=cat)` but actual TelegramNotifier uses `send_alert(category, message)` with positional args in different order
- **Fix:** Used `self._notifier.send_alert(cat, event.message)` matching real API
- **Files modified:** backend/coordinator.py
- **Verification:** Import and test suite pass
- **Committed in:** 414539b

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential correctness fix. Without matching the actual notifier API, anomaly notifications would fail at runtime.

## Issues Encountered
None.

## Known Stubs
None - all integration points are fully wired.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Anomaly detection fully operational in the running system
- Events flow from check_cycle() through coordinator to REST API and Telegram
- Ready for dashboard visualization (future phase)
- Ready for HA MQTT entity exposure (future phase)

---
*Phase: 18-anomaly-detection*
*Completed: 2026-03-24*
