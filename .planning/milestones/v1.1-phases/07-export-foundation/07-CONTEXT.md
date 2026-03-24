# Phase 7: Export Foundation - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

System can evaluate whether PV surplus should be exported or stored, based on economic analysis of fixed feed-in rate vs. future import costs. Feed-in rate is configurable. System never discharges battery to grid. Export decisions are logged with reasoning.

</domain>

<decisions>
## Implementation Decisions

### ExportAdvisor Design
- New `backend/export_advisor.py` module following advisory pattern (like Scheduler)
- ConsumptionForecaster injected at init via DI (same pattern as Scheduler)
- Forward-looking consumption reserve covers next 4-6 hours (evening peak coverage)
- Per-cycle advisory (every 5s control loop) — coordinator queries ExportAdvisor each cycle

### Feed-in Rate Configuration
- `SystemConfig.feed_in_rate_eur_kwh: float = 0.074` — single field in existing config
- Setup wizard Step 5 (Tariff Settings) — alongside Octopus/Modul3 rates
- Runtime override via `POST /api/config` (matches existing config update pattern)
- Validation: >= 0, warn if > 0.15 (suspiciously high for German feed-in)
- HA Add-on config.yaml and run.sh extended with feed_in_rate field

### Decision Logging
- Add `EXPORT` and `SELF_CONSUME` decision types to DecisionEntry
- Log only on state changes (STORE->EXPORT or EXPORT->STORE) — matches existing "role change" pattern
- Reasoning includes: feed-in rate, current import rate, forecast demand next 4h, battery SoC
- Use existing `/api/decisions` endpoint — no new routes needed

### Claude's Discretion
- Internal algorithm design for forward-looking reserve calculation
- Test structure and coverage approach
- Error handling when ConsumptionForecaster is unavailable (graceful degradation)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `SystemConfig` already has `huawei_feed_in_allowed: bool` and `victron_feed_in_allowed: bool` (backend/config.py)
- `DecisionEntry` dataclass with `reasoning: str` field (backend/controller_model.py)
- `ConsumptionForecaster` with ML predictions (backend/consumption_forecaster.py)
- `CompositeTariffEngine` provides current import rate at any instant (backend/tariff.py)
- Setup wizard Step 5 already has Octopus + Modul3 tariff fields (frontend/src/pages/SetupWizard.tsx)

### Established Patterns
- Advisory classes injected into Coordinator (Scheduler pattern)
- Decision ring buffer in Coordinator (backend/orchestrator.py)
- Config fields added to SystemConfig + EmsSetupConfig + SetupCompleteRequest + config.yaml + run.sh + translations
- Fire-and-forget pattern: advisory failures logged at WARNING, never block control loop

### Integration Points
- Coordinator._run_cycle() — ExportAdvisor queried between grid-charge check and P_target computation
- SystemConfig.from_env() — new FEED_IN_RATE_EUR_KWH env var
- EmsSetupConfig — new feed_in_rate field
- HA Add-on config.yaml/run.sh — new option and export

</code_context>

<specifics>
## Specific Ideas

- Feed-in rate is 7.4 ct/kWh (0.074 EUR/kWh) — fixed, not time-varying
- Never discharge battery to grid — export only from direct PV surplus when batteries full
- Winter critical: having too little in battery is worse than exporting surplus
- Summer: full battery is fine, natural PV export acceptable

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>
