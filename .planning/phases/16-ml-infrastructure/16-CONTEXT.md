# Phase 16: ML Infrastructure - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

All ML components have a reliable foundation for model persistence, feature extraction, and non-blocking training

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — pure infrastructure phase. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/consumption_forecaster.py` — existing sklearn-based forecaster (upgrade target for Phase 17)
- `backend/influx_reader.py` — InfluxDB query client
- `backend/ha_statistics_reader.py` — HA SQLite statistics reader
- `backend/config.py` — dataclass config pattern with `from_env()` classmethods

### Established Patterns
- Async-first architecture with FastAPI lifespan
- Fire-and-forget for optional integrations (InfluxDB, Telegram)
- `logging.getLogger(__name__)` throughout
- Dataclass models with type hints

### Integration Points
- `backend/main.py` — FastAPI lifespan for initialization
- `Dockerfile` — for OMP_NUM_THREADS environment variable
- `/config/ems_models/` — HA Add-on persistent storage path

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Refer to ROADMAP phase description and success criteria.

</specifics>

<deferred>
## Deferred Ideas

None — discuss phase skipped.

</deferred>
