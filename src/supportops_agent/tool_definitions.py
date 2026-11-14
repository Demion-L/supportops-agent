"""Tool metadata: the single source of truth for execution and LLM schemas."""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from supportops_agent.models import CasePriority
from supportops_agent.refund_evaluation import check_refund_eligibility
from supportops_agent.tools import (
    create_support_case,
    get_order_tracking,
    lookup_customer,
    lookup_order_for_tool,
)


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LookupOrderArguments(_Arguments):
    """Validated arguments for the lookup_order tool."""

    order_id: str = Field(min_length=1, max_length=100, description="The ID of the order to look up.")


class LookupCustomerArguments(_Arguments):
    """Validated arguments for the lookup_customer tool."""

    customer_id: str = Field(min_length=1, max_length=100, description="The ID of the customer to look up.")


class GetOrderTrackingArguments(_Arguments):
    """Validated arguments for the get_order_tracking tool."""

    order_id: str = Field(min_length=1, max_length=100, description="The ID of the order to track.")


class CheckRefundEligibilityArguments(_Arguments):
    """Validated arguments for the check_refund_eligibility tool."""

    order_id: str = Field(min_length=1, max_length=100, description="The ID of the order to check.")


class CreateSupportCaseArguments(_Arguments):
    """Validated arguments for the create_support_case tool."""

    reason: str = Field(min_length=1, max_length=1000, description="Why the case is being opened.")
    priority: CasePriority = Field(description="Case priority.")


@dataclass
class ToolDefinition:
    """Everything the system knows about a tool."""

    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: Callable[..., object]
    has_side_effects: bool = False
    requires_approval: bool = False
    # Handler получает `trusted_customer_id` от ToolExecutor (из доверенного
    # контекста приложения). В аргументах LLM такого поля нет и быть не может.
    requires_trusted_identity: bool = False
    # Handler получает `trusted_ticket_id` (тикет текущего прогона) от ToolExecutor.
    # В аргументах LLM такого поля нет.
    requires_trusted_ticket: bool = False

    def to_llm_schema(self) -> dict[str, object]:
        """Provider-neutral JSON-schema function spec (OpenAI Responses shape)."""

        parameters = self.arguments_model.model_json_schema()
        parameters.pop("title", None)
        for prop in parameters.get("properties", {}).values():
            prop.pop("title", None)

        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": parameters,
        }


_DEFINITIONS = [
    ToolDefinition(
        name="lookup_order",
        description="Look up an order by its order ID and return its current status and details.",
        arguments_model=LookupOrderArguments,
        handler=lookup_order_for_tool,
        requires_trusted_identity=True,
    ),
    ToolDefinition(
        name="lookup_customer",
        description="Look up a customer by their customer ID and return their details.",
        arguments_model=LookupCustomerArguments,
        handler=lookup_customer,
        requires_trusted_identity=True,
    ),
    ToolDefinition(
        name="get_order_tracking",
        description=(
            "Get shipment tracking (carrier, tracking number, shipment status) for an order. "
            "The result 'outcome' says whether tracking exists, the order is not shipped yet, "
            "has no shipment, or was not found."
        ),
        arguments_model=GetOrderTrackingArguments,
        handler=get_order_tracking,
        requires_trusted_identity=True,
    ),
    ToolDefinition(
        name="check_refund_eligibility",
        description=(
            "Assess whether the customer's own order meets the company refund policy "
            "(the decision comes from the policy engine; the customer identity is "
            "verified by the system, not by arguments). "
            "Read-only: it does NOT issue a refund and does NOT approve exceptions."
        ),
        arguments_model=CheckRefundEligibilityArguments,
        handler=check_refund_eligibility,
        requires_trusted_identity=True,
    ),
    ToolDefinition(
        name="create_support_case",
        description=(
            "Open an internal support case for the current ticket so a human agent can follow up. "
            "The ticket and customer are set by the system, not by arguments. "
            "Retrying with the same reason returns the existing case "
            "instead of creating a duplicate."
        ),
        arguments_model=CreateSupportCaseArguments,
        handler=create_support_case,
        has_side_effects=True,
        requires_trusted_identity=True,
        requires_trusted_ticket=True,
    ),
]

TOOL_REGISTRY: dict[str, ToolDefinition] = {d.name: d for d in _DEFINITIONS}


def get_llm_tool_schemas() -> list[dict[str, object]]:
    """LLM function schemas for every registered tool."""

    return [d.to_llm_schema() for d in TOOL_REGISTRY.values()]
