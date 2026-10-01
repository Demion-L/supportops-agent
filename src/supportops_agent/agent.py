from openai import OpenAI
import json
from .models import SupportTicket
from .tool_executor import ToolCall, execute_tool_call


LOOKUP_ORDER_TOOL = {
    "type": "function",
    "name": "lookup_order",
    "description": "Look up an order by its order ID and return its current status and details.",
    "parameters": {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "The ID of the order to look up.",
            }
        },
        "required": ["order_id"],
        "additionalProperties": False,
    },
}

LOOKUP_CUSTOMER_TOOL = {
    "type": "function",
    "name": "lookup_customer",
    "description": "Look up a customer by their customer ID and return their details.",
    "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {
                "type": "string",
                "description": "The ID of the customer to look up.",
            }
        },
        "required": ["customer_id"],
        "additionalProperties": False,
    },
}

MAX_AGENT_STEPS = 10

class SupportAgent:
    def __init__(self, client: OpenAI):
        self.client = client

    def execute_function_call(self, item) -> object:
        tool_call = ToolCall(
            call_id=item.call_id,
            name=item.name,
            arguments=json.loads(item.arguments),
        )

        return execute_tool_call(tool_call)

    def run(self, ticket: SupportTicket) -> str:
        response = self.client.responses.create(
            model="gpt-4.1-mini",
            input=ticket.message,
            tools=[LOOKUP_ORDER_TOOL, LOOKUP_CUSTOMER_TOOL],
        )

        for step in range(MAX_AGENT_STEPS):
            function_calls = [
                item
                for item in response.output
                if item.type == "function_call"
            ]

            if not function_calls:
                return response.output_text

            tool_outputs = []

            for item in function_calls:

                tool_result = self.execute_function_call(item)

                print("Tool result:", tool_result)

                tool_output = {
                    "type": "function_call_output",
                    "call_id": tool_result.call_id,
                    "output": (
                        str(tool_result.output)
                        if tool_result.error is None
                        else f"Tool execution error: {tool_result.error}"
                    ),
                }

                tool_outputs.append(tool_output)

            response = self.client.responses.create(
                model="gpt-4.1-mini",
                previous_response_id=response.id,
                input=tool_outputs,
            )

        raise RuntimeError(
            f"Agent exceeded maximum number of steps: {MAX_AGENT_STEPS}"
        )