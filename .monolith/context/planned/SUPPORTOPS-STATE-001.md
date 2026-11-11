---
type: context
id: planned-supportops-state-001
authority: proposal
---

# SUPPORTOPS-STATE-001 — Persistent Agent State

**Story ID:** SUPPORTOPS-STATE-001  
**Title:** Persistent Agent State  
**Phase:** Phase 5 — State & Persistence ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Make runs, steps, traces and ticket state outlive the process.

## Scope
1. Persistence layer definition.
2. Storage of `AgentRun` and `AgentStep`.
3. Execution trace storage.
4. Ticket state storage.

## Out of Scope
- Crash recovery and resume (STATE-002).
- Serving stored data through an API (API-001).
- Choosing a production database or deployment topology (API-003).

## Acceptance Criteria (proposed)
- [ ] `AgentRun` and `AgentStep` are saved.
- [ ] The trace is available after the process ends.
- [ ] Ticket state can be restored.
- [ ] Write errors are handled explicitly.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| STATE001-M01 | Persistence Architecture | decide storage approach and boundaries |
| STATE001-M02 | AgentRun & AgentStep Storage | persist runs and steps |
| STATE001-M03 | Trace & Ticket Storage | persist traces and ticket state |
| STATE001-M04 | Persistence Integration Tests | restart and write-failure tests |

## Dependencies
- [SUPPORTOPS-ACT-001](SUPPORTOPS-ACT-001.md) — ticket lifecycle defines what ticket state is.
- [SUPPORTOPS-ACT-004](SUPPORTOPS-ACT-004.md) — audit storage contract is implemented on top of this layer.

## Risks / Open Questions
- Storage technology is undecided (owner decision).
- Trace content may include customer data; retention and redaction rules are undefined.
- Todo lists "store execution state between steps" separately; it is treated as part of this Story and should be confirmed.

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Test that stops and restarts the process and reads back a run, steps and trace.
- Test of a failing write with the expected explicit error.
- Architecture note for the persistence choice.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
