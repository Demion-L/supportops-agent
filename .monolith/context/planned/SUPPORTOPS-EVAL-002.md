---
type: context
id: planned-supportops-eval-002
authority: proposal
---

# SUPPORTOPS-EVAL-002 — Automated Agent Evaluation

**Story ID:** SUPPORTOPS-EVAL-002  
**Title:** Automated Agent Evaluation  
**Phase:** Phase 6 — Evaluation ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Run the dataset automatically, score behavior, and report regressions between versions.

## Scope
1. Evaluation runner.
2. Tool selection scoring.
3. Policy decision scoring.
4. Final decision scoring (the agent's resulting outcome for a scenario compared with the expected one).
5. Escalation evaluation (resolve versus escalate).
6. Regression reporting.

## Out of Scope
- Dataset creation (EVAL-001).
- CI/CD integration unless the owner adds it.

## Acceptance Criteria (proposed)
- [ ] Evaluation is reproducible.
- [ ] Tool selection errors are detected.
- [ ] Policy decision errors are detected.
- [ ] Final decision errors are detected.
- [ ] Resolve/escalate errors are detected.
- [ ] Results are comparable between versions.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| EVAL002-M01 | Evaluation Runner | run scenarios end to end |
| EVAL002-M02 | Tool & Decision Scoring | score tool choice and policy decisions |
| EVAL002-M03 | Escalation Evaluation | score resolve/escalate decisions |
| EVAL002-M04 | Regression Reporting | compare runs across versions |

## Dependencies
- [SUPPORTOPS-EVAL-001](SUPPORTOPS-EVAL-001.md) — dataset and expected behavior.
- [SUPPORTOPS-STATE-001](SUPPORTOPS-STATE-001.md) — stored traces make results comparable across versions (soft dependency).

## Risks / Open Questions
- Real-model runs are non-deterministic and cost money; reproducibility needs a stated strategy (recorded responses, fixed seeds, or tolerance).
- Which model versions are used for comparison is undecided.

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Two runs on the same inputs producing comparable output.
- A deliberately broken tool choice, policy decision, final decision and resolve/escalate decision each detected by the scorer.
- A regression report between two versions.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
