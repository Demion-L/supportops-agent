---
type: context
id: retrospective-readme
authority: reference
---

# Retrospective Summaries (SUPPORTOPS-MONOLITH-001)

> **Historical reconstruction is not historical execution.**
> Everything in this directory was reconstructed on 2026-11-04 from code, tests,
> `Todo.md` and git history. None of it was produced by the MONOLITH Story Lifecycle.

## What these records are not

- Not story capsules (those live only in `memory/working/phases/<phase>/stories/`).
- Not execution history (`memory/long-term/execution-history.md` is intentionally untouched).
- No finalization proposal, digest, governance approval, integrity attestation or
  mission checkpoint exists for any of this work. `finalize plan/apply` was never run.
- Task IDs such as `AUTH-001` or `POLICY-003` are historical labels from commits/owner
  notes, **not** native MONOLITH Story IDs.

## Index

| Record | Scope |
| --- | --- |
| [phase-1-agent-core-and-tools.md](phase-1-agent-core-and-tools.md) | Phase 1 |
| [phase-2-observability-and-testing.md](phase-2-observability-and-testing.md) | Phase 2 |
| [phase-3-safety-and-policy.md](phase-3-safety-and-policy.md) | Phase 3 (Classification, AUTH-001/002, POLICY-001/002/003) |
| [todo-md-mapping.md](todo-md-mapping.md) | Todo.md → roadmap mapping (Historical Bootstrap did not modify `Todo.md`; pre-existing uncommitted changes were preserved) |

Phase goals, criteria and statuses live **only** in `context/maps/roadmap-index.md`.
These records hold evidence, not status.

## Owner Decisions

Separate from the historical retrospective records above.

- [SupportOps Demo Refund Policy v1](owner-decision-refund-policy-v1.md)
  - Owner-approved on 2026-11-04.
  - Documents the demo refund policy.
  - Not a historical story finalization or retrospective governance approval.

## Common fields

Every record carries: Record Type, Source, Verification (`Retrospective`), Evidence,
Known Limitations, Git Reference (`<SHA>` | `UNCOMMITTED` | `UNKNOWN`),
Historical Finalization (`NOT PERFORMED`).

Test baseline when written: `pytest` → **295 passed** (re-run on 2026-11-04, working tree
including uncommitted changes).
