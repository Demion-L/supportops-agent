"""Deterministic tools available to SupportOps Agent."""

import hashlib

from supportops_agent import mock_data
from supportops_agent.mock_data import CUSTOMERS, ORDERS
from supportops_agent.models import (
    CasePriority,
    OrderRecord,
    OrderStatus,
    SupportCase,
)

# Публичный контракт lookup_order (виден LLM) задан белым списком: новые поля
# OrderRecord (в т.ч. delivered_at) наружу не попадают, пока их явно не добавят.
ORDER_PUBLIC_FIELDS = ("order_id", "customer_id", "status", "amount_cents", "currency", "created_at")


def serialize_order_for_tool(order: OrderRecord) -> dict[str, object]:
    """Публичное представление заказа для LLM (контракт lookup_order)."""

    full = order.model_dump(mode="json")
    return {name: full[name] for name in ORDER_PUBLIC_FIELDS}


def lookup_order_for_tool(order_id: str) -> dict[str, object] | None:
    """Handler инструмента lookup_order: явная сериализация на границе."""

    order = lookup_order(order_id)
    return None if order is None else serialize_order_for_tool(order)


def lookup_order(order_id: str) -> OrderRecord | None:
    """Return an order by ID, or None when the order does not exist."""

    normalized_order_id = order_id.strip()

    if not normalized_order_id:
        raise ValueError("order_id must not be empty")

    return ORDERS.get(normalized_order_id)

def lookup_customer(customer_id: str) -> dict[str, str] | None:
    """Return a customer by ID, or None when the customer does not exist."""

    normalized_customer_id = customer_id.strip()

    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")

    return CUSTOMERS.get(normalized_customer_id)


def get_order_tracking(order_id: str) -> dict[str, object]:
    """Return shipment tracking for an order as a structured business outcome."""

    order = lookup_order(order_id)

    if order is None:
        return {"outcome": "order_not_found", "order_id": order_id.strip()}

    if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        return {
            "outcome": "no_shipment",
            "order_id": order.order_id,
            "order_status": order.status,
        }

    tracking = mock_data.TRACKING.get(order.order_id)

    if tracking is None:
        return {
            "outcome": "not_shipped",
            "order_id": order.order_id,
            "order_status": order.status,
        }

    return {
        "outcome": "tracking_found",
        "order_id": order.order_id,
        "order_status": order.status,
        **tracking,
    }


def _case_id(ticket_id: str, reason: str) -> str:
    """Deterministic ID for the logical request (ticket + normalized reason)."""

    key = f"{ticket_id}\n{' '.join(reason.split()).casefold()}"
    return "case_" + hashlib.sha256(key.encode()).hexdigest()[:10]


def create_support_case(
    ticket_id: str, reason: str, priority: CasePriority
) -> dict[str, object]:
    """Create a support case, or return the existing one for a retried request.

    A request is the same when ticket_id and the normalized reason match;
    priority is not part of the identity, and a retry never changes it.
    """

    ticket_id = ticket_id.strip()
    reason = reason.strip()
    case_id = _case_id(ticket_id, reason)

    existing = mock_data.CASES.get(case_id)

    if existing is not None:
        return {"outcome": "already_exists", "case": existing.model_dump(mode="json")}

    case = SupportCase(
        case_id=case_id,
        ticket_id=ticket_id,
        reason=reason,
        priority=CasePriority(priority),
    )
    mock_data.CASES[case_id] = case

    return {"outcome": "created", "case": case.model_dump(mode="json")}
