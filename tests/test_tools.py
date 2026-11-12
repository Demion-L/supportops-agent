from supportops_agent.tools import lookup_customer, lookup_order


def test_lookup_customer_returns_customer():
    customer = lookup_customer("customer_001", trusted_customer_id="customer_001")

    assert customer == {
        "customer_id": "customer_001",
        "name": "John",
        "email": "john@example.com",
    }


def test_lookup_order_returns_order():
    order = lookup_order("order_001")

    assert order is not None
    assert order.order_id == "order_001"
    assert order.customer_id == "customer_001"
    assert order.status == "delivered"