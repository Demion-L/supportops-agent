import json
from datetime import date

import pytest
from pydantic import ValidationError

from supportops_agent import mock_data
from supportops_agent.agent import SupportAgent
from supportops_agent.authorization import support_agent_context
from supportops_agent.authorization import support_agent_context
from supportops_agent.models import AgentRunStatus, SupportTicket
from supportops_agent.tool_definitions import ToolDefinition, get_llm_tool_schemas
from supportops_agent.tool_executor import (
    TOOL_REGISTRY,
    ToolCall,
    execute_tool_call,
)
from supportops_agent.refund_evaluation import check_refund_eligibility

ALL_TOOLS = {
    "lookup_order",
    "lookup_customer",
    "get_order_tracking",
    "check_refund_eligibility",
    "create_support_case",
}


def run(name, trusted_customer_id=None, **arguments):
    return execute_tool_call(
        ToolCall(call_id="c1", name=name, arguments=arguments),
        support_agent_context(),
        trusted_customer_id=trusted_customer_id,
    )


# --- get_order_tracking -----------------------------------------------------

def test_tracking_found_for_shipped_order():
    result = run("get_order_tracking", "customer_002", order_id="order_002")
    assert result.error is None
    assert result.output == {
        "outcome": "tracking_found",
        "order_id": "order_002",
        "order_status": "shipped",
        "carrier": "UPS",
        "tracking_number": "UPS-0002",
        "shipment_status": "in_transit",
        "last_update": "2026-08-07",
    }


def test_tracking_not_shipped_and_no_shipment():
    assert run("get_order_tracking", "customer_003", order_id="order_003").output["outcome"] == "not_shipped"
    assert run("get_order_tracking", "customer_004", order_id="order_004").output["outcome"] == "no_shipment"


def test_tracking_unknown_order_is_business_outcome_not_error():
    result = run("get_order_tracking", "customer_001", order_id="order_999")
    assert result.error is None
    assert result.output == {"outcome": "order_not_found", "order_id": "order_999"}


# --- check_refund_eligibility -----------------------------------------------

@pytest.mark.parametrize(
    "order_id, customer, decision, reason",
    [
        ("order_008", "customer_001", "allow", "within_standard_window"),  # 8 days
        ("order_001", "customer_001", "deny", "outside_grace_period"),     # 69 days
        ("order_003", "customer_003", "deny", "not_delivered_yet"),
        ("order_002", "customer_002", "deny", "not_delivered_yet"),
        ("order_004", "customer_004", "deny", "order_cancelled"),
        ("order_005", "customer_005", "deny", "already_refunded"),
    ],
)
def test_refund_tool_returns_engine_decision(order_id, customer, decision, reason):
    result = run("check_refund_eligibility", trusted_customer_id=customer, order_id=order_id)
    assert result.error is None
    assert result.output["outcome"] == "evaluated"
    assert result.output["decision"] == decision
    assert result.output["reason_code"] == reason
    assert result.output["policy_version"] == "refund-policy-v1"


def test_refund_tool_uses_injected_date_with_engine_boundaries():
    # order_008 доставлен 2026-09-23: дни 40/41/45/46 -> allow/exception/exception/deny
    from datetime import timedelta

    delivered = date(2026, 9, 23)
    expected = {40: "allow", 41: "exception_eligible", 45: "exception_eligible", 46: "deny"}
    for days, decision in expected.items():
        out = check_refund_eligibility(
            "order_008", trusted_customer_id="customer_001",
            today=delivered + timedelta(days=days),
        )
        assert out["decision"] == decision and out["days_since_delivery"] == days


def test_refund_unknown_order_is_not_distinguishable_from_foreign_order():
    unknown = run("check_refund_eligibility", trusted_customer_id="customer_001", order_id="order_999")
    foreign = run("check_refund_eligibility", trusted_customer_id="customer_001", order_id="order_002")
    assert unknown.error is None and unknown.output == foreign.output
    assert unknown.output["outcome"] == "order_not_verified"
    assert "decision" not in unknown.output


def test_refund_tool_without_trusted_identity_reveals_nothing():
    result = run("check_refund_eligibility", order_id="order_008")
    assert result.output == {"outcome": "identity_verification_required"}


