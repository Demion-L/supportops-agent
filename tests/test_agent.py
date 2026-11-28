from supportops_agent.runtime.agent import SupportAgent
from supportops_agent.tools.authorization import support_agent_context
from supportops_agent.models import SupportTicket, AgentRunStatus, AgentStepType
from supportops_agent.tools.executor import ToolResult
import pytest
from pydantic import ValidationError
from supportops_agent.runtime.run_manager import AgentRunManager


class FakeUsage:
    def __init__(self, input_tokens, output_tokens, total_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens


class FakeResponse:
    def __init__(
        self,
        response_id,
        output=None,
        output_text="",
        usage=None,
    ):
        self.id = response_id
        self.output = output or []
        self.output_text = output_text
        self.usage = usage


class FakeFunctionCall:
    def __init__(
        self,
        call_id,
        name,
        arguments,
    ):
        self.type = "function_call"
        self.call_id = call_id
        self.name = name
        self.arguments = arguments


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)

        if len(self.calls) == 1:
            return FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_123",
                        name="lookup_order",
                        arguments='{"order_id": "order_001"}',
                    )
                ],
            )

        return FakeResponse(
            response_id="response_2",
            output_text="Your order has been delivered.",
        )


class FakeOpenAI:
    def __init__(self):
        self.responses = FakeResponses()


class FailingResponses(FakeResponses):
    def create(self, **kwargs):
        self.calls.append(kwargs)

        if len(self.calls) == 1:
            return FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_123",
                        name="lookup_order",
                        arguments='{"order_id": "order_001"}',
                    )
                ],
            )

        return FakeResponse(
            response_id="response_2",
            output_text="I could not retrieve your order information.",
        )


class FailingOpenAI:
    def __init__(self):
        self.responses = FailingResponses()



def test_agent_executes_tool_and_returns_final_answer():
    client = FakeOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_001",
        customer_id="customer_001",
        message="Where is my order order_001?",
    )

    answer = agent.run(ticket)

    assert answer == "Your order has been delivered."

    assert len(client.responses.calls) == 2

    second_call = client.responses.calls[1]

    assert second_call["previous_response_id"] == "response_1"

    assert second_call["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call_123",
            "output": str(
                {
                    "order_id": "order_001",
                    "customer_id": "customer_001",
                    "status": "delivered",
                    "amount_cents": 12999,
                    "currency": "EUR",
                    "created_at": "2026-07-20",
                }
            ),
        }
    ]


def test_agent_returns_tool_error_to_model(monkeypatch):
    def failing_tool(order_id: str, *, trusted_customer_id=None):
        raise RuntimeError("database connection failed")

    from supportops_agent.tools.executor import TOOL_REGISTRY

    monkeypatch.setattr(TOOL_REGISTRY["lookup_order"], "handler", failing_tool)

    client = FailingOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_001",
        customer_id="customer_001",
        message="Where is my order order_001?",
    )

    answer = agent.run(ticket)

    assert answer == "I could not retrieve your order information."

    second_call = client.responses.calls[1]

    assert second_call["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call_123",
            "output": (
                "Tool execution error: "
                "Tool 'lookup_order' failed [tool_execution_error]"
            ),
        }
    ]
    assert "database connection failed" not in str(second_call["input"])


def test_build_tool_output_for_success():
    agent = SupportAgent(FakeOpenAI())

    tool_result = ToolResult(
        call_id="call_123",
        name="lookup_order",
        output={"status": "delivered"},
    )

    result = agent.build_tool_output(tool_result)

    assert result == {
        "type": "function_call_output",
        "call_id": "call_123",
        "output": "{'status': 'delivered'}",
    }


def test_build_tool_output_for_error():
    agent = SupportAgent(FakeOpenAI())

    tool_result = ToolResult(
        call_id="call_123",
        name="lookup_order",
        error="database connection failed",
    )

    result = agent.build_tool_output(tool_result)

    assert result == {
        "type": "function_call_output",
        "call_id": "call_123",
        "output": (
            "Tool execution error: database connection failed"
        ),
    }


