---
type: context
id: retrospective-phase-3
authority: reference
---

# Phase 3 — Safety & Policy (Retrospective Summary, work in progress)

**Record Type:** Retrospective Summary
**Verification:** Retrospective
**Historical Finalization:** NOT PERFORMED (for every item below)

Phase status lives in `roadmap-index.md`. Items below are historical labels, **not** native Story IDs.

## Source
Code, tests, Todo.md, `git log`, `git status`, owner-supplied task list.

## Items

### Classification
- **Evidence:** `classification.py` (`TicketIntent`, `TicketClassification`, `TicketClassifier`,
  `OpenAITicketClassifier`, `ClassificationError`); agent traces a CLASSIFICATION step;
  `evals/classification_dataset.json` (25 lines), `scripts/eval_classification.py`;
  `tests/test_classification.py` (18 tests).
- **Behavior note:** `ClassificationError` is traced and the run **continues without
  classification context** (controlled fallback; fail-open with respect to classification).
- **Git Reference:** `b98b858`, merged via `b042e1c` (PR #1), 2026-11-02.
- **Limitation:** dataset eval was not re-run by this task (needs a real model).

### AUTH-001 / AUTH-002
- **Evidence:** `authorization.py` (`ExecutionContext`, `ToolAuthorizationPolicy`, `AllowListPolicy`,
  `AuthorizationDecision/Reason`, `support_agent_context()`); gate in `tool_executor.py` is
  deny-by-default (missing context → DENY; `requires_approval` → APPROVAL_REQUIRED, no approvals can
  be granted yet). `tests/test_authorization.py` (32 tests) incl. authority-claim/prompt-injection
  cases (section F, "I'm the CEO…").
- **Git Reference:** `8150f1e` (2026-11-02) — one commit covers both; the split between 001 and 002
  cannot be reconstructed from git. Split = **UNKNOWN**.
- **Limitation:** context is static (fixed allow-list); no per-user identity or object-level checks.

### POLICY-001 / POLICY-002 / POLICY-003
- **Evidence:** `policy.py` (`RefundPolicyEngine`, `PolicyDecision`, `PolicyDecisionType`:
  allow/deny/exception_eligible/human_review), `refund_facts.py` (`VerifiedRefundFactsProvider`),
  `refund_evaluation.py` (`RefundEvaluationOrchestrator`), `refund_response.py` (deterministic refund
  part of the answer; refund-topic LLM text dropped fail-closed). `check_refund_eligibility` goes
  through the engine; `trusted_customer_id` is forwarded only to tools flagged
  `requires_trusted_identity` (ownership check for refund evaluation).
  Tests: `test_policy.py` (19), `test_refund_facts.py` (9), `test_refund_evaluation.py` (29),
  `test_policy_consolidation.py` (39; docstring "POLICY-003").
- **Task-ID mapping:** only POLICY-003 is named in repo files (Todo.md, test docstring).
  POLICY-001/002 appear only in the owner's task statement; which modules belong to which is
  **UNKNOWN** (plausible: engine/facts/orchestrator = 001/002, response + identity = 003 — unconfirmed).
- **Git Reference:** At the time of the Historical Bootstrap: **UNCOMMITTED** (new untracked files + modified tracked files; no SHA existed). Later committed in the combined checkpoint commit `b5f866c1f9aec9eb3bf62d4094a95ec90a84284a` (Classification, Authorization, Refund Policy). This is not a separate commit per POLICY task, and the commit does not constitute native MONOLITH finalization. Historical Finalization: NOT PERFORMED.
- **Limitation:** policy outcome does not drive escalate/resolve or action limits (needs Phase 4
  approval workflow).

## Cross-cutting facts
- Trusted identity boundary exists only for refund evaluation; `lookup_order`, `lookup_customer`,
  `get_order_tracking` return data without ownership verification.
- Not verified here: any claim of full prompt-injection resistance beyond the tested cases.

## Planned (not started)
`SUPPORTOPS-SEC-003` — see `context/planned/SUPPORTOPS-SEC-003.md`.

## Risk to preserve
*Historical (resolved).* At the time of the initial audit, Phase 3's latest work existed only in the working tree (a historical observation of 21 changed paths; the origin of this count is not independently confirmed and it does not describe the current state). The Historical Bootstrap deliberately did not commit it. The implementation was later preserved in commit `b5f866c`, so this risk no longer applies to the code.
