---
type: story-capsule
id: story-supportops-sec-003
phase: phase-3
title: "Object-Level Authorization & Response Fail-Closed"
date: 2026-11-15
status: COMPLETED
---

# STORY SUPPORTOPS-SEC-003 — Object-Level Authorization & Response Fail-Closed

## Objective
Make every tool returning customer/order data verify ownership by the trusted ticket owner, and make classification and runtime failures fail closed.

## Implementation Summary
Object-level authorization for lookup_order, lookup_customer, get_order_tracking (99090c0); classification fail-closed (a349d0e); trusted ticket binding (eb6eb53); exception sanitization (f7297d7); concurrent run isolation (5d3b46d); double-failure lifecycle hardening (eb42b97); metadata-only trace (5d1459c); final regression suite (78af152).

## Files Changed
- src/supportops_agent/agent.py
- src/supportops_agent/tools.py
- src/supportops_agent/tool_executor.py
- src/supportops_agent/tool_definitions.py
- src/supportops_agent/models.py
- tests/test_sec003_regression.py
- docs/security/sec003-final-handoff.md

## Tests
614 passed on main (78af152); 23 new final regression tests in tests/test_sec003_regression.py.

## Key Decisions
Identity comes only from the trusted ticket; foreign and missing objects are indistinguishable; only closed-set diagnostic codes leave the executor; trace is metadata-only; one active run per agent instance.

## ADRs Written
None

## Lessons Learned
Accepted residual risks LOW-1 (hostile evaluator status) and LOW-2 (non-dict tool output TypeError, tech debt) are recorded in docs/security/sec003-final-handoff.md.