def test_refund_check_does_not_modify_orders():
    before = mock_data.ORDERS["order_008"].model_copy()
    run("check_refund_eligibility", trusted_customer_id="customer_001", order_id="order_008")
    assert mock_data.ORDERS["order_008"] == before


# --- create_support_case ----------------------------------------------------

def test_create_case_stores_and_returns_structured_result():
    result = run("create_support_case", ticket_id="t1", reason="Damaged item", priority="high")
    assert result.error is None
    assert result.output["outcome"] == "created"
    case = result.output["case"]
    assert case["ticket_id"] == "t1" and case["priority"] == "high"
    assert case["status"] == "open"
    assert mock_data.CASES[case["case_id"]].reason == "Damaged item"


def test_duplicate_case_is_not_created_on_retry():
    first = run("create_support_case", ticket_id="t1", reason="Damaged item", priority="high")
    retry = run("create_support_case", ticket_id="t1", reason="  damaged   ITEM ", priority="low")
    assert retry.output["outcome"] == "already_exists"
    assert retry.output["case"] == first.output["case"]  # priority not overwritten
    assert len(mock_data.CASES) == 1


def test_different_reason_or_ticket_creates_new_case():
    run("create_support_case", ticket_id="t1", reason="A", priority="low")
    run("create_support_case", ticket_id="t1", reason="B", priority="low")
    run("create_support_case", ticket_id="t2", reason="A", priority="low")
    assert len(mock_data.CASES) == 3


@pytest.mark.parametrize(
    "arguments",
    [
        {"ticket_id": "t1", "reason": "x", "priority": "critical"},
        {"ticket_id": "t1", "reason": "", "priority": "low"},
        {"ticket_id": "t1", "priority": "low"},
        {"ticket_id": "", "reason": "x", "priority": "low"},
        {"ticket_id": "t1", "reason": "x", "priority": "low", "extra": 1},
    ],
)
def test_create_case_rejects_invalid_arguments(arguments):
    with pytest.raises(ValidationError):
        ToolCall(call_id="c1", name="create_support_case", arguments=arguments)
    assert mock_data.CASES == {}


@pytest.mark.parametrize("name", ["get_order_tracking", "check_refund_eligibility"])
def test_new_order_tools_reject_invalid_arguments(name):
    for bad in ({}, {"order_id": ""}, {"order_id": 5}, {"order_id": "o", "x": 1}):
        with pytest.raises(ValidationError):
            ToolCall(call_id="c1", name=name, arguments=bad)


# --- execution failures -----------------------------------------------------

@pytest.mark.parametrize(
    "name, arguments",
    [
        ("get_order_tracking", {"order_id": "order_001"}),
        ("check_refund_eligibility", {"order_id": "order_001"}),
        ("create_support_case", {"ticket_id": "t", "reason": "r", "priority": "low"}),
    ],
)
def test_handler_crash_becomes_execution_failure(monkeypatch, name, arguments):
    def boom(**_):
        raise RuntimeError("storage down")

    monkeypatch.setattr(TOOL_REGISTRY[name], "handler", boom)
    result = run(name, **arguments)
    assert result.output is None
    assert result.error == f"Tool '{name}' failed: storage down"
    assert result.error_type == "execution_failed"


def test_non_serializable_output_is_execution_failure(monkeypatch):
    monkeypatch.setattr(TOOL_REGISTRY["lookup_order"], "handler", lambda order_id, **_: {"d": object()})
    result = run("lookup_order", "customer_001", order_id="order_001")
    assert result.output is None and result.error_type == "execution_failed"


def test_results_are_json_serializable():
    for name, args in [
        ("lookup_order", {"order_id": "order_001"}),
        ("get_order_tracking", {"order_id": "order_001"}),
        ("check_refund_eligibility", {"order_id": "order_001"}),
        ("create_support_case", {"ticket_id": "t", "reason": "r", "priority": "low"}),
    ]:
        json.dumps(run(name, "customer_001", **args).model_dump(mode="json"))


def test_successful_result_has_no_error_type():
    assert run("lookup_order", "customer_001", order_id="order_001").error_type is None


# --- registry, schemas, descriptions -----------------------------------------

def test_registry_contains_exactly_expected_tools_and_names_match_keys():
    assert set(TOOL_REGISTRY) == ALL_TOOLS
    assert all(key == d.name for key, d in TOOL_REGISTRY.items())
    assert all(isinstance(d, ToolDefinition) for d in TOOL_REGISTRY.values())


