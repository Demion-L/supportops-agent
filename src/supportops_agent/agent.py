import json
from openai import OpenAI
from pydantic import ValidationError

from .models import AgentRun, AgentStep, AgentStepType, SupportTicket
from .run_manager import AgentRunManager
from .tool_executor import TOOL_REGISTRY, ToolCall, ToolResult, execute_tool_call
from supportops_agent.tool_definitions import (
    LOOKUP_CUSTOMER_TOOL,
    LOOKUP_ORDER_TOOL,
)


MAX_AGENT_STEPS = 10

class SupportAgent:
    def __init__(self, client: OpenAI):
        self.client = client
        self.run_manager: AgentRunManager | None = None

    @property
    def last_run(self) -> AgentRun | None:
        """Return the most recent agent run."""

        if self.run_manager is None:
            return None

        return self.run_manager.run    

    def get_function_calls(self, response) -> list:
        """Extract function calls from an OpenAI response."""
        return [
            item for item in response.output
            if item.type == "function_call"
        ]

    def execute_function_call(self, item) -> ToolResult:

        try:
            tool_call = ToolCall(
                call_id=item.call_id,
                name=item.name,
                arguments=json.loads(item.arguments),
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            if item.name not in TOOL_REGISTRY:
                raise

            return ToolResult(
                call_id=item.call_id,
                name=item.name,
                error=f"Invalid arguments for tool '{item.name}': {exc}",
            )

        return execute_tool_call(tool_call)

    def execute_function_calls(self, function_calls: list) -> list[dict[str, str]]:

        tool_outputs = []

        for item in function_calls:
            tool_result = self.execute_function_call(item)

            try:
                arguments = json.loads(item.arguments)
            except json.JSONDecodeError:
                arguments = item.arguments

            self.run_manager.add_step(
                AgentStep(
                    step_number=self.run_manager.next_step_number(),
                    step_type=AgentStepType.TOOL_EXECUTION,
                    input={
                        "call_id": item.call_id,
                        "tool": item.name,
                        "arguments": arguments,
                    },
                    output=tool_result.output,
                    error=tool_result.error,
                )
            )

            tool_outputs.append(self.build_tool_output(tool_result))

        return tool_outputs

    def build_tool_output(self, tool_result: ToolResult) -> dict[str, str]:
        """Convert an internal ToolResult into the Responses API format."""

        output = (
            str(tool_result.output)
            if tool_result.error is None
            else f"Tool execution error: {tool_result.error}"
        )

        return {
            "type": "function_call_output",
            "call_id": tool_result.call_id,
            "output": output,
        }

    def run(self, ticket: SupportTicket) -> str:
        self.run_manager = AgentRunManager(ticket.ticket_id)

        response = self.client.responses.create(
            model="gpt-4.1-mini",
            input=ticket.message,
            tools=[LOOKUP_ORDER_TOOL, LOOKUP_CUSTOMER_TOOL],
        )

        self.run_manager.add_step(
            AgentStep(
                step_number=self.run_manager.next_step_number(),
                step_type=AgentStepType.LLM_CALL,
                input={
                    "message": ticket.message,
                },
                output={
                    "response_id": response.id,
                },
            )
        )

        for step in range(MAX_AGENT_STEPS):
            function_calls = self.get_function_calls(response)

            if not function_calls:
                self.run_manager.add_step(
                    AgentStep(
                        step_number=self.run_manager.next_step_number(),
                        step_type=AgentStepType.FINAL,
                        input=None,
                        output={
                            "answer": response.output_text,
                        },
                    )
                )

                self.run_manager.complete()

                return response.output_text

            tool_outputs = self.execute_function_calls(function_calls)

            response = self.client.responses.create(
                model="gpt-4.1-mini",
                previous_response_id=response.id,
                input=tool_outputs,
            )

            self.run_manager.add_step(
                AgentStep(
                    step_number=self.run_manager.next_step_number(),
                    step_type=AgentStepType.LLM_CALL,
                    input={
                        "tool_outputs": tool_outputs,
                    },
                    output={
                        "response_id": response.id,
                    },
                )
            )

        error_message = (
            f"Agent exceeded maximum number of steps: {MAX_AGENT_STEPS}"
        )

        self.run_manager.fail(error_message)

        raise RuntimeError(error_message)