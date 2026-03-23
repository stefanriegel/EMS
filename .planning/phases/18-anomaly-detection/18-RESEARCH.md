# Phase 18: Anomaly Detection - Research

**Researched:** 2026-03-24
**Domain:** Anomaly detection / time-series monitoring / sklearn IsolationForest
**Confidence:** HIGH

## Summary

Phase 18 adds observability-only anomaly detection across three domains: communication loss patterns, consumption spikes, and battery health drift. The architecture is constrained by CONTEXT.md decisions: a single `backend/anomaly_detector.py` with `AnomalyDetector` class, JSON persistence in `/config/ems_models/`, nightly IsolationForest training via `anyio.to_thread.run_sync()`, and lightweight per-cycle checks using pre-computed mean/std thresholds (no sklearn calls in the 5s loop).

The codebase already provides all necessary building blocks: `ControllerSnapshot` with `consecutive_failures` and `power_w` fields, `ModelStore` for joblib persistence, `TelegramNotifier` with per-category cooldowns, and the `_nightly_scheduler_loop` pattern for scheduling nightly training. The coordinator's `_loop()` method calls `_run_cycle()` then `_run_export_advisory()` -- the anomaly `check_cycle()` call fits naturally after the export advisory, following the same fire-and-forget pattern.

**Primary recommendation:** Build a single `AnomalyDetector` class with three internal detectors (comm, consumption, battery), injected into the coordinator and called after each control cycle. Nightly IsolationForest training hooks into the existing `_nightly_scheduler_loop`. API extends existing `/api/ml/status` endpoint with `battery_health` and adds a new `/api/anomaly/events` endpoint.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Store anomaly events in /config/ems_models/anomaly_events.json -- consistent with MAPE storage, survives restarts, no DB dependency
- Single `backend/anomaly_detector.py` with AnomalyDetector class managing all 3 detection types
- Pre-computed thresholds stored as simple floats after nightly IsolationForest training -- per-cycle checks are comparisons against mean +/- N*std, no sklearn calls in 5s loop
- AnomalyDetector called from orchestrator's 5s loop via `check_cycle()` method -- receives latest ControllerSnapshot, returns anomaly events if any
- Three severity tiers: info (logged only), warning (1 occurrence), alert (3+ within 24h) -- escalation prevents false-positive fatigue
- Cooldown per anomaly type: 1 hour for warnings, 4 hours for alerts -- prevents notification spam
- Telegram notification format: single-line summary with emoji severity indicator
- Event retention: last 500 events or 90 days, whichever is smaller
- Rolling 7-day baseline of charge/discharge rates per SoC band (0-20%, 20-50%, 50-80%, 80-100%)
- Round-trip efficiency: track energy in (charge kWh) vs energy out (discharge kWh) over 24h windows, flag if below 85%
- Minimum 14 days of data before any battery anomaly alerts
- Battery health metrics exposed via /api/ml/status response as a `battery_health` section

### Claude's Discretion
- IsolationForest hyperparameters (contamination, n_estimators)
- Exact threshold multipliers for anomaly detection (e.g., 2.5 sigma vs 3 sigma)
- Internal data structures for tracking event history and cooldowns
- Specific Modbus registers used for battery charge/discharge kWh tracking

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ANOM-01 | Communication loss pattern detection -- identify recurring driver timeout patterns | ControllerSnapshot.consecutive_failures field available; track patterns over sliding window |
| ANOM-02 | Consumption spike detection -- flag unusual consumption relative to time-of-day baseline | Derive consumption from power_w fields; maintain hourly baselines with rolling stats |
| ANOM-03 | Tiered alerts with confirmation periods -- warning after 1, alert after 3 within 24h | Implement escalation state machine in AnomalyDetector with per-type counters |
| ANOM-04 | SoC curve anomaly detection -- flag when charge/discharge curves deviate from learned profile | Track charge/discharge rates per SoC band (4 bands); compare against 7-day rolling baseline |
| ANOM-05 | Efficiency degradation tracking -- monitor round-trip efficiency trends over weeks | Track charge kWh vs discharge kWh in 24h windows; rolling efficiency with 14-day minimum |
| ANOM-06 | Nightly IsolationForest training on metrics for multi-dimensional anomaly scoring | Use existing ModelStore + anyio.to_thread.run_sync; train on consumption+SoC+power features |
| ANOM-07 | Per-cycle anomaly check uses pre-computed statistical thresholds only | Store mean/std from nightly training; check_cycle does float comparisons only |
| ANOM-08 | Anomaly events exposed via REST API and Telegram notifications | New /api/anomaly/events endpoint + extend /api/ml/status; TelegramNotifier for alerts |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| scikit-learn | 1.8.0 | IsolationForest for multi-dimensional anomaly scoring | Already in project deps; IsolationForest is the standard unsupervised anomaly detector |
| numpy | >=1.25 | Array operations for threshold computation and stats | Already in project deps |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| joblib | (bundled with sklearn) | Model persistence via ModelStore | Nightly IsolationForest model save/load |
| anyio | (existing dep) | `to_thread.run_sync` for non-blocking training | Nightly training in background executor |
| httpx | (existing dep) | Telegram notifications via TelegramNotifier | Alert delivery |

