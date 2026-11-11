---
type: context
id: planned-supportops-api-003
authority: proposal
---

# SUPPORTOPS-API-003 — Production Security & Deployment

**Story ID:** SUPPORTOPS-API-003  
**Title:** Production Security & Deployment  
**Phase:** Phase 7 — API & Production ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Establish the production trust model and a reproducible deployment, and check readiness before the project is called production-ready.

## Scope
1. Production threat model.
2. Authentication integration.
3. API authorization.
4. Deployment architecture.
5. Production readiness.

## Out of Scope
- Features of the agent itself.
- The API endpoints (API-001) and observability (API-002).

## Acceptance Criteria (proposed)
- [ ] Trusted identity comes from the authentication mechanism.
- [ ] The API does not reveal other customers' data.
- [ ] Secrets are protected.
- [ ] Deployment is reproducible.
- [ ] Production readiness has been reviewed.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| API003-M01 | Production Threat Model | model threats for the deployed service |
| API003-M02 | Authentication & API Authorization | integrate authentication and authorize API access |
| API003-M03 | Deployment Architecture | define a reproducible deployment |
| API003-M04 | Production Readiness Review | review readiness |

## Dependencies
- [SUPPORTOPS-API-001](SUPPORTOPS-API-001.md) — the service to secure.
- [SUPPORTOPS-SEC-003](SUPPORTOPS-SEC-003.md) — object-level authorization in the agent.
- [SUPPORTOPS-SEC-004](SUPPORTOPS-SEC-004.md) — security evaluation of policy boundaries.
- [SUPPORTOPS-API-002](SUPPORTOPS-API-002.md) — operational signals for readiness.

## Risks / Open Questions
- The authentication provider and hosting target are undecided.
- Todo includes "assemble a minimal production-ready architecture" under learning milestones; it is not treated as a deliverable of this Story.
- The project is currently not production-ready; this Story does not change that by being planned.

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Threat model document.
- Tests with authenticated callers for own and foreign data.
- Reproducible deployment run and a readiness review record.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
