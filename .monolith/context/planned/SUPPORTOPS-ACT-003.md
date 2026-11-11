---
type: context
id: planned-supportops-act-003
authority: proposal
---

# SUPPORTOPS-ACT-003 — Action Execution & Approval Workflow

**Story ID:** SUPPORTOPS-ACT-003  
**Title:** Action Execution & Approval Workflow  
**Phase:** Phase 4 — Actions & Escalation ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Separate proposing an action from executing it, and make sure side effects happen only after a valid approval that the model cannot grant itself.

## Scope
1. Action / execution layer.
2. Approval workflow (the Gate already returns `APPROVAL_REQUIRED`; no approval can currently be granted).
3. Separation of proposal and execution.
4. Prohibition of unauthorized side effects.

## Out of Scope
- Escalation flow (ACT-002).
- Audit trail contents and storage (ACT-004).
- Long-lived approvals that survive restarts (Phase 5).

## Acceptance Criteria (proposed)
- [ ] Sensitive actions require approval.
- [ ] The LLM cannot grant approval itself.
- [ ] Missing approval blocks execution.
- [ ] Erroneous and repeated requests are handled safely.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| ACT003-M01 | Action Execution Contract | define proposed versus executed action |
| ACT003-M02 | Approval State Machine | define approval states and who may transition them |
| ACT003-M03 | Guarded Action Executor | execute only approved actions |
| ACT003-M04 | Approval & Denial Tests | tests for approval, denial, replay and error paths |

## Dependencies
- [SUPPORTOPS-ACT-001](SUPPORTOPS-ACT-001.md) — policy-to-action mapping.
- [SUPPORTOPS-ACT-002](SUPPORTOPS-ACT-002.md) — escalation is one of the actions the layer must govern.
- Tool Authorization Gate — existing deny-by-default gate and `requires_approval` metadata.

## Risks / Open Questions
- Who is an approver and how they authenticate is undefined and may need API/identity work (Phase 7).
- Approvals that must outlive a process depend on STATE-001/STATE-002; this Story may only deliver an in-memory approval model.
- Idempotency of repeated requests interacts with STATE-002 (side-effect replay protection).

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Tests showing the model cannot self-approve.
- Tests showing blocked execution without approval and safe handling of duplicates.
- Contract document for the action and approval states.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