No new dependencies required. Everything builds on the existing stack.

## Architecture Patterns

### Recommended Project Structure
```
backend/
    anomaly_detector.py      # AnomalyDetector class (all 3 detection types)
    config.py                # +AnomalyDetectorConfig dataclass
    coordinator.py           # +anomaly_detector injection, check_cycle() in _loop()
    main.py                  # +AnomalyDetector construction in lifespan
    api.py                   # +/api/anomaly/events, extend /api/ml/status
    notifier.py              # +ALERT_ANOMALY_* category constants
tests/
    test_anomaly_detector.py # Unit tests for all 3 detection domains
```

### Pattern 1: Per-Cycle Check (Lightweight)
**What:** `check_cycle(h_snap, v_snap)` receives controller snapshots and returns anomaly events using only float comparisons.
**When to use:** Every 5s control cycle in the coordinator's `_loop()`.
**Example:**
```python
# In coordinator._loop():
async def _loop(self) -> None:
    while True:
        try:
            await self._run_cycle()
            await self._run_export_advisory()
            # Anomaly check -- fire-and-forget, never blocks loop
            if self._anomaly_detector is not None:
                try:
                    events = self._anomaly_detector.check_cycle(
                        self._last_h_snap, self._last_v_snap
                    )
                    for event in events:
                        await self._notify_anomaly(event)
                except Exception as exc:
                    logger.warning("anomaly check failed: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Coordinator cycle error: %s", exc, exc_info=True)
        await asyncio.sleep(self._cfg.loop_interval_s)
```

### Pattern 2: Nightly Training (Background Executor)
**What:** IsolationForest training runs in the existing `_nightly_scheduler_loop`, using `anyio.to_thread.run_sync()` to avoid blocking the event loop.
**When to use:** Once per night, after consumption forecaster retrain.
**Example:**
```python
# In _nightly_scheduler_loop (main.py):
if anomaly_detector is not None:
    try:
        await anomaly_detector.nightly_train()
        logger.info("nightly-scheduler: anomaly detector trained")
    except Exception as exc:
        logger.warning("nightly-scheduler: anomaly training failed: %s", exc)
```

### Pattern 3: Tiered Alert Escalation
**What:** State machine tracking occurrence count per anomaly type within a 24h sliding window.
**When to use:** Every time an anomaly is detected in `check_cycle()`.
**Example:**
```python
@dataclass
class AnomalyEvent:
    timestamp: str          # ISO 8601
    anomaly_type: str       # "comm_loss", "consumption_spike", "soc_curve", "efficiency"
    severity: str           # "info", "warning", "alert"
    message: str            # Human-readable description
    value: float            # Observed value
    threshold: float        # Expected threshold
    system: str | None      # "huawei", "victron", or None for consumption

class _EscalationTracker:
    """Tracks occurrences per anomaly type in a 24h window."""

    def __init__(self) -> None:
        self._events: dict[str, list[float]] = {}  # type -> [timestamps]

    def record(self, anomaly_type: str, now: float) -> str:
        """Record occurrence, return severity."""
        window = self._events.setdefault(anomaly_type, [])
        cutoff = now - 86400  # 24h
        window[:] = [t for t in window if t > cutoff]
        window.append(now)
        count = len(window)
        if count >= 3:
            return "alert"
        if count >= 1:
            return "warning"
        return "info"
```

