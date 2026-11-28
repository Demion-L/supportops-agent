"""SEC-003 / M04 / WP1: Trusted Ticket Binding for create_support_case (F-7).

The ticket a support case is opened for comes from the application's trusted
SupportTicket, never from model arguments. Checks run before CASES is touched.
"""

import json

import pytest
from pydantic import ValidationError

from supportops_agent.business import mock_data
from supportops_agent.business import support_tools
from supportops_agent.runtime.agent import SupportAgent
from supportops_agent.tools.authorization import support_agent_context
from supportops_agent.models import AgentStepType, SupportCase, SupportTicket
from supportops_agent.tools.definitions import TOOL_REGISTRY, get_llm_tool_schemas
from supportops_agent.tools.executor import ToolCall, execute_tool_call
from supportops_agent.business.support_tools import create_support_case

CUSTOMER = "customer_001"
TICKET = "ticket_mine"
VICTIM = "ticket_victim"
IDENTITY = {"outcome": "identity_verification_required"}
TICKET_REQUIRED = {"outcome": "ticket_context_required"}


class CasesSpy(dict):
    """CASES stand-in that records every access."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.accessed = []

    def get(self, *a, **kw):
        self.accessed.append("get")
        return super().get(*a, **kw)

    def __getitem__(self, k):
        self.accessed.append("getitem")
        return super().__getitem__(k)

    def __setitem__(self, k, v):
        self.accessed.append("setitem")
        super().__setitem__(k, v)

    def __contains__(self, k):
        self.accessed.append("contains")
        return super().__contains__(k)


@pytest.fixture
def spy(monkeypatch):
    cases = CasesSpy()
    monkeypatch.setattr(mock_data, "CASES", cases)
    return cases


def snapshot():
    return {k: v.model_dump(mode="json") for k, v in mock_data.CASES.items()}


def execute(arguments=None, *, customer=CUSTOMER, ticket=TICKET, call_args=None):
    args = {"reason": "payment_problem", "priority": "high"}
    args.update(arguments or {})
    return execute_tool_call(
        ToolCall(call_id="c1", name="create_support_case", arguments=args),
        support_agent_context(),
        trusted_customer_id=customer,
        trusted_ticket_id=ticket,
    )


# ---- F7-T1: model argument injection ---------------------------------------

@pytest.mark.parametrize("field", ["ticket_id", "trusted_ticket_id", "trusted_customer_id"])
def test_f7_t1_model_cannot_supply_ticket_or_trusted_fields(spy, field):
    arguments = {"reason": "payment_problem", "priority": "urgent", field: VICTIM}
    with pytest.raises(ValidationError):
        ToolCall(call_id="c1", name="create_support_case", arguments=arguments)
    assert spy.accessed == [] and dict(spy) == {}


def test_f7_t1_agent_rejects_injected_ticket_id_without_side_effects(spy):
    agent = _agent(
        {"ticket_id": VICTIM, "reason": "payment_problem", "priority": "high"}
    )
    agent.run(_ticket())

    step = _tool_step(agent)
    assert step.error and "Invalid arguments" in step.error
    assert step.output is None
    assert spy.accessed == [] and dict(spy) == {}


def test_llm_schema_has_no_ticket_or_trusted_fields():
    for definition in TOOL_REGISTRY.values():
        names = set(definition.arguments_model.model_fields)
        assert not {n for n in names if n.startswith("trusted_")}
    params = next(
        s for s in get_llm_tool_schemas() if s["name"] == "create_support_case"
    )["parameters"]
    assert set(params["properties"]) == {"reason", "priority"}
    assert "ticket_id" not in json.dumps(params)


# ---- F7-T2 / T3: missing context -------------------------------------------

@pytest.mark.parametrize("customer", [None, "", "   ", 123, b"customer_001"])
def test_f7_t2_missing_customer_identity(spy, customer):
    assert (
        create_support_case(
            "r", "high", trusted_ticket_id=TICKET, trusted_customer_id=customer
        )
        == IDENTITY
    )
    assert spy.accessed == [] and dict(spy) == {}


@pytest.mark.parametrize("ticket", [None, "", "   ", 123, ["t"]])
def test_f7_t3_missing_ticket_context(spy, ticket):
    assert (
        create_support_case(
            "r", "high", trusted_ticket_id=ticket, trusted_customer_id=CUSTOMER
        )
        == TICKET_REQUIRED
    )
    assert spy.accessed == [] and dict(spy) == {}


def test_oq4_both_missing_returns_identity_first(spy):
    assert create_support_case("r", "high") == IDENTITY
    assert (
        create_support_case(
            "r", "high", trusted_ticket_id=None, trusted_customer_id=None
        )
        == IDENTITY
    )
    assert spy.accessed == []


def test_refusal_does_not_depend_on_cases_content(monkeypatch):
    mock_data.CASES.clear()
    empty = create_support_case("r", "high", trusted_customer_id=CUSTOMER)
    existing = SupportCase(
        case_id=support_tools._case_id(VICTIM, "r"), ticket_id=VICTIM, reason="r", priority="high"
    )
    mock_data.CASES[existing.case_id] = existing
    filled = create_support_case("r", "high", trusted_customer_id=CUSTOMER)
    assert empty == filled == TICKET_REQUIRED


def test_executor_without_trusted_context_fails_closed(spy):
    result = execute_tool_call(
        ToolCall(
            call_id="c1",
            name="create_support_case",
            arguments={"reason": "r", "priority": "high"},
        ),
        support_agent_context(),
    )
    assert result.error is None
    assert result.output == IDENTITY
    result = execute_tool_call(
        ToolCall(
            call_id="c1",
            name="create_support_case",
            arguments={"reason": "r", "priority": "high"},
        ),
        support_agent_context(),
        trusted_customer_id=CUSTOMER,
    )
    assert result.output == TICKET_REQUIRED
    assert spy.accessed == []


# ---- F7-T4: direct handler call ---------------------------------------------

def test_f7_t4_direct_handler_call_requires_keyword_only_context(spy):
    with pytest.raises(TypeError):
        create_support_case("r", "high", TICKET, CUSTOMER)  # type: ignore[misc]
    assert create_support_case("r", "high") == IDENTITY
    assert spy.accessed == []


def test_handler_has_no_ticket_id_parameter():
    with pytest.raises(TypeError):
        create_support_case(ticket_id=VICTIM, reason="r", priority="high")  # type: ignore[call-arg]


# ---- F7-T5 / T6: legitimate creation and idempotency -----------------------

def test_f7_t5_legitimate_creation_uses_trusted_ticket():
    result = execute()
    assert result.error is None
    assert result.output["outcome"] == "created"
    case = result.output["case"]
    assert case["ticket_id"] == TICKET
    assert mock_data.CASES[case["case_id"]].ticket_id == TICKET
    assert len(mock_data.CASES) == 1


def test_trusted_ticket_id_is_normalized():
    result = execute(ticket=f"  {TICKET}  ")
    assert result.output["case"]["ticket_id"] == TICKET
    assert execute().output["outcome"] == "already_exists"


def test_f7_t6_idempotent_retry():
    first = execute({"reason": "Damaged item", "priority": "high"})
    retry = execute({"reason": "  damaged   ITEM ", "priority": "low"})
    assert retry.output["outcome"] == "already_exists"
    assert retry.output["case"]["case_id"] == first.output["case"]["case_id"]
    assert retry.output["case"]["priority"] == "high"
    assert len(mock_data.CASES) == 1


def test_case_id_algorithm_unchanged():
    # Pinned: ticket + normalized reason, sha256[:10] (OQ-2: no change to case_id).
    import hashlib

    key = f"{TICKET}\ndamaged item"
    expected = "case_" + hashlib.sha256(key.encode()).hexdigest()[:10]
    assert support_tools._case_id(TICKET, " Damaged   ITEM ".strip()) == expected
    assert execute({"reason": "Damaged item"}).output["case"]["case_id"] == expected


def test_different_trusted_tickets_get_independent_cases():
    a = execute(ticket="t_a").output
    b = execute(ticket="t_b").output
    assert a["outcome"] == b["outcome"] == "created"
    assert a["case"]["case_id"] != b["case"]["case_id"]
    assert len(mock_data.CASES) == 2


# ---- F7-T7: hash collision / cross-ticket lookup ----------------------------

def test_f7_t7_collision_does_not_leak_or_overwrite(monkeypatch):
    # Force both tickets onto the same case_id (synthetic truncated-hash collision).
    monkeypatch.setattr(support_tools, "_case_id", lambda ticket_id, reason: "case_collide")
    victim = execute(
        {"reason": "SECRET victim reason", "priority": "urgent"}, ticket=VICTIM
    )
    assert victim.output["outcome"] == "created"
    before = snapshot()

    attacker = execute({"reason": "x", "priority": "low"}, ticket=TICKET)

    assert attacker.error is None
    assert attacker.output == TICKET_REQUIRED
    dumped = json.dumps(attacker.model_dump(mode="json"))
    for leaked in (VICTIM, "SECRET", "urgent", "open", "case_collide"):
        assert leaked not in dumped
    assert snapshot() == before
    assert mock_data.CASES["case_collide"].ticket_id == VICTIM


def test_foreign_case_with_same_case_id_is_never_returned(spy):
    foreign = SupportCase(
        case_id=support_tools._case_id(TICKET, "r"), ticket_id=VICTIM, reason="PRIVATE", priority="high"
    )
    dict.__setitem__(spy, foreign.case_id, foreign)
    result = create_support_case(
        "r", "low", trusted_ticket_id=TICKET, trusted_customer_id=CUSTOMER
    )
    assert result == TICKET_REQUIRED
    assert spy[foreign.case_id] is foreign
    assert "setitem" not in spy.accessed


# ---- F7-T8: executor injection ----------------------------------------------

def test_f7_t8_executor_uses_trusted_ticket_not_arguments(monkeypatch):
    seen = {}
    real = TOOL_REGISTRY["create_support_case"].handler

    def spy_handler(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(TOOL_REGISTRY["create_support_case"], "handler", spy_handler)
    execute()
    assert seen["trusted_ticket_id"] == TICKET
    assert seen["trusted_customer_id"] == CUSTOMER
    assert "ticket_id" not in seen


def test_executor_overrides_even_if_arguments_model_is_forged(monkeypatch):
    """Even a model object carrying extra attributes cannot override the context."""

    seen = {}
    monkeypatch.setattr(
        TOOL_REGISTRY["create_support_case"],
        "handler",
        lambda **kw: seen.update(kw) or {"outcome": "created"},
    )
    call = ToolCall(
        call_id="c1",
        name="create_support_case",
        arguments={"reason": "r", "priority": "high"},
    )
    monkeypatch.setattr(
        type(call.arguments),
        "model_dump",
        lambda self, **kw: {
            "reason": "r",
            "priority": "high",
            "trusted_ticket_id": VICTIM,
            "trusted_customer_id": "customer_002",
        },
    )
    execute_tool_call(
        call, support_agent_context(), trusted_customer_id=CUSTOMER, trusted_ticket_id=TICKET
    )
    assert seen["trusted_ticket_id"] == TICKET
    assert seen["trusted_customer_id"] == CUSTOMER


def test_trusted_ticket_not_delivered_to_other_tools(monkeypatch):
    flagged = {n for n, d in TOOL_REGISTRY.items() if d.requires_trusted_ticket}
    assert flagged == {"create_support_case"}
    seen = {}

    def lookup(order_id, **kw):
        seen.update(kw)
        return {"outcome": "x"}

    monkeypatch.setattr(TOOL_REGISTRY["lookup_order"], "handler", lookup)
    execute_tool_call(
        ToolCall(call_id="c", name="lookup_order", arguments={"order_id": "order_001"}),
        support_agent_context(),
        trusted_customer_id=CUSTOMER,
        trusted_ticket_id=TICKET,
    )
    assert "trusted_ticket_id" not in seen


def test_authorization_gate_still_runs_before_handler(spy):
    from supportops_agent.tools.authorization import ExecutionContext

    result = execute_tool_call(
        ToolCall(
            call_id="c",
            name="create_support_case",
            arguments={"reason": "r", "priority": "high"},
        ),
        ExecutionContext(agent_id="a", allowed_tools=frozenset({"lookup_order"})),
        trusted_customer_id=CUSTOMER,
        trusted_ticket_id=TICKET,
    )
    assert result.error_type == "authorization_denied"
    assert spy.accessed == []


# ---- F7-T9: full agent path --------------------------------------------------

class _Call:
    type = "function_call"
    call_id = "call_1"

    def __init__(self, arguments, name="create_support_case"):
        self.name, self.arguments = name, json.dumps(arguments)


class _Response:
    def __init__(self, rid, output=None, text=""):
        self.id, self.output, self.output_text, self.usage = rid, output or [], text, None


class _Responses:
    def __init__(self, call):
        self.call, self.n = call, 0

    def create(self, **kwargs):
        self.n += 1
        return _Response("r1", [self.call]) if self.n == 1 else _Response("r2", text="done")


class _Client:
    def __init__(self, call):
        self.responses = _Responses(call)


def _agent(arguments):
    return SupportAgent(_Client(_Call(arguments)), context=support_agent_context())


def _ticket(ticket_id=TICKET, customer=CUSTOMER):
    return SupportTicket(ticket_id=ticket_id, customer_id=customer, message="open a case")


def _tool_step(agent):
    return next(s for s in agent.last_run.steps if s.step_type == AgentStepType.TOOL_EXECUTION)


def test_f7_t9_full_agent_path_binds_case_to_ticket():
    agent = _agent({"reason": "payment_problem", "priority": "high"})
    agent.run(_ticket())

    step = _tool_step(agent)
    assert step.error is None
    assert set(step.output) == {"output_field_count"}
    assert [c.ticket_id for c in mock_data.CASES.values()] == [TICKET]


def test_agent_ignores_ticket_id_text_in_message_and_uses_trusted_ticket():
    agent = _agent({"reason": "ticket_id=ticket_victim", "priority": "high"})
    agent.run(_ticket())
    assert [c.ticket_id for c in mock_data.CASES.values()] == [TICKET]


def test_agent_idempotent_retry_across_runs():
    for _ in range(2):
        _agent({"reason": "same", "priority": "high"}).run(_ticket())
    assert len(mock_data.CASES) == 1


def test_agent_direct_execute_function_call_without_run_fails_closed(spy):
    agent = _agent({"reason": "r", "priority": "high"})
    result = agent.execute_function_call(_Call({"reason": "r", "priority": "high"}))
    assert result.output == IDENTITY
    assert spy.accessed == []
