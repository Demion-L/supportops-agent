---
type: context
id: planned-supportops-sec-003
authority: proposal
---

# SUPPORTOPS-SEC-003 — Object-Level Authorization & Response Fail-Closed

**Status: ACTIVE (not finalized).** Activated by owner approval; recorded in `active.md`
(`## Current Story`). Not in `story-index.md`, no finalize command run. Phase: 3 — Safety & Policy.

## Objective
Make every tool that returns customer/order data verify that the object belongs to the trusted
ticket owner, and make failures in classification fail closed rather than degrade silently.

## Scope (proposed, from owner brief)
1. Ownership check for `lookup_order`.
2. Ownership check for `lookup_customer`.
3. Ownership check for `get_order_tracking`.
4. Confirm ownership is preserved in `check_refund_eligibility` (already uses `trusted_customer_id`
   via `requires_trusted_identity`; uncommitted at time of writing; since committed in `b5f866c`).
5. Fail-closed behavior on classification error (today: `ClassificationError` → trace + continue
   without classification context, `agent.py::_classify`).
6. Security regression tests.

## Open questions for the owner (to resolve before activation)
- "Response fail-closed": generalize `refund_response.py`'s refund-only filter, or only item 5?
- What does fail-closed mean for classification: abort run (FAILED), force escalation, or restrict
  the allow-list to no tools? (Phase 4 escalation does not exist yet.)
- On ownership mismatch: error type / `reason_code` and whether the response reveals existence.
- POLICY-001/002/003 and AUTH work were uncommitted at time of writing; they are now committed in `b5f866c`, so SEC-003 has a clean code baseline. (Resolved; the documentation changes remain for Commit 2.)

## Acceptance Criteria (proposed)
- [ ] For each of the 3 lookup tools: caller with a different `trusted_customer_id` gets a denial
      indistinguishable from "not found"; no data fields leak.
- [ ] Missing/None trusted identity → deny (fail-closed), never allow.
- [ ] Ownership cannot be influenced by model-supplied arguments or ticket text
      (authority-claim cases, IDEA-001 style).
- [ ] `check_refund_eligibility` ownership regression test present and passing.
- [ ] Classification error produces the owner-approved fail-closed outcome, traced as a step.
- [ ] Security regression tests added; full suite green (baseline 295 passed + new tests).
- [ ] No change to RefundPolicyEngine decisions or to the Authorization Gate contract
      unless the owner approves.
- [ ] Finalized via native `monolith finalize plan` / `apply` after owner approval of the digest.

## Out of scope
Escalation workflow, persistence, API, evaluation dataset (Phases 4–7).

## Proposed Missions
Plan only (added by SUPPORTOPS-BACKLOG-001). No Missions exist in MONOLITH for this Story. A Mission
does not complete the Story; alpha.3 has no automatic Story↔Mission link. The open questions above
remain unresolved owner decisions; nothing here approves any of them.

| # | Mission | Intended outcome |
| --- | --- | --- |
| SEC003-M01 | Security Architecture & Trust Boundaries | settle the open questions with the owner and document trust boundaries |
| SEC003-M02 | Object-Level Authorization | ownership checks for the three lookup tools |
| SEC003-M03 | Classification Fail-Closed | owner-approved fail-closed outcome for classification errors |
| SEC003-M04 | Security Regression & Review | regression tests and security review |

Follow-on Story: [SUPPORTOPS-SEC-004](SUPPORTOPS-SEC-004.md) (security evaluation; does not duplicate the criteria above).