def test_llm_schemas_are_generated_from_registry():
    schemas = get_llm_tool_schemas()
    assert [s["name"] for s in schemas] == list(TOOL_REGISTRY)
    for schema in schemas:
        definition = TOOL_REGISTRY[schema["name"]]
        assert schema["type"] == "function"
        assert schema["description"] == definition.description
        params = schema["parameters"]
        assert params["type"] == "object"
        assert params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])
        json.dumps(schema)


def test_create_support_case_schema_exposes_priority_enum():
    params = next(s for s in get_llm_tool_schemas() if s["name"] == "create_support_case")["parameters"]
    assert set(params["required"]) == {"ticket_id", "reason", "priority"}
    priority = params["properties"]["priority"]
    values = priority.get("enum") or params["$defs"]["CasePriority"]["enum"]
    assert values == ["low", "normal", "high", "urgent"]


def test_existing_lookup_schemas_unchanged():
    schemas = {s["name"]: s for s in get_llm_tool_schemas()}
    assert schemas["lookup_order"]["parameters"]["properties"]["order_id"]["type"] == "string"
    assert schemas["lookup_order"]["parameters"]["required"] == ["order_id"]


def test_descriptions_are_meaningful_and_metadata_flags():
    for d in TOOL_REGISTRY.values():
        assert len(d.description) > 20
    assert "NOT issue a refund" in TOOL_REGISTRY["check_refund_eligibility"].description
    assert "duplicate" in TOOL_REGISTRY["create_support_case"].description
    side_effects = {n for n, d in TOOL_REGISTRY.items() if d.has_side_effects}
    assert side_effects == {"create_support_case"}
    assert not any(d.requires_approval for d in TOOL_REGISTRY.values())


def test_no_refund_or_cancel_tools_exist():
    assert not any("cancel" in n or n in {"refund_order", "issue_refund"} for n in TOOL_REGISTRY)


def test_unknown_tool_name_rejected():
    with pytest.raises(ValidationError):
        ToolCall(call_id="c1", name="refund_order", arguments={"order_id": "order_001"})


# --- agent loop compatibility -----------------------------------------------

class _Call:
    type = "function_call"

    def __init__(self, call_id, name, arguments):
        self.call_id, self.name, self.arguments = call_id, name, arguments


class _Response:
    def __init__(self, id, output=(), text=""):
        self.id, self.output, self.output_text, self.usage = id, list(output), text, None


class _Responses:
    def __init__(self, scripted):
        self.scripted, self.calls = list(scripted), []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.scripted.pop(0)


class _Client:
    def __init__(self, scripted):
        self.responses = _Responses(scripted)


def test_agent_runs_new_tools_and_sends_registry_schemas():
    client = _Client(
        [
            _Response(
                "r1",
                [
                    _Call("c1", "check_refund_eligibility", '{"order_id": "order_008"}'),
                    _Call("c2", "create_support_case",
                          '{"ticket_id": "t9", "reason": "refund", "priority": "normal"}'),
                ],
            ),
            _Response("r2", text="done"),
        ]
    )
    agent = SupportAgent(client, context=support_agent_context())
    ticket = SupportTicket(ticket_id="t9", customer_id="customer_001", message="refund?")

    assert agent.run(ticket) == "done"
    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert client.responses.calls[0]["tools"] == get_llm_tool_schemas()
    tool_steps = [s for s in agent.last_run.steps if s.step_type == "tool_execution"]
    assert [s.error for s in tool_steps] == [None, None]
    assert tool_steps[0].output["decision"] == "allow"
    assert len(mock_data.CASES) == 1


def test_agent_reports_invalid_new_tool_arguments_to_model():
    client = _Client(
        [
            _Response("r1", [_Call("c1", "create_support_case", '{"ticket_id": "t", "priority": "bad"}')]),
            _Response("r2", text="sorry"),
        ]
    )
    agent = SupportAgent(client, context=support_agent_context())
    agent.run(SupportTicket(ticket_id="t", customer_id="customer_001", message="hi"))

    step = next(s for s in agent.last_run.steps if s.step_type == "tool_execution")
    assert "Invalid arguments for tool 'create_support_case'" in step.error
    assert mock_data.CASES == {}
