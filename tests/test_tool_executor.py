from supportops_agent.tool_executor import ToolCall, execute_tool_call


def test_execute_lookup_customer():
    call = ToolCall(
        call_id="test_customer",
        name="lookup_customer",
        arguments={"customer_id": "customer_001"},
    )

    result = execute_tool_call(call)

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

    result = execute_tool_call(call)

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