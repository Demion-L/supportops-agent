"""Deterministic tools available to SupportOps Agent."""

from datetime import date

from supportops_agent.models import OrderRecord, OrderStatus


_ORDERS: dict[str, OrderRecord] = {
    "order_001": OrderRecord(
        order_id="order_001",
        customer_id="customer_001",
        status=OrderStatus.DELIVERED,
        amount_cents=12999,
        currency="EUR",
        created_at=date(2026, 7, 20),
    ),
    "order_002": OrderRecord(
        order_id="order_002",
        customer_id="customer_002",
        status=OrderStatus.SHIPPED,
        amount_cents=4999,
        currency="EUR",
        created_at=date(2026, 8, 5),
    ),
}

CUSTOMERS = {
    "customer_001": {
        "customer_id": "customer_001",
        "name": "John",
        "email": "john@example.com",
    },
    "customer_002": {
        "customer_id": "customer_002",
        "name": "Alice",
        "email": "alice@example.com",
    },
}


def lookup_order(order_id: str) -> OrderRecord | None:
    """Return an order by ID, or None when the order does not exist."""

    normalized_order_id = order_id.strip()

    if not normalized_order_id:
        raise ValueError("order_id must not be empty")

    return _ORDERS.get(normalized_order_id)

def lookup_customer(customer_id: str) -> dict[str, str] | None:
    """Return a customer by ID, or None when the customer does not exist."""

    normalized_customer_id = customer_id.strip()

    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")

    return CUSTOMERS.get(normalized_customer_id)