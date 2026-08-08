# SupportOps Agent

SupportOps Agent is the first operational domain of a broader agentic command platform for coordinating enterprise workflows, tools, services, and human decisions.

The project begins with a focused customer-support agent capable of investigating requests, calling business tools, applying operational policies, and escalating cases when human judgment is required.

## Current Scope

The initial implementation focuses on support operations:

* ticket classification;
* customer and order investigation;
* policy-aware decision-making;
* tool calling;
* human escalation;
* execution tracking;
* behavior evaluation.

## Platform Foundation

The system is being built around:

* FastAPI for application-facing APIs;
* MCP for AI-native capability access;
* LangGraph for stateful workflow orchestration;
* an agent runtime for execution management;
* an evaluation harness for measurable behavioral quality.

## Long-Term Direction

The long-term direction is an AI-native operations control plane capable of coordinating multiple business domains across a large organization.

Support operations is the starting point. The same architectural foundation can later support finance, sales, compliance, logistics, internal operations, and executive decision workflows while preserving human oversight, policy enforcement, traceability, and measurable execution quality.

## Status

Early development.
