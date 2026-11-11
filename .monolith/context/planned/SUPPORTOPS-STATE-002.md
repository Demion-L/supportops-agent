---
type: context
id: planned-supportops-state-002
authority: proposal
---

# SUPPORTOPS-STATE-002 — Run Recovery & Durable Execution

**Story ID:** SUPPORTOPS-STATE-002  
**Title:** Run Recovery & Durable Execution  
**Phase:** Phase 5 — State & Persistence ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Detect unfinished runs after a restart and resume them safely without repeating side effects.

## Scope
1. Recovery state model.
2. Execution checkpointing.
3. Run resume.
4. Protection against repeated side effects.

## Out of Scope
- Basic persistence of runs and traces (STATE-001).
- Building MONOLITH Durable State into the Python runtime (excluded by the roadmap).

## Acceptance Criteria (proposed)
- [ ] An unfinished run is detected after a restart.
- [ ] A run can be restored safely.
- [ ] Repeated execution of side effects is controlled.
- [ ] Crash and recovery scenarios are verified.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| STATE002-M01 | Recovery Architecture & Reuse Assessment | design recovery and assess reuse of MONOLITH Durable State |
| STATE002-M02 | Execution Checkpointing | checkpoint execution state |
| STATE002-M03 | Run Resume Mechanism | resume an unfinished run |
| STATE002-M04 | Crash & Recovery Tests | crash-injection and resume tests |

## Dependencies
- [SUPPORTOPS-STATE-001](SUPPORTOPS-STATE-001.md) — recovery requires persisted runs.
- [SUPPORTOPS-ACT-003](SUPPORTOPS-ACT-003.md) — side-effect control builds on the action layer.

## Risks / Open Questions
- Reuse of MONOLITH Durable State: before implementing recovery in the project, investigate whether it can be reused. Do not assume an internal MONOLITH component is a public SDK, and do not copy its implementation. Assessment is mandatory in M01; the outcome is not predetermined.
- The roadmap excludes building MONOLITH Durable State into the Python runtime; any reuse proposal must respect that.
- Exactly-once semantics for external side effects may be impossible; the achievable guarantee must be stated honestly.

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Written reuse assessment with sources (what MONOLITH Durable State is, whether it is public, decision and reasons).
- Crash-and-recover test results.
- Test showing no duplicated side effect after resume.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