def test_agent_creates_execution_trace():
    client = FakeOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_001",
        customer_id="customer_001",
        message="Where is my order order_001?",
    )

    agent.run(ticket)

    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert agent.last_run.finished_at is not None

    assert agent.last_run is not None
    assert agent.last_run.ticket_id == "ticket_001"

    assert len(agent.last_run.steps) == 4

    llm_step = agent.last_run.steps[0]

    assert llm_step.step_number == 1
    assert llm_step.step_type == AgentStepType.LLM_CALL
    assert llm_step.input == {
        "message_length": len("Where is my order order_001?"),
    }
    assert llm_step.output == {
        "model": "gpt-4.1-mini",
        "usage": None,
    }

    tool_step = agent.last_run.steps[1]

    assert tool_step.step_number == 2
    assert tool_step.step_type == AgentStepType.TOOL_EXECUTION
    assert tool_step.input == {
        "call_index": 0,
        "tool": "lookup_order",
        "argument_count": 1,
        "validation_status": "valid",
    }
    # SR-4: сырой результат инструмента в trace не пишется (только метаданные).
    assert tool_step.output == {"output_field_count": 6}
    assert tool_step.error is None

    assert tool_step.finished_at is not None
    assert tool_step.finished_at >= tool_step.started_at
    assert tool_step.duration_ms is not None
    assert tool_step.duration_ms >= 0


    second_llm_step = agent.last_run.steps[2]

    assert second_llm_step.step_number == 3
    assert second_llm_step.step_type == AgentStepType.LLM_CALL
    assert second_llm_step.input == {"tool_output_count": 1}
    assert second_llm_step.output == {
        "model": "gpt-4.1-mini",
        "usage": None,
    }


    final_step = agent.last_run.steps[3]

    assert final_step.step_number == 4
    assert final_step.step_type == AgentStepType.FINAL
    assert final_step.input is None
    assert final_step.output == {
        "answer_length": len("Your order has been delivered."),
        "response_generated": True,
    }
    assert final_step.error is None

    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert agent.last_run.started_at is not None
    assert agent.last_run.finished_at is not None
    assert agent.last_run.finished_at >= agent.last_run.started_at


def test_agent_marks_run_as_failed_when_step_limit_is_exceeded(monkeypatch):
    client = FakeOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_001",
        customer_id="customer_001",
        message="Where is my order order_001?",
    )

    def always_return_function_call(*args, **kwargs):
        return FakeResponse(
            response_id="response_loop",
            output=[
                FakeFunctionCall(
                    call_id="call_123",
                    name="lookup_order",
                    arguments='{"order_id": "order_001"}',
                )
            ],
        )

    monkeypatch.setattr(
        client.responses,
        "create",
        always_return_function_call,
    )

    with pytest.raises(RuntimeError, match="maximum number of steps"):
        agent.run(ticket)

    assert agent.last_run is not None
    assert agent.last_run.status == AgentRunStatus.FAILED
    assert agent.last_run.finished_at is not None

    assert agent.last_run.steps[-1].step_type == AgentStepType.FAILURE
    assert (
        agent.last_run.steps[-1].error == "agent_execution_error"
    )
    assert "maximum number of steps" not in agent.last_run.model_dump_json()


class UnknownToolResponses(FakeResponses):
    def create(self, **kwargs):
        self.calls.append(kwargs)

        return FakeResponse(
            response_id="response_1",
            output=[
                FakeFunctionCall(
                    call_id="call_123",
                    name="refund_order",
                    arguments='{"order_id": "order_001"}',
                )
            ],
        )


class UnknownToolOpenAI:
    def __init__(self):
        self.responses = UnknownToolResponses()


