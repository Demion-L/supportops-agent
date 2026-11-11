---
type: context
id: planned-supportops-act-001
authority: proposal
---

# SUPPORTOPS-ACT-001 — Ticket Lifecycle & Decision Model

**Story ID:** SUPPORTOPS-ACT-001  
**Title:** Ticket Lifecycle & Decision Model  
**Phase:** Phase 4 — Actions & Escalation ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Define how a ticket moves between states and how policy decisions map to permitted actions, so resolve/escalate behavior is explicit rather than implied.

## Scope
1. Ticket lifecycle: states and allowed transitions.
2. Resolve / escalate decision rules (today the policy engine emits ALLOW / DENY / EXCEPTION_ELIGIBLE / HUMAN_REVIEW; agent-level scenarios are undefined).
3. Policy-to-action mapping: which actions each policy outcome permits.
4. Stop conditions: when the agent must stop and hand off.

## Out of Scope
- Executing actions or approvals (ACT-003).
- The human handoff implementation (ACT-002).
- Persisting ticket state (STATE-001).

## Acceptance Criteria (proposed)
- [ ] Ticket states and transitions are defined.
- [ ] Policy decisions are linked to permitted actions.
- [ ] Ambiguous cases are handled safely.
- [ ] Transitions are covered by tests.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| ACT001-M01 | Ticket Lifecycle Design | define states and transitions |
| ACT001-M02 | Resolve / Escalate Decision Rules | define when the agent resolves versus escalates |
| ACT001-M03 | Policy-to-Action Mapping | map each policy outcome to permitted actions |
| ACT001-M04 | Lifecycle Tests & Review | transition tests and review |

## Dependencies
- [SUPPORTOPS-SEC-003](SUPPORTOPS-SEC-003.md) — defines the fail-closed outcome that lifecycle rules must be consistent with (Phase 3).
- `RefundPolicyEngine` outcomes — existing `PolicyDecisionType` values are the input to the mapping.

## Risks / Open Questions
- SEC-003 open question on what classification fail-closed means (abort, force escalation, restrict tools) depends on this model, and vice versa.
- Todo wording "approval workflow needed" means full policy-to-action wiring may only complete with ACT-003.
- Ticket lifecycle is both a design topic (here) and a storage topic (STATE-001); keep design here.

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Lifecycle state diagram or table committed to the repo.
- Decision-rule table with the policy outcome ↔ action mapping.
- Test results for every defined transition and for ambiguous inputs.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
