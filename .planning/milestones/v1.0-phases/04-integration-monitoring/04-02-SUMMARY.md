---
phase: 04-integration-monitoring
plan: 02
subsystem: coordinator
tags: [influxdb, ha-mqtt, decision-logging, integration-health, graceful-degradation]

# Dependency graph
requires:
  - phase: 04-01
    provides: "InfluxDB writer per-system methods, HA MQTT extra_fields, DecisionEntry/IntegrationStatus models"
  - phase: 02-controller-coordinator
    provides: "Coordinator control loop with poll/execute pattern"
provides:
  - "Per-cycle InfluxDB writes (coordinator_state + per_system_metrics)"
  - "Per-cycle HA MQTT publish with Victron per-phase extra_fields"
  - "Decision ring buffer (maxlen=100) with change-detection logging"
  - "Integration health tracking per service (influxdb, ha_mqtt, evcc, telegram)"
  - "set_ha_mqtt_client() wired in main.py lifespan"
affects: [04-03, dashboard, api-health]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Fire-and-forget integration writes with per-service health tracking"
    - "Decision ring buffer with change-detection (role change, allocation shift, EVCC hold)"

key-files:
  created: []
  modified:
    - backend/coordinator.py
    - backend/main.py
    - tests/test_coordinator.py

key-decisions:
  - "Decision entries only on role changes, allocation shifts >300W, or EVCC hold -- not every cycle"
  - "EVCC hold logged as special hold_signal trigger before generic _check_and_log_decision"
  - "Integration health uses evcc_available attribute (not _connected) matching EvccMqttDriver public API"

patterns-established:
  - "Fire-and-forget: integration writes wrapped in try/except, failures logged at WARNING, never block control loop"
  - "Health tracking: per-service IntegrationStatus updated on success/failure of each integration call"

requirements-completed: [INT-01, INT-03, INT-04, INT-05, INT-06]

# Metrics
duration: 3min
completed: 2026-03-22
---

# Phase 04 Plan 02: Coordinator Integration Wiring Summary

**Per-cycle InfluxDB and HA MQTT calls with decision ring buffer, integration health tracking, and EVCC hold verification**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-22T11:57:41Z
- **Completed:** 2026-03-22T12:00:41Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 3

## Accomplishments
- Coordinator now calls write_coordinator_state and write_per_system_metrics on every 5s cycle
- Coordinator calls ha_mqtt_client.publish per cycle with Victron per-phase data in extra_fields
- Decision ring buffer (maxlen=100) logs entries only on role changes, allocation shifts, or EVCC hold
- Integration health tracked per service with available/last_error/last_seen fields
- main.py wires HA MQTT client into coordinator via set_ha_mqtt_client()
- All integration failures caught with WARNING log -- never block the control loop

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Failing tests for decision buffer, integration writes, health tracking** - `221c51b` (test)
2. **Task 1 GREEN: Wire InfluxDB and HA MQTT, add decision buffer and health tracking** - `426f114` (feat)

## Files Created/Modified
- `backend/coordinator.py` - Added _decisions deque, _integration_health dict, set_ha_mqtt_client(), get_decisions(), get_integration_health(), _check_and_log_decision(), _write_integrations(); wired all 5 exit points of _run_cycle
- `backend/main.py` - Added coordinator.set_ha_mqtt_client(ha_client) after HA MQTT client creation
- `tests/test_coordinator.py` - Added TestDecisionRingBuffer, TestIntegrationWrites, TestIntegrationHealth, TestSetHaMqttClient test classes (20 new tests)

## Decisions Made
- Decision entries only created on meaningful changes (role change, allocation shift >300W, EVCC hold) to avoid noise
- EVCC hold uses a dedicated hold_signal trigger logged before generic _check_and_log_decision to ensure it always fires
- Integration health checks EVCC via evcc_available (public attr) not _connected (private)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Coordinator fully wired with InfluxDB and HA MQTT integration
- Decision ring buffer ready for API exposure in Plan 03 (dashboard endpoints)
- Integration health ready for /api/health endpoint enhancement

---
*Phase: 04-integration-monitoring*
*Completed: 2026-03-22*
