---
type: context
id: planned-supportops-eval-003
authority: proposal
---

# SUPPORTOPS-EVAL-003 — Agent Architecture Research

**Story ID:** SUPPORTOPS-EVAL-003  
**Title:** Agent Architecture Research  
**Phase:** Phase 6 — Evaluation ([roadmap](../maps/roadmap-index.md))  
**Status:** PLANNED  
**Authority:** proposal

**Type:** Research Story

Not in `active.md`, not in `story-index.md`, no Missions created, no finalize command run.
Registered by SUPPORTOPS-BACKLOG-001 as a planning document only. Scope and acceptance
criteria are proposals until the owner activates this Story.

## Purpose
Produce a reasoned recommendation about the agent's architecture, without committing the project to any migration.

## Scope
1. Current agent architecture.
2. Comparison with LangGraph.
3. Applicability of MCP.
4. Component boundaries (Agent Core / Tools / State / Policy).
5. Decision on which parts must remain the project's own code and which could be delegated to an external framework or protocol, with reasons.

## Out of Scope
- Any migration to LangGraph or adoption of MCP. The research is not permission to migrate.
- Implementation work of any kind.

## Acceptance Criteria (proposed)
- [ ] Alternatives have been investigated.
- [ ] Advantages and costs are described.
- [ ] Component boundaries are defined.
- [ ] The parts that must remain own code are identified and justified.
- [ ] A reasoned recommendation is prepared.

## Proposed Missions
Plan only. No Missions exist in MONOLITH for this Story. A Mission does not complete the Story;
alpha.3 has no automatic Story↔Mission link.

| # | Mission | Intended outcome |
| --- | --- | --- |
| EVAL003-M01 | Current Architecture Review | describe the present architecture and its boundaries |
| EVAL003-M02 | LangGraph Comparison | compare against LangGraph |
| EVAL003-M03 | MCP Applicability | decide where MCP helps and where it adds only a layer |
| EVAL003-M04 | Architecture Decision Report | write the recommendation |

## Dependencies
- None required — the research can proceed at any time; findings are more useful after Phases 4–5 shape the architecture.

## Risks / Open Questions
- Placement: `todo-md-mapping.md` historically records Agent Architecture as backlog/research with no phase. The owner placed it in Phase 6; the roadmap Phase 6 scope now names it (clarified by SUPPORTOPS-BACKLOG-002).
- Research may recommend a migration; any follow-up needs its own owner-approved Story.

## Evidence Requirements
None exist yet. At execution time the following evidence is required before the Story can be
considered for finalization:
- Decision report with sources and compared options.
- Boundary diagram or table.
- Explicit statement of what was not evaluated.
- Finalization only via native `monolith finalize plan` / `apply` after owner approval.
