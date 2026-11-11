---
type: context
id: planned-supportops-act-004
authority: proposal
---

# SUPPORTOPS-ACT-004 — Audit Trail & Action Accountability

**Story ID:** SUPPORTOPS-ACT-004  
**Title:** Audit Trail & Action Accountability  
**Phase:** Phase 4 — Actions & Escalation ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Make every significant action attributable: who or what proposed it, who approved or denied it, and in which run and ticket it happened.

## Scope
1. Audit event schema.
2. History of approvals and denials.
3. Linking actions to `AgentRun` and the ticket.
4. Storage contract for the audit trail.

## Out of Scope
- Implementing durable storage (STATE-001). This Story defines the contract only; persistence is not delivered here.
- Exposing audit data through an API (API-001).

## Acceptance Criteria (proposed)
- [ ] Significant actions produce audit events.
- [ ] Events are linked to the run and the ticket.
- [ ] Approval and denial are distinguishable.
- [ ] Integrity requirements for the log are defined.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| ACT004-M01 | Audit Event Schema | define event types and fields |
| ACT004-M02 | Action Audit Instrumentation | emit events from the action layer |
| ACT004-M03 | Audit Storage Contract | define the interface and integrity requirements, no durable backend |
| ACT004-M04 | Audit Integrity Tests | tests for event content, linkage and ordering |

## Dependencies
- [SUPPORTOPS-ACT-003](SUPPORTOPS-ACT-003.md) — action and approval events are what gets audited.
- None on STATE-001. Durable audit storage is a Phase 5 concern, implemented by SUPPORTOPS-STATE-001 on top of the contract defined here (STATE-001 depends on this Story, not the reverse); this Story must not be declared to implement persistence.
- Execution trace — existing `POLICY_DECISION` / `CLASSIFICATION` steps are partial groundwork.

## Risks / Open Questions
- Todo marks audit trail as partial (execution trace exists; no persistent or immutable log). This Story alone does not close the persistence gap.
- Retention, tamper-evidence and privacy of audit data are undecided.

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Schema document.
- Tests asserting event content and run/ticket linkage.
- Explicit statement in the Story result that persistence is deferred to Phase 5.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
