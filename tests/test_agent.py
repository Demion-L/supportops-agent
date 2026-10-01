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