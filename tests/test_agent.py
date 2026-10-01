from unittest.mock import Mock

from supportops_agent.agent import SupportAgent
from supportops_agent.models import SupportTicket


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