def test_agent_records_explicit_failure_step_for_unrecognized_tool_name():
    client = UnknownToolOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_008",
        customer_id="customer_001",
        message="Please refund my order order_001",
    )

    with pytest.raises(ValidationError):
        agent.run(ticket)

    assert agent.last_run.status == AgentRunStatus.FAILED
    assert agent.last_run.finished_at is not None

    assert agent.last_run.steps[-1].step_type == AgentStepType.FAILURE
    assert agent.last_run.steps[-1].error is not None


class BrokenResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("OpenAI API unavailable")


class BrokenOpenAI:
    def __init__(self):
        self.responses = BrokenResponses()


def test_agent_records_explicit_failure_step_for_client_api_error():
    client = BrokenOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_009",
        customer_id="customer_001",
        message="Where is my order order_001?",
    )

    with pytest.raises(RuntimeError, match="OpenAI API unavailable"):
        agent.run(ticket)

    assert agent.last_run.status == AgentRunStatus.FAILED
    assert agent.last_run.finished_at is not None

    assert len(agent.last_run.steps) == 1
    assert agent.last_run.steps[0].step_type == AgentStepType.FAILURE
    assert agent.last_run.steps[0].error == "agent_execution_error"
    assert "OpenAI API unavailable" not in agent.last_run.model_dump_json()


class ParallelToolCallResponses(FakeResponses):
    def create(self, **kwargs):
        self.calls.append(kwargs)

        if len(self.calls) == 1:
            return FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_order",
                        name="lookup_order",
                        arguments='{"order_id": "order_001"}',
                    ),
                    FakeFunctionCall(
                        call_id="call_customer",
                        name="lookup_customer",
                        arguments='{"customer_id": "customer_001"}',
                    ),
                ],
            )

        return FakeResponse(
            response_id="response_2",
            output_text="Your order was delivered. Contact: john@example.com.",
        )


class ParallelToolCallOpenAI:
    def __init__(self):
        self.responses = ParallelToolCallResponses()


def test_agent_executes_multiple_tool_calls_from_a_single_response():
    client = ParallelToolCallOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_002",
        customer_id="customer_001",
        message="Where is my order order_001, and who can I contact?",
    )

    answer = agent.run(ticket)

    assert answer == "Your order was delivered. Contact: john@example.com."
    assert len(client.responses.calls) == 2

    second_call = client.responses.calls[1]

    assert second_call["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call_order",
            "output": str(
                {
                    "order_id": "order_001",
                    "customer_id": "customer_001",
                    "status": "delivered",
                    "amount_cents": 12999,
                    "currency": "EUR",
                    "created_at": "2026-07-20",
                }
            ),
        },
        {
            "type": "function_call_output",
            "call_id": "call_customer",
            "output": str(
                {
                    "customer_id": "customer_001",
                    "name": "John",
                    "email": "john@example.com",
                }
            ),
        },
    ]

    tool_steps = [
        step
        for step in agent.last_run.steps
        if step.step_type == AgentStepType.TOOL_EXECUTION
    ]

    assert len(tool_steps) == 2
    assert tool_steps[0].input["tool"] == "lookup_order"
    assert tool_steps[1].input["tool"] == "lookup_customer"
    assert agent.last_run.status == AgentRunStatus.COMPLETED


class SequentialToolCallResponses(FakeResponses):
    def create(self, **kwargs):
        self.calls.append(kwargs)

        if len(self.calls) == 1:
            return FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_order",
                        name="lookup_order",
                        arguments='{"order_id": "order_001"}',
                    )
                ],
            )

        if len(self.calls) == 2:
            return FakeResponse(
                response_id="response_2",
                output=[
                    FakeFunctionCall(
                        call_id="call_customer",
                        name="lookup_customer",
                        arguments='{"customer_id": "customer_001"}',
                    )
                ],
            )

        return FakeResponse(
            response_id="response_3",
            output_text="Your order was delivered. Contact: john@example.com.",
        )


class SequentialToolCallOpenAI:
    def __init__(self):
        self.responses = SequentialToolCallResponses()


