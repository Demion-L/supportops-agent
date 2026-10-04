"""Execution boundary for model-requested tool calls."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from supportops_agent.tools import lookup_customer, lookup_order


TOOL_REGISTRY = {
    "lookup_order": lookup_order,
    "lookup_customer": lookup_customer,
}


class LookupOrderArguments(BaseModel):
    """Validated arguments for the lookup_order tool."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    order_id: str = Field(min_length=1, max_length=100)


class LookupCustomerArguments(BaseModel):
    """Validated arguments for the lookup_customer tool."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    customer_id: str = Field(min_length=1, max_length=100)


class ToolCall(BaseModel):
    """Structured request to execute a tool."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    name: Literal["lookup_order", "lookup_customer"]
    arguments: LookupOrderArguments | LookupCustomerArguments


class ToolResult(BaseModel):
    """Structured result returned after tool execution."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    name: Literal["lookup_order", "lookup_customer"]
    output: dict[str, object] | None = None
    error: str | None = None


def execute_tool_call(call: ToolCall) -> ToolResult:
    """Execute a validated tool call and return its structured result."""

    tool = TOOL_REGISTRY.get(call.name)

    if tool is None:
        raise ValueError(f"Unknown tool: {call.name}")
       
    arguments = call.arguments.model_dump()

    try:
        result = tool(**arguments)
    except Exception as exc:
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            error=f"Tool '{call.name}' failed: {exc}",
    )

    if hasattr(result, "model_dump"):
        output = result.model_dump(mode="json")
    else:
        output = result

    return ToolResult(
        call_id=call.call_id,
        name=call.name,
        output=output,
    )