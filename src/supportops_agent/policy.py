"""Deterministic Business Policy Engine (refund eligibility).

Отвечает на вопрос «что разрешают бизнес-правила на основании подтверждённых
фактов». Он НЕ вызывает LLM/БД/сеть/инструменты, НЕ меняет состояние и НЕ
выдаёт полномочий: `PolicyDecision` не является решением Authorization Gate.
"""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .models import OrderStatus


class PolicyDecisionType(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    # Только «может быть рассмотрено как goodwill-исключение». НЕ одобрение.
    EXCEPTION_ELIGIBLE = "exception_eligible"
    HUMAN_REVIEW = "human_review"


class VerifiedRefundFacts(BaseModel):
    """Подтверждённые бизнес-факты (из доверенных источников, не от клиента).

    Сюда нельзя помещать данные, извлечённые LLM из текста клиента,
    в том числе `claimed_reason`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str = Field(min_length=1, max_length=100)
    # str, а не OrderStatus: некорректный статус должен быть представим
    # и безопасно обработан движком (а не падать при построении фактов).
    order_status: str
    delivered_at: date | None = None
    # Происхождение фактов: поле -> источник (напр. "OrderRecord.status").
    # Информационное; движок его не читает и решение от него не зависит.
    provenance: dict[str, str] = Field(default_factory=dict)


class RefundPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    standard_window_days: int = Field(default=40, ge=0)
    grace_period_days: int = Field(default=5, ge=0)
    policy_version: str = Field(default="refund-policy-v1", min_length=1)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: PolicyDecisionType
    reason_code: str
    policy_version: str
    explanation: str
    days_since_delivery: int | None = None


class RefundPolicyEngine:
    """Чистая функция (facts, today, config) -> PolicyDecision.

    Отвечает только на вопрос «соответствует ли возврат этого заказа правилам».
    Выбор сценария (нужна ли вообще оценка возврата) — ответственность
    orchestration layer, а не движка.
    """

    def __init__(self, config: RefundPolicyConfig | None = None):
        self.config = config or RefundPolicyConfig()

    def _decide(
        self,
        decision: PolicyDecisionType,
        reason_code: str,
        explanation: str,
        days: int | None = None,
    ) -> PolicyDecision:
        return PolicyDecision(
            decision=decision,
            reason_code=reason_code,
            policy_version=self.config.policy_version,
            explanation=explanation,
            days_since_delivery=days,
        )

    def evaluate(
        self,
        facts: VerifiedRefundFacts,
        today: date,
    ) -> PolicyDecision:
        # datetime — подкласс date; он сломал бы календарную арифметику.
        if isinstance(today, datetime) or not isinstance(today, date):
            raise TypeError("today must be a datetime.date (not datetime)")

        H = PolicyDecisionType.HUMAN_REVIEW

        try:
            status = OrderStatus(facts.order_status)
        except ValueError:
            return self._decide(
                H,
                "unknown_order_status",
                "Order status is not recognised; automatic decision impossible.",
            )

        if status == OrderStatus.REFUNDED:
            return self._decide(
                PolicyDecisionType.DENY, "already_refunded", "Order is already refunded."
            )
        if status == OrderStatus.CANCELLED:
            return self._decide(
                PolicyDecisionType.DENY, "order_cancelled", "Order is cancelled."
            )
        if status != OrderStatus.DELIVERED:
            # paid / shipped: доставка не подтверждена, срок не оценивается,
            # а created_at не подменяет дату доставки.
            return self._decide(
                PolicyDecisionType.DENY,
                "not_delivered_yet",
                "Order is not delivered yet; the refund window has not started.",
            )

        if facts.delivered_at is None:
            return self._decide(
                H,
                "missing_delivery_date",
                "Delivered order without a verified delivery date; "
                "automatic decision impossible.",
            )

        days = (today - facts.delivered_at).days
        if days < 0:
            return self._decide(
                H,
                "delivery_date_in_future",
                "Verified delivery date is later than today; data is inconsistent.",
                days,
            )

        standard = self.config.standard_window_days
        grace_end = standard + self.config.grace_period_days
        if days <= standard:
            return self._decide(
                PolicyDecisionType.ALLOW,
                "within_standard_window",
                f"{days} days since delivery, standard window is {standard} days.",
                days,
            )
        if days <= grace_end:
            return self._decide(
                PolicyDecisionType.EXCEPTION_ELIGIBLE,
                "within_grace_period",
                f"{days} days since delivery; outside the {standard}-day window but "
                f"within the {grace_end}-day goodwill period. May be considered "
                "for an exception; NOT approved.",
                days,
            )
        return self._decide(
            PolicyDecisionType.DENY,
            "outside_grace_period",
            f"{days} days since delivery exceeds the {grace_end}-day limit.",
            days,
        )