def test_agent_executes_sequential_tool_calls_across_multiple_rounds():
    client = SequentialToolCallOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_003",
        customer_id="customer_001",
        message="Where is my order order_001, and who can I contact?",
    )

    answer = agent.run(ticket)

    assert answer == "Your order was delivered. Contact: john@example.com."
    assert len(client.responses.calls) == 3

    assert client.responses.calls[1]["previous_response_id"] == "response_1"
    assert client.responses.calls[2]["previous_response_id"] == "response_2"

    tool_steps = [
        step
        for step in agent.last_run.steps
        if step.step_type == AgentStepType.TOOL_EXECUTION
    ]

    assert len(tool_steps) == 2
    assert tool_steps[0].input["tool"] == "lookup_order"
    assert tool_steps[1].input["tool"] == "lookup_customer"
    assert agent.last_run.status == AgentRunStatus.COMPLETED

    assert agent.state.ticket is ticket
    assert agent.state.step_count == 2
    assert agent.state.response.id == "response_3"


def test_agent_state_tracks_ticket_and_step_count_for_a_single_tool_call():
    client = FakeOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_001",
        customer_id="customer_001",
        message="Where is my order order_001?",
    )

    agent.run(ticket)

    assert agent.state.ticket is ticket
    assert agent.state.step_count == 1
    assert agent.state.response.id == "response_2"


class InvalidArgumentsResponses(FakeResponses):
    def create(self, **kwargs):
        self.calls.append(kwargs)

        if len(self.calls) == 1:
            return FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_123",
                        name="lookup_order",
                        arguments="{}",
                    )
                ],
            )

        return FakeResponse(
            response_id="response_2",
            output_text="I need an order ID to look that up.",
        )


class InvalidArgumentsOpenAI:
    def __init__(self):
        self.responses = InvalidArgumentsResponses()


def test_agent_reports_invalid_tool_arguments_to_model_instead_of_crashing():
    client = InvalidArgumentsOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_004",
        customer_id="customer_001",
        message="Where is my order?",
    )

    answer = agent.run(ticket)

    assert answer == "I need an order ID to look that up."

    second_call = client.responses.calls[1]

    assert len(second_call["input"]) == 1
    tool_output = second_call["input"][0]

    assert tool_output["call_id"] == "call_123"
    assert tool_output["output"].startswith(
        "Tool execution error: Invalid arguments for tool 'lookup_order'"
    )

    tool_step = agent.last_run.steps[1]

    assert tool_step.step_type == AgentStepType.TOOL_EXECUTION
    assert tool_step.output is None
    assert "Invalid arguments for tool 'lookup_order'" in tool_step.error
    assert agent.last_run.status == AgentRunStatus.COMPLETED


class MalformedJsonResponses(FakeResponses):
    def create(self, **kwargs):
        self.calls.append(kwargs)

        if len(self.calls) == 1:
            return FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_123",
                        name="lookup_order",
                        arguments="{not valid json",
                    )
                ],
            )

        return FakeResponse(
            response_id="response_2",
            output_text="Could you resend your order ID?",
        )


class MalformedJsonOpenAI:
    def __init__(self):
        self.responses = MalformedJsonResponses()


def test_agent_reports_malformed_tool_arguments_json_to_model():
    client = MalformedJsonOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_005",
        customer_id="customer_001",
        message="Where is my order?",
    )

    answer = agent.run(ticket)

    assert answer == "Could you resend your order ID?"

    second_call = client.responses.calls[1]
    tool_output = second_call["input"][0]

    assert tool_output["output"].startswith(
        "Tool execution error: Invalid arguments for tool 'lookup_order'"
    )
    assert agent.last_run.status == AgentRunStatus.COMPLETED


def test_extract_token_usage_returns_none_when_unavailable():
    agent = SupportAgent(FakeOpenAI())

    response = FakeResponse(response_id="response_1", output_text="hi")

    assert agent.extract_token_usage(response) is None


