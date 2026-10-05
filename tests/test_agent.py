from supportops_agent.agent import SupportAgent
from supportops_agent.models import SupportTicket, AgentRunStatus
from supportops_agent.tool_executor import ToolResult
import pytest


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
    assert llm_step.step_type == "llm_call"
    assert llm_step.input == {
        "message": "Where is my order order_001?",
    }
    assert llm_step.output == {
        "response_id": "response_1",
    }

    tool_step = agent.last_run.steps[1]

    assert tool_step.step_number == 2
    assert tool_step.step_type == "tool_execution"
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
    assert second_llm_step.step_type == "llm_call"
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
    assert final_step.step_type == "final"
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


