"""Закрытые словари. Через границу Commons проходят только токены из этих множеств.

Это главный механизм S3/S4: нет свободного текста, нет произвольных строк, нет
исполняемых инструкций — только перечисления.
"""
from __future__ import annotations

from enum import Enum


class ActionId(str, Enum):
    """Закрытый набор идентификаторов действий. Это метки, а не код."""

    REQUEST_CARRIER_STATUS = "request_carrier_status"
    REQUEST_ORDER_DETAILS = "request_order_details"
    REISSUE_INVOICE = "reissue_invoice"
    ISSUE_CREDIT_NOTE = "issue_credit_note"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    SEND_APOLOGY = "send_apology"
    OFFER_GOODWILL_CREDIT = "offer_goodwill_credit"
    # Намеренно вредные метки: присутствуют в словаре, чтобы локальная политика
    # могла их отклонить, а не чтобы Commons их «не знал».
    REFUND_WITHOUT_VERIFICATION = "refund_without_verification"
    GRANT_ADMIN_OVERRIDE = "grant_admin_override"


class ProblemType(str, Enum):
    DELIVERY_DELAY = "delivery_delay"
    BILLING_ERROR = "billing_error"
    REPEAT_CONTACT = "repeat_contact"
    REFUND_DISPUTE = "refund_dispute"


# Допустимые ключи и значения контекста (публичные признаки, не ПДн).
CONTEXT_VOCAB: dict[str, frozenset[str]] = {
    "order_status": frozenset({"processing", "shipped", "delivered"}),
    "invoice_issue": frozenset({"duplicate_charge", "amount_mismatch"}),
    "contact_count": frozenset({"2", "3plus"}),
    "dispute_type": frozenset({"unverified_claim", "verified_claim"}),
}

# Допустимые причины отклонения Application.
REJECT_REASONS = frozenset(
    {"not_applicable", "stale_version", "revoked", "denied_by_local_policy"}
)