def test_extract_token_usage_returns_usage_dict_when_available():
    agent = SupportAgent(FakeOpenAI())

    response = FakeResponse(
        response_id="response_1",
        output_text="hi",
        usage=FakeUsage(input_tokens=42, output_tokens=8, total_tokens=50),
    )

    assert agent.extract_token_usage(response) == {
        "input_tokens": 42,
        "output_tokens": 8,
        "total_tokens": 50,
    }


class UsageReportingResponses(FakeResponses):
    def create(self, **kwargs):
        self.calls.append(kwargs)

        if len(self.calls) == 1:
            return FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_123",
                        name="lookup_order",
                        arguments='{"order_id": "order_001"}',
                    )
                ],
                usage=FakeUsage(
                    input_tokens=120, output_tokens=15, total_tokens=135
                ),
            )

        return FakeResponse(
            response_id="response_2",
            output_text="Your order has been delivered.",
            usage=FakeUsage(
                input_tokens=160, output_tokens=12, total_tokens=172
            ),
        )


class UsageReportingOpenAI:
    def __init__(self):
        self.responses = UsageReportingResponses()


def test_agent_records_token_usage_in_trace_when_available():
    client = UsageReportingOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_006",
        customer_id="customer_001",
        message="Where is my order order_001?",
    )

    agent.run(ticket)

    llm_steps = [
        step
        for step in agent.last_run.steps
        if step.step_type == AgentStepType.LLM_CALL
    ]

    assert llm_steps[0].output["model"] == "gpt-4.1-mini"
    assert llm_steps[0].output["usage"] == {
        "input_tokens": 120,
        "output_tokens": 15,
        "total_tokens": 135,
    }
    assert llm_steps[1].output["usage"] == {
        "input_tokens": 160,
        "output_tokens": 12,
        "total_tokens": 172,
    }


def test_tool_execution_step_records_latency(monkeypatch):
    import time

    from supportops_agent.tools.executor import TOOL_REGISTRY

    def slow_lookup_order(order_id: str, *, trusted_customer_id=None):
        time.sleep(0.02)
        return {"order_id": order_id, "status": "delivered"}

    monkeypatch.setattr(TOOL_REGISTRY["lookup_order"], "handler", slow_lookup_order)

    client = FakeOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    ticket = SupportTicket(
        ticket_id="ticket_007",
        customer_id="customer_001",
        message="Where is my order order_001?",
    )

    agent.run(ticket)

    tool_step = agent.last_run.steps[1]

    assert tool_step.step_type == AgentStepType.TOOL_EXECUTION
    assert tool_step.duration_ms is not None
    assert tool_step.duration_ms >= 20
    assert tool_step.finished_at >= tool_step.started_at


def test_get_function_calls_extracts_function_calls():
    client = FakeOpenAI()
    agent = SupportAgent(client, context=support_agent_context())

    response = FakeResponse(
        response_id="response_1",
        output=[
            FakeFunctionCall(
                call_id="call_123",
                name="lookup_order",
                arguments='{"order_id": "order_001"}',
            ),
        ],
    )

    function_calls = agent.get_function_calls(response)

    assert len(function_calls) == 1
    assert function_calls[0].call_id == "call_123"
    assert function_calls[0].name == "lookup_order"


def test_execute_function_calls_returns_tool_outputs():
    client = FakeOpenAI()
    agent = SupportAgent(client, context=support_agent_context())
    agent.run_manager = AgentRunManager("ticket_123")

    function_call = FakeFunctionCall(
        call_id="call_123",
        name="lookup_order",
        arguments='{"order_id": "order_001"}',
    )

    tool_outputs = agent.execute_function_calls([function_call])

    assert len(tool_outputs) == 1
    assert tool_outputs[0]["type"] == "function_call_output"
    assert tool_outputs[0]["call_id"] == "call_123"

    assert len(agent.last_run.steps) == 1

    step = agent.last_run.steps[0]
    assert step.step_type == AgentStepType.TOOL_EXECUTION
    assert step.input["tool"] == "lookup_order"
    assert step.input["argument_count"] == 1
    assert step.input["validation_status"] == "valid"
    assert "arguments" not in step.input
    assert step.error is None


