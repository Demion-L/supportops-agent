---
type: memory
id: memory-long-term-tech-debt
category: long-term
authority: authoritative
---

# Technical Debt Registry

Tracks intentional technical debt to prevent accidental "fixing" of accepted trade-offs.

---

## Open Debt

| ID  | Description | Accepted | Rationale |
| --- | ----------- | -------- | --------- |
| TD-001 | `len(tool_result.output or {})` in agent.py may raise `TypeError` if a tool handler returns a non-dict output (SEC-003 LOW-2) | 2026-11-15 (owner) | Current handlers return dicts; fix when external/plugin handlers appear. Not fixed in WP4 (tests-only). |
| TD-002 | SEC-003 LOW-1: hostile evaluator status (needs a substituted trusted evaluator; fail-closed preserved) | 2026-11-15 (owner) | Exploit requires compromise of trusted code. |

---

## Accepted Debt

<!-- Debt that has been consciously accepted with a known trade-off. -->
<!-- Do NOT fix accepted debt without explicitly revisiting the decision. -->
