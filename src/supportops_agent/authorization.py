"""Tool Authorization Gate: deterministic, fail-closed, independent of the LLM.

Invariant: unauthorized action -> DENY -> the business handler is never called.

The gate decides from exactly two inputs, both owned by trusted application
code: the ``ToolDefinition`` (from the registry) and an ``ExecutionContext``.
It never looks at ticket text, classification output, developer messages or
tool-call arguments, so none of them can widen permissions.

Authorization (may this agent use this tool at all?) is kept separate from
approval (may this particular execution proceed?). There is no approval
channel yet, so every ``requires_approval`` tool is blocked until one exists.
"""

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from supportops_agent.tool_definitions import ToolDefinition

__all__ = [
    "AuthorizationDecision",
    "AuthorizationReason",
    "AuthorizationResult",
    "AllowListPolicy",
    "DEFAULT_POLICY",
    "ExecutionContext",
    "SUPPORT_AGENT_ALLOWED_TOOLS",
    "ToolAuthorizationPolicy",
    "support_agent_context",
]


class AuthorizationDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


class AuthorizationReason(StrEnum):
    """Stable reason codes reported in ToolResult and the execution trace."""

    ALLOWED = "ALLOWED"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    APPROVAL_MISSING = "APPROVAL_MISSING"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    POLICY_MISSING = "POLICY_MISSING"
    POLICY_ERROR = "POLICY_ERROR"
    POLICY_INVALID_RESULT = "POLICY_INVALID_RESULT"
    TOOL_UNKNOWN = "TOOL_UNKNOWN"


class AuthorizationResult(BaseModel):
    """Decision plus a machine-readable reason."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: AuthorizationDecision
    reason_code: AuthorizationReason


class ExecutionContext(BaseModel):
    """Trusted authority of the caller.

    Must be created by application code only. It is immutable and is never
    derived from model output, ticket text or tool-call arguments.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1)
    allowed_tools: frozenset[str]


class ToolAuthorizationPolicy(Protocol):
    def authorize(
        self, tool: ToolDefinition, context: ExecutionContext
    ) -> AuthorizationResult: ...


class AllowListPolicy:
    """Fixed allow-list policy.

    1. tool not in ``context.allowed_tools``      -> DENY
    2. tool requires approval                     -> APPROVAL_REQUIRED
       (the context carries no approvals, so none can ever be granted yet)
    3. otherwise                                  -> ALLOW

    A side effect alone does not require approval.
    """

    def authorize(
        self, tool: ToolDefinition, context: ExecutionContext
    ) -> AuthorizationResult:
        if tool.name not in context.allowed_tools:
            return AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason_code=AuthorizationReason.TOOL_NOT_ALLOWED,
            )

        if tool.requires_approval:
            return AuthorizationResult(
                decision=AuthorizationDecision.APPROVAL_REQUIRED,
                reason_code=AuthorizationReason.APPROVAL_MISSING,
            )

        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            reason_code=AuthorizationReason.ALLOWED,
        )


DEFAULT_POLICY: ToolAuthorizationPolicy = AllowListPolicy()

SUPPORT_AGENT_ID = "support_agent"

# Fixed configuration; no write/high-risk tools beyond case creation.
SUPPORT_AGENT_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "lookup_order",
        "lookup_customer",
        "get_order_tracking",
        "check_refund_eligibility",
        "create_support_case",
    }
)


def support_agent_context() -> ExecutionContext:
    """Trusted context of the Support Agent (call from application code)."""

    return ExecutionContext(
        agent_id=SUPPORT_AGENT_ID, allowed_tools=SUPPORT_AGENT_ALLOWED_TOOLS
    )