### Pattern 4: JSON File Persistence (Existing Pattern)
**What:** Anomaly events and baselines persisted to `/config/ems_models/anomaly_events.json` and `/config/ems_models/anomaly_baselines.json`.
**When to use:** On each new event and after nightly training.
**Example:**
```python
# Follows existing MAPE history pattern from consumption_forecaster.py
def _save_events(self) -> None:
    data = [dataclasses.asdict(e) for e in self._events[-500:]]
    self._events_path.write_text(json.dumps(data, indent=2))

def _load_events(self) -> list[AnomalyEvent]:
    if not self._events_path.exists():
        return []
    try:
        raw = json.loads(self._events_path.read_text())
        return [AnomalyEvent(**e) for e in raw]
    except (json.JSONDecodeError, TypeError):
        return []
```

### Anti-Patterns to Avoid
- **sklearn predict() in the 5s loop:** IsolationForest.predict() is too slow for per-cycle checks. Pre-compute thresholds nightly, use float comparisons per cycle.
- **Auto-remediation on anomaly:** Per REQUIREMENTS.md "Anomaly-triggered control changes" is explicitly out of scope. Anomalies are observability only.
- **Global cooldowns:** Use per-anomaly-type cooldowns (1h warning, 4h alert) to prevent spam while allowing different types to fire independently.
- **Training on first startup:** Require 14+ days of data before battery anomalies and sufficient consumption history before consumption anomalies. Cold-start = no alerts, just logging.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Model persistence | Custom pickle/JSON | ModelStore (existing) | Handles sklearn version mismatch, atomic save, metadata sidecars |
| Telegram alerts | Custom HTTP | TelegramNotifier (existing) | Already has per-category cooldown, HTML formatting, error handling |
| Background ML training | Custom threading | anyio.to_thread.run_sync | Matches Phase 16/17 pattern, proper async/sync bridge |
| Anomaly scoring | Custom threshold-only | IsolationForest (nightly) | Catches multi-dimensional anomalies that single-threshold checks miss |

**Key insight:** The per-cycle check uses simple statistics (mean +/- N*std), but the nightly IsolationForest training provides multi-dimensional anomaly scoring that updates those thresholds. This two-tier approach keeps the 5s loop fast while still benefiting from ML.

## Common Pitfalls

### Pitfall 1: False Positives on Startup
**What goes wrong:** System generates anomaly alerts before establishing reliable baselines.
**Why it happens:** No historical data to compute meaningful thresholds on first boot.
**How to avoid:** Enforce minimum data requirements: 14 days for battery health (per CONTEXT.md), 7 days for consumption baselines, 48h for comm patterns. Return empty events from `check_cycle()` until sufficient data exists.
**Warning signs:** Anomaly alerts within first week of installation.

### Pitfall 2: Stale Baselines After Seasonal Change
**What goes wrong:** Summer baselines flag normal winter consumption as anomalous.
**Why it happens:** Rolling 7-day window may not capture seasonal transitions.
**How to avoid:** Use rolling windows that naturally adapt. The 7-day rolling baseline for battery health and time-of-day consumption baselines already handle this. Nightly IsolationForest retraining continuously adapts to recent patterns.
**Warning signs:** Spike in "consumption_spike" events at season transitions.

### Pitfall 3: Blocking the Control Loop
**What goes wrong:** Anomaly detection takes too long, delaying setpoint updates.
**Why it happens:** Complex calculations or file I/O in `check_cycle()`.
**How to avoid:** `check_cycle()` MUST be pure float comparisons with in-memory state. All I/O (file saves, Telegram sends) happens asynchronously after returning events. Budget: < 1ms per call.
**Warning signs:** Control cycle time exceeding 5s interval.

### Pitfall 4: JSON File Corruption on Crash
**What goes wrong:** Power loss during file write corrupts anomaly_events.json.
**Why it happens:** Non-atomic write (truncate then write).
**How to avoid:** Write to a temp file, then atomic rename. Or use the existing fire-and-forget pattern -- if load fails, start with empty state (matches ModelStore pattern of silent discard on corruption).
**Warning signs:** JSON parse errors on startup.

