from supportops_agent.agent import SupportAgent
from supportops_agent.models import SupportTicket, AgentRunStatus, AgentStepType
from supportops_agent.tool_executor import ToolResult
import pytest
from supportops_agent.run_manager import AgentRunManager


class FakeResponse:
    def __init__(
        self,
        response_id,
        output=None,
        output_text="",
    ):
        self.id = response_id
        self.output = output or []
        self.output_text = output_text


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
    agent = SupportAgent(client)

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
    def failing_tool(order_id: str):
        raise RuntimeError("database connection failed")

    from supportops_agent.tool_executor import TOOL_REGISTRY

    monkeypatch.setitem(
        TOOL_REGISTRY,
        "lookup_order",
        failing_tool,
    )

    client = FailingOpenAI()
    agent = SupportAgent(client)

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
                "Tool 'lookup_order' failed: database connection failed"
            ),
        }
    ]


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
    agent = SupportAgent(client)

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
        "message": "Where is my order order_001?",
    }
    assert llm_step.output == {
        "response_id": "response_1",
    }

    tool_step = agent.last_run.steps[1]

    assert tool_step.step_number == 2
    assert tool_step.step_type == AgentStepType.TOOL_EXECUTION
    assert tool_step.input == {
        "call_id": "call_123",
        "tool": "lookup_order",
        "arguments": {
            "order_id": "order_001",
        },
    }
    assert tool_step.output == {
        "order_id": "order_001",
        "customer_id": "customer_001",
        "status": "delivered",
        "amount_cents": 12999,
        "currency": "EUR",
        "created_at": "2026-07-20",
    }
    assert tool_step.error is None


    second_llm_step = agent.last_run.steps[2]

    assert second_llm_step.step_number == 3
    assert second_llm_step.step_type == AgentStepType.LLM_CALL
    assert second_llm_step.input == {
        "tool_outputs": [
            {
                "type": "function_call_output",
                "call_id": "call_123",
                "output": str({
                    "order_id": "order_001",
                    "customer_id": "customer_001",
                    "status": "delivered",
                    "amount_cents": 12999,
                    "currency": "EUR",
                    "created_at": "2026-07-20",
                }),
            }
        ],
    }
    assert second_llm_step.output == {
        "response_id": "response_2",
    }


    final_step = agent.last_run.steps[3]

    assert final_step.step_number == 4
    assert final_step.step_type == AgentStepType.FINAL
    assert final_step.input is None
    assert final_step.output == {
        "answer": "Your order has been delivered.",
    }
    assert final_step.error is None

    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert agent.last_run.started_at is not None
    assert agent.last_run.finished_at is not None
    assert agent.last_run.finished_at >= agent.last_run.started_at


def test_agent_marks_run_as_failed_when_step_limit_is_exceeded(monkeypatch):
    client = FakeOpenAI()
    agent = SupportAgent(client)

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
        agent.last_run.steps[-1].error
        == "Agent exceeded maximum number of steps: 10"
    )



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
    agent = SupportAgent(client)

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
    agent = SupportAgent(client)

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
    agent = SupportAgent(client)

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
    agent = SupportAgent(client)

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


def test_get_function_calls_extracts_function_calls():
    client = FakeOpenAI()
    agent = SupportAgent(client)

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
    agent = SupportAgent(client)
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
    assert step.input["arguments"] == {"order_id": "order_001"}
    assert step.error is None


