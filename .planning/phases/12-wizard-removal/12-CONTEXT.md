# Phase 12: Wizard Removal - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

Remove the setup wizard entirely. The Add-on options page becomes the sole configuration surface. Delete setup_api.py, setup_config.py, and SetupWizard.tsx. Remove the ems_config.json config layer from the lifespan startup path. Clean up frontend routing so /setup no longer exists.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — pure infrastructure phase. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

Key constraints from research:
- No config migration needed (user confirmed no existing wizard users)
- The ems_config.json layer in main.py lifespan should be removed entirely
- Frontend /setup route should redirect to dashboard or simply not exist
- setup_api.py probe endpoints may still be useful for health checks — evaluate during planning

</decisions>

<code_context>
## Existing Code Insights

### Files to Delete
- `backend/setup_api.py` — setup wizard API routes
- `backend/setup_config.py` — ems_config.json persistence layer
- `frontend/src/pages/SetupWizard.tsx` (or equivalent setup page component)

### Files to Modify
- `backend/main.py` — remove setup_config loading from lifespan, remove setup_api router mount
- `frontend/src/App.tsx` — remove /setup route and setup-related imports
- Any test files referencing setup wizard functionality

### Integration Points
- `main.py` lifespan currently loads config from 3 layers: env vars, ems_config.json (setup_config), options.json — reduce to 2 layers
- Frontend routing currently checks setup_complete flag to show wizard vs dashboard

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Delete wizard code, simplify config loading.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>