### Pitfall 5: Cooldown Drift with monotonic vs wall clock
**What goes wrong:** Cooldowns use `time.monotonic()` but events use ISO timestamps.
**Why it happens:** Mixing clock sources for different purposes.
**How to avoid:** Use `time.monotonic()` for cooldown tracking (same as TelegramNotifier), ISO timestamps only for event records. Never compare the two.
**Warning signs:** Events being suppressed for wrong durations.

## Code Examples

### IsolationForest Configuration (Recommended)
```python
# sklearn 1.8.0 IsolationForest -- verified on this system
from sklearn.ensemble import IsolationForest

# Nightly training
model = IsolationForest(
    n_estimators=100,        # default, good balance of accuracy vs speed
    contamination=0.05,      # expect ~5% of data points are anomalous
    max_samples=256,         # sub-sampling for aarch64 speed
    random_state=42,         # reproducible training
    n_jobs=1,                # single thread (OMP_NUM_THREADS=2 in Dockerfile)
)

# Train on recent data (e.g., last 30 days of hourly features)
# Features: [consumption_kw, soc_delta, power_w, hour_of_day, day_of_week]
model.fit(X_train)

# Extract thresholds from trained model (done nightly, stored as floats)
scores = model.decision_function(X_train)
threshold = float(numpy.percentile(scores, 5))  # 5th percentile = anomaly boundary
```

### Consumption Baseline Tracking
```python
@dataclass
class HourlyBaseline:
    """Per-hour-of-day consumption baseline with rolling stats."""
    mean: float = 0.0
    std: float = 0.0
    count: int = 0

    def update(self, value: float, alpha: float = 0.1) -> None:
        """Exponential moving average update."""
        if self.count == 0:
            self.mean = value
            self.std = 0.0
        else:
            delta = value - self.mean
            self.mean += alpha * delta
            self.std = math.sqrt((1 - alpha) * (self.std ** 2 + alpha * delta ** 2))
        self.count += 1
```

### Communication Loss Pattern Detection
```python
def _check_comm_loss(
    self,
    h_snap: ControllerSnapshot,
    v_snap: ControllerSnapshot,
) -> list[AnomalyEvent]:
    """Detect recurring driver timeout patterns."""
    events: list[AnomalyEvent] = []
    for name, snap in [("huawei", h_snap), ("victron", v_snap)]:
        if snap.consecutive_failures > 0:
            self._comm_failure_history[name].append(time.monotonic())
            # Check for recurring pattern: 3+ failure windows in last hour
            cutoff = time.monotonic() - 3600
            recent = [t for t in self._comm_failure_history[name] if t > cutoff]
            # Count distinct failure windows (gaps > 30s = new window)
            windows = self._count_windows(recent, gap_s=30.0)
            if windows >= 3:
                events.append(AnomalyEvent(
                    timestamp=datetime.now(tz=timezone.utc).isoformat(),
                    anomaly_type="comm_loss",
                    severity=self._escalation.record("comm_loss", time.monotonic()),
                    message=f"Recurring {name} communication failures: {windows} episodes in last hour",
                    value=float(windows),
                    threshold=3.0,
                    system=name,
                ))
    return events
```

