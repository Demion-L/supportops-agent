import json
from datetime import UTC, datetime

from openai import OpenAI
from pydantic import ValidationError

from .agent_state import AgentState
from .models import AgentRun, AgentStepType, SupportTicket
from .run_manager import AgentRunManager
from .tool_executor import TOOL_REGISTRY, ToolCall, ToolResult, execute_tool_call
from supportops_agent.tool_definitions import get_llm_tool_schemas


MAX_AGENT_STEPS = 10
MODEL_NAME = "gpt-4.1-mini"

class SupportAgent:
    def __init__(self, client: OpenAI):
        self.client = client
        self.run_manager: AgentRunManager | None = None
        self.state: AgentState | None = None

    @property
    def last_run(self) -> AgentRun | None:
        """Return the most recent agent run."""

        if self.run_manager is None:
            return None

        return self.run_manager.run    

    @staticmethod
    def get_function_calls(response) -> list:
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
                error_type="invalid_arguments",
            )

        return execute_tool_call(tool_call)

    def execute_function_calls(self, function_calls: list) -> list[dict[str, str]]:

        tool_outputs = []

        for item in function_calls:
            started_at = datetime.now(UTC)
            tool_result = self.execute_function_call(item)
            finished_at = datetime.now(UTC)
            duration_ms = (finished_at - started_at).total_seconds() * 1000

            try:
                arguments = json.loads(item.arguments)
            except json.JSONDecodeError:
                arguments = item.arguments

            self.run_manager.record(
                AgentStepType.TOOL_EXECUTION,
                input={
                    "call_id": item.call_id,
                    "tool": item.name,
                    "arguments": arguments,
                },
                output=tool_result.output,
                error=tool_result.error,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )

            tool_outputs.append(self.build_tool_output(tool_result))

        return tool_outputs

    @staticmethod
    def extract_token_usage(response) -> dict[str, int] | None:

        usage = getattr(response, "usage", None)

        if usage is None:
            return None

        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        }

    @staticmethod
    def build_tool_output(tool_result: ToolResult) -> dict[str, str]:
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
        # any error aborting the loop gets recorded as a failure step below
        self.run_manager = AgentRunManager(ticket.ticket_id)

        try:
            return self._run_agent_loop(ticket)
        except Exception as exc:
            self.run_manager.fail(str(exc))
            raise

    def _run_agent_loop(self, ticket: SupportTicket) -> str:
        """Orchestrate the loop; delegates the rest to dedicated helpers."""

        self.state = self._start(ticket)

        for _ in range(MAX_AGENT_STEPS):
            function_calls = self.get_function_calls(self.state.response)

            if not function_calls:
                return self._finish()

            self._advance(function_calls)

        raise RuntimeError(
            f"Agent exceeded maximum number of steps: {MAX_AGENT_STEPS}"
        )

    def _start(self, ticket: SupportTicket) -> AgentState:
        response = self._call_model(ticket.message)
        self._record_llm_step({"message": ticket.message}, response)

        return AgentState(ticket=ticket, response=response)

    def _advance(self, function_calls: list) -> None:
        tool_outputs = self.execute_function_calls(function_calls)

        self.state.response = self._call_model(
            tool_outputs,
            previous_response_id=self.state.response.id,
        )
        self.state.step_count += 1

        self._record_llm_step({"tool_outputs": tool_outputs}, self.state.response)

    def _finish(self) -> str:
        self.run_manager.record(
            AgentStepType.FINAL,
            output={"answer": self.state.response.output_text},
        )

        self.run_manager.complete()

        return self.state.response.output_text

    def _call_model(self, model_input, *, previous_response_id: str | None = None):
        kwargs = {"model": MODEL_NAME, "input": model_input}

        if previous_response_id is None:
            kwargs["tools"] = get_llm_tool_schemas()
        else:
            kwargs["previous_response_id"] = previous_response_id

        return self.client.responses.create(**kwargs)

    def _record_llm_step(self, input_payload: dict, response) -> None:
        self.run_manager.record(
            AgentStepType.LLM_CALL,
            input=input_payload,
            output={
                "response_id": response.id,
                "model": MODEL_NAME,
                "usage": self.extract_token_usage(response),
            },
        )