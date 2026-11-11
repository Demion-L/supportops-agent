---
type: context
id: planned-supportops-eval-001
authority: proposal
---

# SUPPORTOPS-EVAL-001 — Agent Evaluation Dataset

**Story ID:** SUPPORTOPS-EVAL-001  
**Title:** Agent Evaluation Dataset  
**Phase:** Phase 6 — Evaluation ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Describe expected agent behavior as data, so behavior can be measured rather than inspected by hand.

## Scope
1. Scenario taxonomy.
2. Evaluation dataset.
3. Expected agent behavior.
4. Expected tool selection.
5. Expected policy decisions.
6. Expected escalation decisions.

## Out of Scope
- The evaluation runner and scoring (EVAL-002).
- Changing the existing classification dataset (it is preserved as is).
- Security-specific authority-claim scenarios beyond what is shared with SEC-004.

## Acceptance Criteria (proposed)
- [ ] Expected outcomes of scenarios are defined.
- [ ] Normal and negative cases are covered.
- [ ] The dataset is suitable for automatic processing.
- [ ] The existing classification dataset is preserved.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| EVAL001-M01 | Scenario Taxonomy | classify scenarios |
| EVAL001-M02 | Dataset Construction | build the dataset |
| EVAL001-M03 | Expected Behavior Specification | specify expected tools, decisions, escalations |
| EVAL001-M04 | Dataset Quality Review | review coverage and quality |

## Dependencies
- [SUPPORTOPS-ACT-001](SUPPORTOPS-ACT-001.md) — resolve/escalate expectations need defined scenarios.
- [SUPPORTOPS-ACT-002](SUPPORTOPS-ACT-002.md) — escalation expectations.
- `evals/classification_dataset.json` — existing dataset that must remain intact.

## Risks / Open Questions
- Expected behavior for escalation cannot be finalized before Phase 4 defines it.
- Whether SEC-004 scenarios are stored in this dataset or separately is an owner decision.
- Todo marks a SupportOps scenario set and a dataset as partial (classification only); this Story completes them.

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Dataset file(s) with a documented schema.
- Coverage table per scenario category (normal and negative).
- Confirmation that the classification dataset is unchanged.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
