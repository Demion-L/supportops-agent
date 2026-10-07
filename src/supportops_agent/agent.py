import json
from openai import OpenAI

from .models import AgentRun, AgentStep, SupportTicket
from .run_manager import AgentRunManager
from .tool_executor import ToolCall, ToolResult, execute_tool_call
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

    def execute_function_call(self, item) -> ToolResult:
        tool_call = ToolCall(
            call_id=item.call_id,
            name=item.name,
            arguments=json.loads(item.arguments),
        )

        return execute_tool_call(tool_call)

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
                step_type="llm_call",
                input={
                    "message": ticket.message,
                },
                output={
                    "response_id": response.id,
                },
            )
        )

        for step in range(MAX_AGENT_STEPS):
            function_calls = [
                item
                for item in response.output
                if item.type == "function_call"
            ]

            if not function_calls:
                self.run_manager.add_step(
                    AgentStep(
                        step_number=self.run_manager.next_step_number(),
                        step_type="final",
                        input=None,
                        output={
                            "answer": response.output_text,
                        },
                    )
                )

                self.run_manager.complete()

                return response.output_text


            tool_outputs = []

            for item in function_calls:

                tool_result = self.execute_function_call(item)

                self.run_manager.add_step(
                    AgentStep(
                        step_number=self.run_manager.next_step_number(),
                        step_type="tool_execution",
                        input={
                            "call_id": item.call_id,
                            "tool": item.name,
                            "arguments": json.loads(item.arguments),
                        },
                        output=tool_result.output,
                        error=tool_result.error,
                    )
                )

                tool_outputs.append(
                    self.build_tool_output(tool_result)
                )

            response = self.client.responses.create(
                model="gpt-4.1-mini",
                previous_response_id=response.id,
                input=tool_outputs,
            )

            self.run_manager.add_step(
                AgentStep(
                    step_number=self.run_manager.next_step_number(),
                    step_type="llm_call",
                    input={
                        "tool_outputs": tool_outputs,
                    },
                    output={
                        "response_id": response.id,
                    },
                )
            )


        self.run_manager.fail()

        raise RuntimeError(
            f"Agent exceeded maximum number of steps: {MAX_AGENT_STEPS}"
        )