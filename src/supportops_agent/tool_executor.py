"""Execution boundary for model-requested tool calls.

Two kinds of outcome are kept apart:
  * business outcomes (e.g. order not found) are normal ``output`` values;
  * execution failures (bad arguments, handler crashed, non-serializable
    output) set ``error`` and ``error_type`` and leave ``output`` empty.
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from supportops_agent.tool_definitions import TOOL_REGISTRY

__all__ = ["TOOL_REGISTRY", "ToolCall", "ToolResult", "execute_tool_call"]


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
    error_type: Literal["invalid_arguments", "execution_failed"] | None = None


def execute_tool_call(call: ToolCall) -> ToolResult:
    """Execute a validated tool call and return its structured result."""

    definition = TOOL_REGISTRY.get(call.name)

    if definition is None:
        raise ValueError(f"Unknown tool: {call.name}")

    try:
        result = definition.handler(**call.arguments.model_dump())

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
