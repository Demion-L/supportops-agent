import pytest
from pydantic import ValidationError

from supportops_agent.tools.authorization import support_agent_context
from supportops_agent.tools.executor import (
    ToolCall,
    execute_tool_call,
     TOOL_REGISTRY,
)


def test_execute_lookup_customer():
    call = ToolCall(
        call_id="test_customer",
        name="lookup_customer",
        arguments={"customer_id": "customer_001"},
    )

    result = execute_tool_call(
        call, support_agent_context(), trusted_customer_id="customer_001"
    )

    assert result.call_id == "test_customer"
    assert result.name == "lookup_customer"
    assert result.output == {
        "customer_id": "customer_001",
        "name": "John",
        "email": "john@example.com",
    }


def test_execute_lookup_order():
    call = ToolCall(
        call_id="test_order",
        name="lookup_order",
        arguments={"order_id": "order_001"},
    )

    result = execute_tool_call(
        call, support_agent_context(), trusted_customer_id="customer_001"
    )

    assert result.call_id == "test_order"
    assert result.name == "lookup_order"
    assert result.output == {
        "order_id": "order_001",
        "customer_id": "customer_001",
        "status": "delivered",
        "amount_cents": 12999,
        "currency": "EUR",
        "created_at": "2026-07-20",
    }


def test_lookup_customer_rejects_empty_customer_id():

    with pytest.raises(ValidationError):
        ToolCall(
            call_id="test_invalid",
            name="lookup_customer",
            arguments={"customer_id": ""},
        )


def test_lookup_order_returns_none_for_unknown_order():
    call = ToolCall(
        call_id="test_unknown_order",
        name="lookup_order",
        arguments={"order_id": "order_999"},
    )

    result = execute_tool_call(
        call, support_agent_context(), trusted_customer_id="customer_001"
    )

    assert result.call_id == "test_unknown_order"
    assert result.name == "lookup_order"
    assert result.output is None


def test_lookup_order_rejects_missing_order_id():
    with pytest.raises(ValidationError):
        ToolCall(
            call_id="test_missing_order_id",
            name="lookup_order",
            arguments={},
        )


def test_lookup_order_rejects_wrong_argument_type():
    with pytest.raises(ValidationError):
        ToolCall(
            call_id="test_wrong_type",
            name="lookup_order",
            arguments={"order_id": 12345},
        )


def test_lookup_order_rejects_unexpected_field():
    with pytest.raises(ValidationError):
        ToolCall(
            call_id="test_extra_field",
            name="lookup_order",
            arguments={"order_id": "order_001", "unexpected": "value"},
        )


def test_lookup_order_rejects_order_id_too_long():
    with pytest.raises(ValidationError):
        ToolCall(
            call_id="test_too_long",
            name="lookup_order",
            arguments={"order_id": "x" * 101},
        )


def test_lookup_customer_rejects_missing_customer_id():
    with pytest.raises(ValidationError):
        ToolCall(
            call_id="test_missing_customer_id",
            name="lookup_customer",
            arguments={},
        )


def test_lookup_customer_rejects_wrong_argument_type():
    with pytest.raises(ValidationError):
        ToolCall(
            call_id="test_wrong_type",
            name="lookup_customer",
            arguments={"customer_id": ["customer_001"]},
        )


def test_tool_call_rejects_unknown_tool_name():
    with pytest.raises(ValidationError):
        ToolCall(
            call_id="test_unknown_tool",
            name="refund_order",
            arguments={"order_id": "order_001"},
        )


def test_tool_execution_error_is_returned_as_result(monkeypatch):
    def failing_tool(order_id: str, *, trusted_customer_id=None):
        raise RuntimeError("database connection failed")

    monkeypatch.setattr(TOOL_REGISTRY["lookup_order"], "handler", failing_tool)

    call = ToolCall(
        call_id="test_failing_order",
        name="lookup_order",
        arguments={"order_id": "order_001"},
    )

    result = execute_tool_call(
        call, support_agent_context(), trusted_customer_id="customer_001"
    )

    assert result.call_id == "test_failing_order"
    assert result.name == "lookup_order"
    assert result.output is None
    assert result.error == "Tool 'lookup_order' failed [tool_execution_error]"
    assert "database connection failed" not in result.model_dump_json()