"""Execution boundary for model-requested tool calls."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from supportops_agent.tools import lookup_order


class LookupOrderArguments(BaseModel):
    """Validated arguments for the lookup_order tool."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    order_id: str = Field(min_length=1, max_length=100)


class ToolCall(BaseModel):
    """Structured request to execute a tool."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    name: Literal["lookup_order"]
    arguments: LookupOrderArguments


class ToolResult(BaseModel):
    """Structured result returned after tool execution."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    name: Literal["lookup_order"]
    output: dict[str, object] | None


def execute_tool_call(call: ToolCall) -> ToolResult:
    """Execute a validated tool call and return its structured result."""

    order = lookup_order(call.arguments.order_id)

    return ToolResult(
        call_id=call.call_id,
        name=call.name,
        output=order.model_dump(mode="json") if order else None,
    )