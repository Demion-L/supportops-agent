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

class SupportAgent:
    def __init__(self, client: OpenAI):
        self.client = client

    def run(self, ticket: SupportTicket) -> str:
        response = self.client.responses.create(
            model="gpt-4.1-mini",
            input=ticket.message,
            tools=[LOOKUP_ORDER_TOOL],
        )

        for item in response.output:
            if item.type == "function_call":
                tool_call = ToolCall(
                    call_id=item.call_id,
                    name=item.name,
                    arguments=json.loads(item.arguments),
                )

                tool_result = execute_tool_call(tool_call)

                print("Tool result:", tool_result)

                response = self.client.responses.create(
                model="gpt-4.1-mini",
                previous_response_id=response.id,
                input=[
                    {
                        "type": "function_call_output",
                        "call_id": tool_result.call_id,
                        "output": str(tool_result.output),
                    }
                ],
            )

        return response.output_text