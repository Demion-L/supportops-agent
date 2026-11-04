---
type: context
id: roadmap-index
topology_role: root
authority: authoritative
---

# Roadmap Index — supportops-agent

Navigable index of project phases and their status. **Single source of truth for phase
goals, criteria and status.** Evidence for Phases 1–3 is in `context/retrospective/`
(reconstructed 2026-11-04; Retrospective — not native story execution, nothing finalized).

The seven-phase structure is the owner's; do not change count or order without agreement.

---

## Phases

### Phase 1 — Agent Core & Tools

**Status:** COMPLETED (retrospectively verified by code + tests; no native stories)
- Goal: working tool-calling agent loop with validated, registered tools.
- Scope: `SupportAgent`, `MAX_AGENT_STEPS`, `TOOL_REGISTRY`, `ToolExecutor`, Pydantic args, `ToolResult`.
- Acceptance: multi-step tool loop, step cap, invalid-args handling, tested without a real API.
- Evidence: [phase-1](../retrospective/phase-1-agent-core-and-tools.md).
- Limitations: mock data; no ownership checks.

### Phase 2 — Observability & Testing

**Status:** COMPLETED (retrospectively verified by code + tests; no native stories)
- Goal: debuggable runs and a reliable offline test harness.
- Scope: `AgentRun`/`AgentStep`, execution trace, timings, token usage, failure steps, pytest suite.
- Acceptance: trace separates debug data from the final answer; failures recorded; suite green.
- Evidence: [phase-2](../retrospective/phase-2-observability-and-testing.md); `pytest` 295 passed.
- Limitations: in-memory trace only; no cost aggregation.

### Phase 3 — Safety & Policy

**Status:** IN PROGRESS
- Goal: the agent cannot act beyond verified authority or policy.
- Scope: classification, tool authorization gate, refund policy engine, policy-aware response,
  object-level authorization, fail-closed behavior.
- Acceptance: deny-by-default gate; deterministic refund decisions; ownership enforced on all
  customer/order tools; classification and response failures fail closed; security regression tests.
- Evidence: [phase-3](../retrospective/phase-3-safety-and-policy.md). Historical (non-native) items:
  Classification, AUTH-001/002 (committed earlier), POLICY-001/002/003 (uncommitted at Historical Bootstrap; later committed within the combined checkpoint commit `b5f866c`, together with Classification, Authorization and Refund Policy — not as separate per-task commits).
- Next planned story: `SUPPORTOPS-SEC-003` — prepared, **not activated**
  ([scope](../planned/SUPPORTOPS-SEC-003.md)).
- Limitations: ownership only for refund evaluation; classification failure degrades open.

### Phase 4 — Actions & Escalation

**Status:** PLANNED
- Goal: controlled actions and human handoff.
- Scope: action lifecycle, human escalation, approval workflow, agent stop rules, audit trail.
- Acceptance: defined resolve/escalate scenarios; approvals gate side-effecting actions; audit trail.
- Evidence: none beyond groundwork (`create_support_case` tool, `APPROVAL_REQUIRED` gate outcome,
  `HUMAN_REVIEW` policy outcome). None of the listed mechanisms is implemented.

### Phase 5 — State & Persistence

**Status:** PLANNED
- Goal: runs and workflow state survive restarts.
- Scope: persisted agent runs, workflow state, recovery, idempotency, restart behavior.
- Dependency: a reliable long-lived approval workflow (Phase 4) requires saved/restorable state.
- Out of scope: MONOLITH Durable State is not to be built into the Python runtime.

### Phase 6 — Evaluation

**Status:** PLANNED (partial artifacts exist)
- Goal: measurable agent behavior and regression safety.
- Scope: eval dataset, resolve/escalate and security scenarios, regression and reliability evaluation.
- Existing partial work: classification dataset + script; authority-claim unit tests; IDEA-001.

### Phase 7 — API & Production

**Status:** PLANNED
- Goal: operable service. Scope: API layer, configuration, logging, security boundaries,
  operational readiness. The project is **not** production-ready.

---

## Current Focus

Active Phase: Phase 3 — Safety & Policy
Active Story: none

---

## Completion Summary

| Phase | Stories | Status |
| ----- | ------- | ------ |
| 1 | retrospective summary, 0 native stories | COMPLETED |
| 2 | retrospective summary, 0 native stories | COMPLETED |
| 3 | retrospective summary, 0 native stories | IN PROGRESS |
| 4 | — | PLANNED |
| 5 | — | PLANNED |
| 6 | — | PLANNED |
| 7 | — | PLANNED |
