---
type: context
id: planned-supportops-api-004
authority: proposal
---

# SUPPORTOPS-API-004 — Service API Vertical Slice

**Story ID:** SUPPORTOPS-API-004  
**Title:** Service API Vertical Slice  
**Phase:** Phase 7 — API & Production ([roadmap](../maps/roadmap-index.md)); persistence part touches Phase 5  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created in MONOLITH, no finalize command run.
Registered as a planning document (owner permission, API-004 registration task). Scope and acceptance
criteria are proposals until the owner activates this Story. Activation does not depend on closing
SEC-003 (owner decision D-11, parallel development), but implementation of packages that call
`SupportAgent.run` waits for SEC-003 M04 integration (see Dependencies).

Design source: [`docs/design/api-003-product-track.md`](../../../docs/design/api-003-product-track.md)
(its working-package numbering WP-1…WP-9 is separate from this Story's Missions; mapping in §16 of that document).
Implementation package 1: [`docs/design/api-004-m01-wp1-api-skeleton.md`](../../../docs/design/api-004-m01-wp1-api-skeleton.md).

## Purpose
Turn the existing SupportAgent into a standalone backend-to-backend API service.
First product milestone: a working HTTP vertical slice with PostgreSQL and synthetic data.

## Scope
1. API authentication (API credentials with scopes).
2. Trusted tenant context (tenant derived only from the credential).
3. Ticket creation (`POST /v1/tickets`, idempotent).
4. PostgreSQL persistence (tickets, runs, credentials).
5. Execution worker.
6. Stored results.
7. HTTP retrieval (`GET /v1/tickets/{id}`).
8. Two LLM modes: Demo (fake LLM, fixtures, deterministic, no network) and Live (real LLM, synthetic tickets only, explicit configuration).

## Out of Scope
- Multi-agent orchestration, Agent Commons, FEMIDA, STC integration.
- Production CRM integrations, billing, Kubernetes, microservices.
- `GET /v1/runs/{id}/trace` (deferred to a separate package).
- Automatic replay of business effects; stable business idempotency beyond the MVP constraint below.
- Real customer data (requires a separate compliance review before any commercial launch).

## Owner Decisions Applied
- D-3 PostgreSQL. D-4 Synthetic Demo Tenant. D-5 FastAPI + Pydantic + PostgreSQL driver + ASGI server.
- D-6 MVP constraint: **at most one support case per ticket**. Temporary business constraint of the Demo MVP, not a platform-wide contract; a stable operation identifier is reserved for the future.
- D-8 synthetic data only. D-11 parallel Alpha/Beta work with strict file and worktree separation. D-12 Demo/Live LLM modes; tests run without an external LLM.

## Acceptance Criteria (proposed)
- [ ] `POST /v1/tickets` creates a ticket; a repeated POST with the same `Idempotency-Key` creates no duplicate.
- [ ] A worker processes the ticket and stores the result.
- [ ] `GET /v1/tickets/{id}` returns the stored result.
- [ ] Tenant is determined by the credential only; cross-tenant access is denied; unknown and foreign tickets are indistinguishable.
- [ ] Authentication precedes body validation; error responses are safe (no input echo, no exception text).
- [ ] Double execution is prevented; a failed run is reflected in the database; no automatic replay of business effects.
- [ ] All data is synthetic; the full test suite passes offline without an external LLM.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
there is no automatic Story↔Mission link. Mission records are created with `monolith mission start` at activation.

| # | Mission | Intended outcome | Acceptance |
| --- | --- | --- | --- |
| API004-M01 | API Foundation | FastAPI application, configuration, `/healthz`, `/readyz`, basic error contracts, Demo Mode | API starts locally; `/healthz` and `/readyz` work; no real LLM calls; tests pass without network |
| API004-M02 | Trusted Identity & Persistence | API credentials, tenant identity, credential scopes, PostgreSQL, tickets, runs, idempotent ticket creation | Tenant from credential; cross-tenant access denied; ticket stored; repeated POST creates no duplicate; unknown and foreign ticket indistinguishable |
| API004-M03 | Execution Integration | worker, execution lifecycle, SupportAgent integration, persisted result, basic failure handling | Ticket processed by worker; result stored; failed run recorded; double execution prevented; no automatic replay of business effects |
| API004-M04 | Vertical Slice Acceptance | end-to-end HTTP scenario, regression, security validation, demo documentation | POST → worker → GET works; data synthetic; cross-tenant denied; all tests pass |

## Dependencies
- [SUPPORTOPS-SEC-003](SUPPORTOPS-SEC-003.md) — M01/M02 live in a new package (`supportops_service`) and do not touch SEC-003 files. M03 (SupportAgent integration) starts after SEC-003 M04 WP4 is integrated into `main`. This Story does not modify SEC-003, Authorization Gate or RefundPolicyEngine.
- Relation to [API-001](SUPPORTOPS-API-001.md) (FastAPI layer, endpoints) and [API-003](SUPPORTOPS-API-003.md) (authentication, deployment): overlapping scope. The owner narrows them at activation; neither is renamed or closed by this registration.
- Relation to [STATE-001/002](SUPPORTOPS-STATE-001.md): persistence here is service-level (tickets, runs); agent-state persistence stays in STATE.
- New runtime dependencies (FastAPI, uvicorn, psycopg) need explicit installation approval at the start of M01.

## Risks / Open Questions
- Overlap with API-001/API-003 scope (owner to narrow at activation).
- `SupportAgent.run` returns `str` only; exposing a `degraded` flag needs verified access to `agent.last_run`.
- Blocking LLM call cannot be interrupted by a deadline; relies on client timeouts (not yet verified for the `openai` package).
- Case storage stays in `mock_data.CASES` (not durable) until a later stable-idempotency story.
- Compliance (GDPR, DPA, LLM-provider retention) blocks a real launch, not development.

## Evidence Requirements
None exist yet. Before the Story can be considered for finalization:
- HTTP-level integration tests (tenant isolation, idempotency, safe errors, concurrent claim).
- Evidence that the suite runs offline in Demo Mode.
- Demo documentation with a reproducible scenario on synthetic data.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
