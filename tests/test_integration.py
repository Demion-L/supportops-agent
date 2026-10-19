import pytest

from supportops_agent.agent import SupportAgent
from supportops_agent.models import AgentRunStatus, AgentStepType, SupportTicket


class FakeResponse:
    def __init__(self, response_id, output=None, output_text=""):
        self.id = response_id
        self.output = output or []
        self.output_text = output_text


class FakeFunctionCall:
    def __init__(self, call_id, name, arguments):
        self.type = "function_call"
        self.call_id = call_id
        self.name = name
        self.arguments = arguments


class ScriptedResponses:
    """Replays a fixed sequence of responses, one per `create()` call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses[len(self.calls) - 1]


class ScriptedOpenAI:
    def __init__(self, responses):
        self.responses = ScriptedResponses(responses)


def test_order_status_inquiry_resolves_via_lookup_order():
    client = ScriptedOpenAI(
        [
            FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_1",
                        name="lookup_order",
                        arguments='{"order_id": "order_001"}',
                    )
                ],
            ),
            FakeResponse(
                response_id="response_2",
                output_text="Your order order_001 was delivered on 2026-07-20.",
            ),
        ]
    )
    agent = SupportAgent(client)

    ticket = SupportTicket(
        ticket_id="ticket_order_status",
        customer_id="customer_001",
        message="Where is my order order_001?",
        order_id="order_001",
    )

    answer = agent.run(ticket)

    assert answer == "Your order order_001 was delivered on 2026-07-20."
    assert agent.last_run.status == AgentRunStatus.COMPLETED


def test_customer_account_inquiry_resolves_via_lookup_customer():
    client = ScriptedOpenAI(
        [
            FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_1",
                        name="lookup_customer",
                        arguments='{"customer_id": "customer_001"}',
                    )
                ],
            ),
            FakeResponse(
                response_id="response_2",
                output_text="Your account is registered under john@example.com.",
            ),
        ]
    )
    agent = SupportAgent(client)

    ticket = SupportTicket(
        ticket_id="ticket_account_info",
        customer_id="customer_001",
        message="What email is on my account?",
    )

    answer = agent.run(ticket)

    assert answer == "Your account is registered under john@example.com."
    assert agent.last_run.status == AgentRunStatus.COMPLETED


def test_combined_order_and_customer_inquiry_resolves_sequentially():
    client = ScriptedOpenAI(
        [
            FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_order",
                        name="lookup_order",
                        arguments='{"order_id": "order_002"}',
                    )
                ],
            ),
            FakeResponse(
                response_id="response_2",
                output=[
                    FakeFunctionCall(
                        call_id="call_customer",
                        name="lookup_customer",
                        arguments='{"customer_id": "customer_002"}',
                    )
                ],
            ),
            FakeResponse(
                response_id="response_3",
                output_text=(
                    "Order order_002 has shipped. We'll notify Alice at "
                    "alice@example.com once it's delivered."
                ),
            ),
        ]
    )
    agent = SupportAgent(client)

    ticket = SupportTicket(
        ticket_id="ticket_combined",
        customer_id="customer_002",
        message="Where is my order order_002, and will I get an email update?",
        order_id="order_002",
    )

    answer = agent.run(ticket)

    assert "shipped" in answer
    assert "alice@example.com" in answer
    assert agent.last_run.status == AgentRunStatus.COMPLETED

    tool_steps = [
        step
        for step in agent.last_run.steps
        if step.step_type == AgentStepType.TOOL_EXECUTION
    ]
    assert [step.input["tool"] for step in tool_steps] == [
        "lookup_order",
        "lookup_customer",
    ]


def test_unknown_order_is_reported_back_to_customer_gracefully():
    client = ScriptedOpenAI(
        [
            FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_1",
                        name="lookup_order",
                        arguments='{"order_id": "order_999"}',
                    )
                ],
            ),
            FakeResponse(
                response_id="response_2",
                output_text=(
                    "I couldn't find an order with ID order_999. Could you "
                    "double-check the order number?"
                ),
            ),
        ]
    )
    agent = SupportAgent(client)

    ticket = SupportTicket(
        ticket_id="ticket_unknown_order",
        customer_id="customer_001",
        message="Where is my order order_999?",
        order_id="order_999",
    )

    answer = agent.run(ticket)

    assert "couldn't find" in answer
    assert agent.last_run.status == AgentRunStatus.COMPLETED

    tool_step = agent.last_run.steps[1]
    assert tool_step.output is None
    assert tool_step.error is None


def test_tool_outage_is_surfaced_to_customer_without_crashing_the_run(monkeypatch):
    from supportops_agent.tool_executor import TOOL_REGISTRY

    def failing_lookup_order(order_id: str):
        raise RuntimeError("database connection failed")

    monkeypatch.setitem(TOOL_REGISTRY, "lookup_order", failing_lookup_order)

    client = ScriptedOpenAI(
        [
            FakeResponse(
                response_id="response_1",
                output=[
                    FakeFunctionCall(
                        call_id="call_1",
                        name="lookup_order",
                        arguments='{"order_id": "order_001"}',
                    )
                ],
            ),
            FakeResponse(
                response_id="response_2",
                output_text=(
                    "I'm having trouble looking up your order right now. "
                    "I've escalated this to a human agent."
                ),
            ),
        ]
    )
    agent = SupportAgent(client)

    ticket = SupportTicket(
        ticket_id="ticket_tool_outage",
        customer_id="customer_001",
        message="Where is my order order_001?",
        order_id="order_001",
    )

    answer = agent.run(ticket)

    assert "escalated" in answer
    assert agent.last_run.status == AgentRunStatus.COMPLETED

    tool_step = agent.last_run.steps[1]
    assert tool_step.error == "Tool 'lookup_order' failed: database connection failed"


def test_agent_gives_up_and_fails_the_run_when_model_loops_forever():
    forever_looping_response = FakeResponse(
        response_id="response_loop",
        output=[
            FakeFunctionCall(
                call_id="call_1",
                name="lookup_order",
                arguments='{"order_id": "order_001"}',
            )
        ],
    )
    client = ScriptedOpenAI([forever_looping_response] * 20)
    agent = SupportAgent(client)

    ticket = SupportTicket(
        ticket_id="ticket_runaway_loop",
        customer_id="customer_001",
        message="Where is my order order_001?",
        order_id="order_001",
    )

    with pytest.raises(RuntimeError, match="maximum number of steps"):
        agent.run(ticket)

    assert agent.last_run.status == AgentRunStatus.FAILED
    assert agent.last_run.steps[-1].step_type == AgentStepType.FAILURE
