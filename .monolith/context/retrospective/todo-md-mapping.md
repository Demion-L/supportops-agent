---
type: context
id: retrospective-todo-mapping
authority: reference
---

# Todo.md → MONOLITH Mapping

**Record Type:** Retrospective mapping (analysis only). Historical Bootstrap did not modify
`Todo.md`. Pre-existing uncommitted changes were preserved. `Todo.md` remains
the backlog/technical-notes source. Phase numbers refer to `roadmap-index.md`.
Verified against Todo.md as of 2026-11-04 (file is itself modified/uncommitted).

| Todo.md section | Destination | Context in MONOLITH? | Remains only in Todo | Safe to shrink later? |
| --- | --- | --- | --- | --- |
| Agent Core | Phase 1 | Retrospective summary | Per-item `[x]` list | Yes, once Phase 1 is natively verified |
| Tools | Phase 1 (+ AUTH items → Phase 3) | Retrospective summaries | Item granularity | Yes, same condition |
| Testing | Phase 2 | Retrospective summary | Item granularity | Yes |
| Agent Observability | Phase 2 | Retrospective summary | Item granularity | Yes |
| SupportOps Logic — classification, policy, response | Phase 3 | Retrospective summary | Details of `[~]` notes | After Phase 3 stories are finalized |
| SupportOps Logic — ownership checks | Phase 3 → planned SUPPORTOPS-SEC-003 | Scope prepared | Original wording | After SEC-003 finalized |
| SupportOps Logic — resolve/escalate, human escalation, action layer, audit trail, stop rules | Phase 4 | Phase goal only | All detail | No (no stories yet) |
| State & Persistence | Phase 5 | Phase goal only | All detail | No |
| Evaluation | Phase 6 | Phase goal only; IDEA-001 seed | All detail | No |
| API & Production | Phase 7 | Phase goal only | All detail | No |
| Agent Architecture (LangGraph, MCP, boundaries) | **Backlog / research** — no phase | No | Everything | No (not in roadmap) |
| Learning / Architecture Milestones | **Personal learning log** — no phase | No | Everything | No |

## Contradictions and gaps found (none auto-fixed)

1. **Resolved.** At the time of the Historical Bootstrap, the Todo "policy / decision layer" line
   said the engine "only writes to trace, does not affect tools"; the next line and the code
   (`check_refund_eligibility` via `RefundPolicyEngine`, `refund_response.py`) showed it did, so the
   Todo line was partially stale. The implementation was later committed in `b5f866c`, and `Todo.md`
   was synchronized with the actual behavior: policy evaluation is written to the trace, passed into
   the model context, and used to build the deterministic refund part of the answer. The decision is
   made by the Policy Engine, not the LLM. The historical contradiction no longer applies.
2. Todo "Security eval: authority-claim (IDEA-001)" is `[ ]`, yet `tests/test_authorization.py`
   section F already tests authority claims at unit level. The dataset-level eval is what is missing.
3. SEC-003 items **fail-closed on classification error** and **response fail-closed** have no Todo
   entry (only "ownership" does). Note `refund_response.py` already applies a refund-topic fail-closed
   filter; scope overlap must be settled by the owner before SEC-003 is activated.
4. POLICY-001/002 IDs do not appear in Todo.md; only POLICY-003 and AUTH-001/002 do.

## Historical note (added by SUPPORTOPS-BACKLOG-002)

At the time of the Historical Bootstrap, Agent Architecture research was not attached to any Phase
(see the table above, which is left unchanged). In SUPPORTOPS-BACKLOG-001 it is planned as
SUPPORTOPS-EVAL-003 in Phase 6.

## Not created
No new IDEA records were created. Candidates the owner may later capture with `monolith idea add`:
LangGraph/MCP evaluation (Agent Architecture), cost tracking from token usage.
