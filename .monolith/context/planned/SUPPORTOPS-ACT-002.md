---
type: context
id: planned-supportops-act-002
authority: proposal
---

# SUPPORTOPS-ACT-002 — Human Escalation Workflow

**Story ID:** SUPPORTOPS-ACT-002  
**Title:** Human Escalation Workflow  
**Phase:** Phase 4 — Actions & Escalation ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Let the agent hand a ticket to a human with preserved context, and guarantee it stops acting afterwards.

## Scope
1. Escalation flow built on the lifecycle and decision rules from ACT-001.
2. Use of the existing `create_support_case` tool.
3. Human handoff: what context is passed and how.
4. Stop conditions after handoff.

## Out of Scope
- Lifecycle and decision-rule design (ACT-001).
- Approval of side-effecting actions (ACT-003).
- Durable storage of escalated cases (STATE-001).

## Acceptance Criteria (proposed)
- [ ] The agent can hand a ticket to a human.
- [ ] Handoff context is preserved.
- [ ] Forbidden actions are not executed after handoff.
- [ ] Escalation errors are handled explicitly.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| ACT002-M01 | Escalation Contract | define the handoff payload and failure semantics |
| ACT002-M02 | Human Handoff Implementation | implement the flow on `create_support_case` |
| ACT002-M03 | Escalation Stop Conditions | enforce no further actions after handoff |
| ACT002-M04 | Escalation Integration Tests | integration tests including failure paths |

## Dependencies
- [SUPPORTOPS-ACT-001](SUPPORTOPS-ACT-001.md) — lifecycle states and stop rules.
- `create_support_case` — existing tool (groundwork only; there is no escalation flow today).

## Risks / Open Questions
- Today `create_support_case` is a tool, not a flow; the case system it talks to is mock data.
- What happens to the run when the escalation call itself fails (retry, fail closed, alert) needs an owner decision.

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Integration test showing handoff payload content.
- Test showing blocked actions after handoff.
- Test showing explicit handling of an escalation failure.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
