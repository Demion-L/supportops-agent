---
type: context
id: planned-supportops-api-001
authority: proposal
---

# SUPPORTOPS-API-001 — FastAPI Service Layer

**Story ID:** SUPPORTOPS-API-001  
**Title:** FastAPI Service Layer  
**Phase:** Phase 7 — API & Production ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Expose the agent as a service without weakening its existing security boundaries.

## Scope
1. FastAPI application.
2. Ticket endpoint.
3. Agent run endpoint.
4. Trace endpoint.
5. API error handling.

## Out of Scope
- Authentication integration and deployment (API-003).
- Configuration, logging and metrics (API-002).

## Acceptance Criteria (proposed)
- [ ] The API provides the required operations.
- [ ] Input is validated.
- [ ] Errors have a stable contract.
- [ ] The API does not bypass existing security boundaries.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| API001-M01 | API Contract | define endpoints, schemas, error contract |
| API001-M02 | Ticket & Run Endpoints | implement ticket and run endpoints |
| API001-M03 | Trace Endpoint | implement trace retrieval |
| API001-M04 | API Integration Tests | tests including security-boundary cases |

## Dependencies
- [SUPPORTOPS-STATE-001](SUPPORTOPS-STATE-001.md) — run and trace endpoints need persisted data.
- [SUPPORTOPS-SEC-003](SUPPORTOPS-SEC-003.md) — trusted identity and object-level authorization must exist before data is exposed.
- Soft / contract only: SUPPORTOPS-API-003 supplies real authentication. This Story defines only the contract that the trusted identity is injected by the caller side; it is not blocked by API-003, and without API-003 the endpoint cannot be treated as production-safe. The hard dependency runs one way: API-003 depends on API-001.

## Risks / Open Questions
- Exposing traces can leak customer data; access rules are undefined.
- Without API-003 the trusted customer identity has no real source.
- A new dependency (FastAPI) requires owner approval when the Story starts.

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Integration tests via the HTTP layer.
- Tests showing cross-customer access is rejected through the API.
- Error contract document.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
