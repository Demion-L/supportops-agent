"""Execution boundary for model-requested tool calls.

Two kinds of outcome are kept apart:
  * business outcomes (e.g. order not found) are normal ``output`` values;
  * execution failures (bad arguments, handler crashed, non-serializable
    output) set ``error`` and ``error_type`` and leave ``output`` empty.

Every execution passes the Authorization Gate first (see ``authorization``).
The handler is reached only on an explicit ALLOW; anything else - missing
context, missing/failing policy, unknown tool, denied, approval required -
returns a structured error result and never invokes the handler.
"""

import json
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from supportops_agent.authorization import (
    DEFAULT_POLICY,
    AuthorizationDecision,
    AuthorizationReason,
    AuthorizationResult,
    ExecutionContext,
    ToolAuthorizationPolicy,
)
from supportops_agent.tool_definitions import TOOL_REGISTRY, ToolDefinition

__all__ = ["TOOL_REGISTRY", "ToolCall", "ToolResult", "execute_tool_call"]

logger = logging.getLogger(__name__)


class ToolCall(BaseModel):
    """Structured request to execute a tool (provider-independent)."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    name: str
    arguments: BaseModel

    @model_validator(mode="before")
    @classmethod
    def _validate_arguments(cls, data):
        """Validate the raw arguments dict against the tool's own model."""

        if not isinstance(data, dict):
            return data

        definition = TOOL_REGISTRY.get(data.get("name"))

        if definition is None:
            raise ValueError(f"Unknown tool: {data.get('name')!r}")

        arguments = data.get("arguments")

        if not isinstance(arguments, definition.arguments_model):
            arguments = definition.arguments_model.model_validate(arguments)

        return {**data, "arguments": arguments}


class ToolResult(BaseModel):
    """Structured, JSON-serializable result of a tool execution."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    name: str
    output: dict[str, object] | None = None
    error: str | None = None
    error_type: (
        Literal[
            "invalid_arguments",
            "execution_failed",
            "authorization_denied",
            "approval_required",
        ]
        | None
    ) = None
    reason_code: str | None = None
    # Только имя класса исключения политики; текст исключения не хранится.
    diagnostic_type: str | None = None


def _authorization_failure(
    call: ToolCall,
    decision: AuthorizationDecision,
    reason: str,
    diagnostic_type: str | None = None,
) -> ToolResult:
    """Structured refusal. Contains no arguments, business data or exception text."""

    if decision == AuthorizationDecision.APPROVAL_REQUIRED:
        error_type, text = "approval_required", "requires approval"
    else:
        error_type, text = "authorization_denied", "authorization denied"

    return ToolResult(
        call_id=call.call_id,
        name=call.name,
        error=f"Tool '{call.name}' {text} [{reason}]",
        error_type=error_type,
        reason_code=str(reason),
        diagnostic_type=diagnostic_type,
    )


def _authorize(
    definition: ToolDefinition | None,
    context: ExecutionContext | None,
    policy: ToolAuthorizationPolicy | None,
    call: ToolCall,
) -> ToolResult | None:
    """Return a refusal ToolResult, or None only on an explicit ALLOW."""

    deny = AuthorizationDecision.DENY

    if definition is None:
        return _authorization_failure(call, deny, AuthorizationReason.TOOL_UNKNOWN)

    if not isinstance(context, ExecutionContext):
        return _authorization_failure(call, deny, AuthorizationReason.MISSING_CONTEXT)

    if policy is None:
        return _authorization_failure(call, deny, AuthorizationReason.POLICY_MISSING)

    try:
        outcome = policy.authorize(definition, context)
    except Exception as exc:
        # Текст исключения может содержать секреты/внутренние адреса: он не
        # попадает ни в ToolResult (-> LLM), ни в trace. Остаётся только тип.
        diagnostic_type = type(exc).__name__
        logger.error(
            "Authorization policy failed: tool=%s diagnostic_type=%s",
            call.name,
            diagnostic_type,
        )
        return _authorization_failure(
            call, deny, AuthorizationReason.POLICY_ERROR, diagnostic_type
        )

    if not isinstance(outcome, AuthorizationResult):
        return _authorization_failure(
            call, deny, AuthorizationReason.POLICY_INVALID_RESULT
        )

    if outcome.decision == AuthorizationDecision.ALLOW:
        return None

    return _authorization_failure(call, outcome.decision, outcome.reason_code)


def execute_tool_call(
    call: ToolCall,
    context: ExecutionContext | None = None,
    policy: ToolAuthorizationPolicy | None = DEFAULT_POLICY,
    *,
    trusted_customer_id: str | None = None,
) -> ToolResult:
    """Authorize, then execute a validated tool call.

    ``context`` must come from trusted application code. Omitting it (or
    passing anything that is not an ``ExecutionContext``) is a DENY.

    ``trusted_customer_id`` is the application-verified identity of the ticket
    owner. It is forwarded only to tools flagged ``requires_trusted_identity``
    and is never read from ``call.arguments`` (the models forbid extra fields).
    """

    definition = TOOL_REGISTRY.get(call.name)

    refusal = _authorize(definition, context, policy, call)

    if refusal is not None:
        return refusal

    try:
        kwargs = call.arguments.model_dump()
        if definition.requires_trusted_identity:
            kwargs["trusted_customer_id"] = trusted_customer_id

        result = definition.handler(**kwargs)

        if isinstance(result, BaseModel):
            result = result.model_dump(mode="json")

        # Guarantees JSON-serializable output (fails on e.g. raw dates/sets).
        output = None if result is None else json.loads(json.dumps(result))
    except Exception as exc:
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            error=f"Tool '{call.name}' failed: {exc}",
            error_type="execution_failed",
        )

    return ToolResult(call_id=call.call_id, name=call.name, output=output)
