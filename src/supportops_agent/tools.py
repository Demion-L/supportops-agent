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


def _identity_required() -> dict[str, object]:
    """Фиксированный отказ без доверенной identity.

    Не зависит от существования объекта и не содержит данных: безопасен для LLM.
    """

    return {"outcome": "identity_verification_required"}


def _normalize_identity(trusted_customer_id: object) -> str | None:
    """Непустая доверенная identity или None (-> DENY). Источник доверия — вызывающее приложение."""

    if not isinstance(trusted_customer_id, str):
        return None
    return trusted_customer_id.strip() or None


def get_owned_order(order_id: str, trusted_customer_id: str | None) -> OrderRecord | None:
    """Заказ, принадлежащий доверенному клиенту, иначе None.

    Единая точка object-level проверки (SEC-003 / M02). None возвращается без
    различия для: нет identity, несуществующего заказа и чужого заказа — поэтому
    наличие чужого объекта нельзя определить по результату.
    """

    identity = _normalize_identity(trusted_customer_id)
    if identity is None:
        return None

    order = lookup_order(order_id)
    if order is None or order.customer_id != identity:
        return None
    return order


def lookup_order_for_tool(
    order_id: str, *, trusted_customer_id: str | None = None
) -> dict[str, object] | None:
    """Handler инструмента lookup_order: ownership, затем явная сериализация."""

    if _normalize_identity(trusted_customer_id) is None:
        return _identity_required()

    order = get_owned_order(order_id, trusted_customer_id)
    return None if order is None else serialize_order_for_tool(order)


def lookup_order(order_id: str) -> OrderRecord | None:
    """Return an order by ID, or None when the order does not exist."""

    normalized_order_id = order_id.strip()

    if not normalized_order_id:
        raise ValueError("order_id must not be empty")

    return ORDERS.get(normalized_order_id)

def lookup_customer(
    customer_id: str, *, trusted_customer_id: str | None = None
) -> dict[str, object] | None:
    """Return the trusted customer's own profile.

    Чужой и несуществующий профиль -> None (неразличимо). Без доверенной
    identity профиль не читается.
    """

    identity = _normalize_identity(trusted_customer_id)
    if identity is None:
        return _identity_required()

    normalized_customer_id = customer_id.strip()

    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")

    if normalized_customer_id != identity:
        return None

    profile = CUSTOMERS.get(normalized_customer_id)
    return None if profile is None else dict(profile)


def get_order_tracking(
    order_id: str, *, trusted_customer_id: str | None = None
) -> dict[str, object]:
    """Return shipment tracking for the trusted customer's own order.

    Чужой и несуществующий заказ -> order_not_found (неразличимо).
    """

    if _normalize_identity(trusted_customer_id) is None:
        return _identity_required()

    order = get_owned_order(order_id, trusted_customer_id)

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


def _ticket_context_required() -> dict[str, object]:
    """Фиксированный отказ без доверенного тикета (не зависит от CASES и аргументов)."""

    return {"outcome": "ticket_context_required"}


def create_support_case(
    reason: str,
    priority: CasePriority,
    *,
    trusted_ticket_id: str | None = None,
    trusted_customer_id: str | None = None,
) -> dict[str, object]:
    """Create a support case, or return the existing one for a retried request.

    ``trusted_ticket_id`` / ``trusted_customer_id`` come from the application's
    trusted ticket, never from model arguments. Both are checked before CASES
    is touched (customer first). A request is the same when the trusted ticket
    and the normalized reason match; priority is not part of the identity, and
    a retry never changes it.
    """

    if _normalize_identity(trusted_customer_id) is None:
        return _identity_required()

    ticket_id = _normalize_identity(trusted_ticket_id)

    if ticket_id is None:
        return _ticket_context_required()

    reason = reason.strip()
    case_id = _case_id(ticket_id, reason)

    existing = mock_data.CASES.get(case_id)

    if existing is not None:
        if existing.ticket_id != ticket_id:
            # Коллизия усечённого хэша: чужой кейс не раскрывается и не меняется.
            return _ticket_context_required()

        return {"outcome": "already_exists", "case": existing.model_dump(mode="json")}

    case = SupportCase(
        case_id=case_id,
        ticket_id=ticket_id,
        reason=reason,
        priority=CasePriority(priority),
    )
    mock_data.CASES[case_id] = case

    return {"outcome": "created", "case": case.model_dump(mode="json")}
