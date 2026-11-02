"""Deterministic tools available to SupportOps Agent."""

import hashlib
from datetime import date

from supportops_agent import mock_data
from supportops_agent.mock_data import CUSTOMERS, ORDERS
from supportops_agent.models import (
    CasePriority,
    OrderRecord,
    OrderStatus,
    SupportCase,
)

# Business rule: delivered/paid orders can be refunded within this many days
# of the order date (inclusive).
REFUND_WINDOW_DAYS = 30


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


def check_refund_eligibility(
    order_id: str, today: date | None = None
) -> dict[str, object]:
    """Decide whether an order may be refunded. Never performs a refund.

    Rules, checked in order:
      1. refunded   -> not eligible (already_refunded)
      2. cancelled  -> not eligible (order_cancelled)
      3. shipped    -> not eligible (not_delivered_yet)
      4. paid / delivered -> eligible only if at most REFUND_WINDOW_DAYS
         days passed since the order date (otherwise window_expired)
    """

    order = lookup_order(order_id)

    if order is None:
        return {"outcome": "order_not_found", "order_id": order_id.strip()}

    today = today or mock_data.BUSINESS_DATE
    days_since_order = (today - order.created_at).days

    if order.status == OrderStatus.REFUNDED:
        eligible, reason = False, "already_refunded"
    elif order.status == OrderStatus.CANCELLED:
        eligible, reason = False, "order_cancelled"
    elif order.status == OrderStatus.SHIPPED:
        eligible, reason = False, "not_delivered_yet"
    elif days_since_order > REFUND_WINDOW_DAYS:
        eligible, reason = False, "window_expired"
    else:
        eligible, reason = True, "within_refund_window"

    return {
        "outcome": "eligible" if eligible else "not_eligible",
        "order_id": order.order_id,
        "eligible": eligible,
        "reason": reason,
        "order_status": order.status,
        "days_since_order": days_since_order,
        "refund_window_days": REFUND_WINDOW_DAYS,
        "amount_cents": order.amount_cents,
        "currency": order.currency,
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