### Battery Round-Trip Efficiency Tracking
```python
def _track_efficiency(
    self,
    h_snap: ControllerSnapshot,
    v_snap: ControllerSnapshot,
    dt_s: float,
) -> None:
    """Accumulate charge/discharge energy for efficiency computation."""
    for name, snap in [("huawei", h_snap), ("victron", v_snap)]:
        energy_kwh = abs(snap.power_w) * dt_s / 3_600_000  # W * s -> kWh
        if snap.power_w > 0:  # charging (positive = charge in coordinator convention)
            self._charge_kwh[name] += energy_kwh
        elif snap.power_w < 0:  # discharging
            self._discharge_kwh[name] += energy_kwh
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Fixed thresholds only | IsolationForest + statistical thresholds | Current best practice | Multi-dimensional anomaly scoring adapts to data |
| sklearn predict() per sample | Pre-computed thresholds from nightly training | Performance optimization | Keeps per-cycle cost at O(1) float comparisons |
| Single alert level | Tiered escalation (info/warning/alert) | Industry standard | Reduces alert fatigue while catching persistent issues |

## Open Questions

1. **Consumption derivation from snapshots**
   - What we know: `ControllerSnapshot.power_w` gives battery power. Grid power is available from Victron (`grid_power_w`).
   - What's unclear: Total household consumption = grid_import + battery_discharge + PV_direct. Not all terms are directly in snapshots.
   - Recommendation: Use `abs(h_snap.power_w) + abs(v_snap.power_w) + (v_snap.grid_power_w or 0)` as a proxy for system activity level. For true consumption anomaly detection, the nightly IsolationForest training can pull from HA statistics (same as ConsumptionForecaster).

2. **SoC band charge rate tracking granularity**
   - What we know: 4 bands defined (0-20%, 20-50%, 50-80%, 80-100%). SoC reported per cycle.
   - What's unclear: How quickly SoC changes between cycles (5s intervals may not show movement).
   - Recommendation: Track rate as delta_soc/delta_time when power_w != 0. Accumulate over 5-minute windows before updating baselines to smooth out noise.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + anyio |
| Config file | pyproject.toml (asyncio_mode = "auto") |
| Quick run command | `python -m pytest tests/test_anomaly_detector.py -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ANOM-01 | Comm loss pattern detection from consecutive_failures | unit | `python -m pytest tests/test_anomaly_detector.py::test_comm_loss_pattern -x` | Wave 0 |
| ANOM-02 | Consumption spike vs time-of-day baseline | unit | `python -m pytest tests/test_anomaly_detector.py::test_consumption_spike -x` | Wave 0 |
| ANOM-03 | Tiered alert escalation (1 -> warning, 3 -> alert) | unit | `python -m pytest tests/test_anomaly_detector.py::test_alert_escalation -x` | Wave 0 |
| ANOM-04 | SoC curve deviation detection | unit | `python -m pytest tests/test_anomaly_detector.py::test_soc_curve_anomaly -x` | Wave 0 |
| ANOM-05 | Round-trip efficiency degradation tracking | unit | `python -m pytest tests/test_anomaly_detector.py::test_efficiency_tracking -x` | Wave 0 |
| ANOM-06 | Nightly IsolationForest training in background executor | unit | `python -m pytest tests/test_anomaly_detector.py::test_nightly_train -x` | Wave 0 |
| ANOM-07 | Per-cycle check uses floats only (no sklearn) | unit | `python -m pytest tests/test_anomaly_detector.py::test_check_cycle_no_sklearn -x` | Wave 0 |
| ANOM-08 | REST API + Telegram notification integration | unit | `python -m pytest tests/test_anomaly_detector.py::test_api_events -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_anomaly_detector.py -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before verification

### Wave 0 Gaps
- [ ] `tests/test_anomaly_detector.py` -- covers ANOM-01 through ANOM-08
- No framework install needed (pytest already configured)

## Sources

### Primary (HIGH confidence)
- Codebase inspection: `backend/coordinator.py` -- `_loop()` method (lines 508-518), `_run_cycle()` (lines 520-745)
- Codebase inspection: `backend/controller_model.py` -- `ControllerSnapshot` with `consecutive_failures`, `power_w`, `soc_pct`
- Codebase inspection: `backend/model_store.py` -- ModelStore API for joblib persistence
- Codebase inspection: `backend/notifier.py` -- TelegramNotifier with per-category cooldown
- Codebase inspection: `backend/main.py` -- `_nightly_scheduler_loop` pattern (lines 101-176)
- Codebase inspection: `backend/consumption_forecaster.py` -- `retrain_if_stale()`, MAPE history JSON pattern
- Local verification: `sklearn.ensemble.IsolationForest` available in sklearn 1.8.0

### Secondary (MEDIUM confidence)
- sklearn IsolationForest defaults: n_estimators=100, contamination="auto", max_samples="auto"

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new deps, all verified locally
- Architecture: HIGH -- all integration points inspected in codebase
- Pitfalls: HIGH -- based on direct analysis of existing patterns (cooldowns, persistence, loop timing)

**Research date:** 2026-03-24
**Valid until:** 2026-04-24 (stable domain, no external API changes expected)
