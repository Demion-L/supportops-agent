---
type: context
id: owner-decision-refund-policy-v1
authority: reference
---

# SupportOps Demo Refund Policy v1 — Owner Decision Record

**Record Type:** Owner decision note (outside the MONOLITH governance lifecycle)
**Not an ADR. Not a DEC record.** No `DEC-`/ADR identifier is assigned: `decisions.md` is
maintained by `finalize-story`, and no story exists for this decision.
**No** governance approval, integrity attestation, finalization record, mission checkpoint
or execution-history event accompanies this note.

- **Decision date:** 2026-11-04
- **Authority:** Explicit Owner Decision

## Context
The learning project uses a deterministic, demonstration refund policy. It is not a statement
of any real store's rules.

## Decision
A 40-day standard window plus 5 additional days in which an exception may be considered,
counted from the verified delivery date (`delivered_at`).

| Days since verified delivery | Result |
|---|---|
| 0–40 inclusive | `ALLOW` |
| 41–45 inclusive | `EXCEPTION_ELIGIBLE` |
| more than 45 | `DENY` |

The table describes time boundaries only. Other checks of the existing Policy Engine remain
(order status, ownership, verified facts, `HUMAN_REVIEW` when data is insufficient or inconsistent).

## Consequences
- The only canonical start of the window is `delivered_at`; `created_at` is not used.
- The LLM does not make the business decision.
- `ALLOW` means eligibility, not an executed refund. No financial operation is performed.
- `EXCEPTION_ELIGIBLE` means an exception may be considered, not that it is approved.
- The policy must not be used as real store rules.

## Implementation
Existing `RefundPolicyEngine` / `RefundPolicyConfig` in `src/supportops_agent/policy.py`
(`standard_window_days=40`, `grace_period_days=5`, `policy_version="refund-policy-v1"`).
The implementation was not changed by this record.

## Verification
By code reading and existing tests: `tests/test_policy.py` (boundaries 40/41/45/46, default
config 40/5) and `tests/test_business_tools.py` (tool boundaries through the engine).

## Historical note
The formal owner decision was recorded on 2026-11-04. It is not claimed that it was formally
approved at the time of POLICY-001; the values existed in the (uncommitted) implementation
before this record.
