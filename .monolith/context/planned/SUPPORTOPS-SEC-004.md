---
type: context
id: planned-supportops-sec-004
authority: proposal
---

# SUPPORTOPS-SEC-004 — Security Evaluation & Policy Boundary Hardening

**Story ID:** SUPPORTOPS-SEC-004  
**Title:** Security Evaluation & Policy Boundary Hardening  
**Phase:** Phase 3 — Safety & Policy ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Prove, with negative security scenarios, that authority claims and classification output cannot change what the agent is permitted to do, and that protected operations cannot be reached by a side path.

## Scope
1. Authority-claim scenarios from IDEA-001 ("I am the CEO", "support lead approved this", and similar), covering ticket text and model-supplied arguments.
2. Verification that Authorization Gate decisions are independent of the classification result.
3. Regression checks for policy boundaries (Gate, RefundPolicyEngine, trusted identity).
4. Search for bypass paths to protected operations (tools with `requires_approval`, `has_side_effects`, `requires_trusted_identity`).

## Out of Scope
- Object-level authorization and classification fail-closed behavior (SEC-003; not duplicated here).
- The general agent evaluation dataset and runner (EVAL-001, EVAL-002).
- New authorization mechanisms; this Story verifies and hardens the existing boundary.

## Acceptance Criteria (proposed)
- [ ] Authority claims do not change the agent's authority.
- [ ] Authorization decisions do not depend on classification.
- [ ] Protected operations do not bypass mandatory checks.
- [ ] Negative security scenarios are covered by tests.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| SEC004-M01 | Authority-Claim Threat Scenarios | enumerate and document the authority-claim and bypass scenarios (IDEA-001 as seed) |
| SEC004-M02 | Authorization Boundary Tests | tests proving Gate decisions ignore claims and classification |
| SEC004-M03 | Policy Boundary Regression | regression tests for Gate, policy engine and trusted-identity boundaries |
| SEC004-M04 | Security Evidence & Review | collect evidence and run the security review |

## Dependencies
- [SUPPORTOPS-SEC-003](SUPPORTOPS-SEC-003.md) — object-level authorization and fail-closed classification should land first so boundary tests target the final behavior.
- IDEA-001 — parked idea `Security eval: authority-claim requests` (`monolith idea show IDEA-001`).
- `tests/test_authorization.py` section F — existing unit-level authority-claim cases; this Story must extend, not duplicate, them.

## Risks / Open Questions
- Overlap with SEC-003 acceptance criteria: boundary of ownership checks (SEC-003) versus claim/bypass scenarios (SEC-004) must be kept explicit.
- Overlap with EVAL-001/EVAL-002: whether authority-claim scenarios also live in the evaluation dataset is an owner decision.
- Dataset-level evaluation needs a real model; tests in this Story must run offline.

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Scenario catalogue with expected outcomes.
- Test run showing the new negative tests failing without the control and passing with it.
- Review note listing bypass paths examined.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
