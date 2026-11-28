"""Refund Evaluation orchestration.

LLM предлагает (intent, order_id) -> доверенные данные устанавливают факты ->
детерминированная политика решает. Результат — только информация для trace:
он НЕ выдаёт полномочий, не меняет ExecutionContext/allowed_tools, не вызывает
ToolExecutor и не совершает финансовых действий.

Метод принимает ТОЛЬКО тикет и классификацию — готовые факты передать нельзя.
`order_id` (из тикета или от LLM) считается недоверенным идентификатором.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from supportops_agent.classification import TicketClassification, TicketIntent
from supportops_agent.models import SupportTicket
from supportops_agent.business.policy import PolicyDecision, RefundPolicyEngine
from supportops_agent.business.refund_facts import (
    FactsProviderError,
    OrderNotFoundError,
    VerifiedRefundFactsProvider,
)


class RefundEvaluationStatus(StrEnum):
    EVALUATED = "evaluated"
    NOT_APPLICABLE = "not_applicable"  # нет refund intent / нет классификации
    NEEDS_CLARIFICATION = "needs_clarification"  # нет order_id / неясный запрос
    ORDER_ID_CONFLICT = "order_id_conflict"
    ORDER_NOT_FOUND = "order_not_found"
    OWNERSHIP_NOT_VERIFIED = "ownership_not_verified"
    FACTS_UNAVAILABLE = "facts_unavailable"
    POLICY_ERROR = "policy_error"


class RefundEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: RefundEvaluationStatus
    reason_code: str
    # Только для верифицированного (существующего и принадлежащего клиенту) заказа.
    order_id: str | None = None
    decision: PolicyDecision | None = None
    provenance: dict[str, str] | None = None

    def public_view(self) -> dict[str, object]:
        """Минимум для LLM/клиента: без order_id, provenance и explanation.

        «Заказ не найден» и «заказ чужой» неразличимы (нет раскрытия чужих заказов).
        """

        S = RefundEvaluationStatus
        if self.status in (S.ORDER_NOT_FOUND, S.OWNERSHIP_NOT_VERIFIED):
            return {"status": "order_not_verified", "reason_code": "order_not_verified"}

        view: dict[str, object] = {
            "status": self.status.value,
            "reason_code": self.reason_code,
        }
        if self.decision is not None:
            view.update(
                policy_version=self.decision.policy_version,
                decision=self.decision.decision.value,
                days_since_delivery=self.decision.days_since_delivery,
            )
        return view

    @property
    def authorizes_action(self) -> bool:
        """Всегда False: оценка политики не является Authorization-решением."""
        return False


class RefundEvaluationOrchestrator:
    def __init__(
        self,
        provider: VerifiedRefundFactsProvider,
        engine: RefundPolicyEngine,
        today: date,
    ):
        self.provider = provider
        self.engine = engine
        self.today = today

    def should_evaluate(self, classification: TicketClassification | None) -> bool:
        return (
            classification is not None
            and TicketIntent.REFUND_REQUEST in classification.intents
        )

    def evaluate(
        self, ticket: SupportTicket, classification: TicketClassification | None
    ) -> RefundEvaluationResult:
        S = RefundEvaluationStatus

        def out(status, code, **kw):
            return RefundEvaluationResult(status=status, reason_code=code, **kw)

        # Сбой классификации / нерелевантный intent: полномочия не повышаются.
        if not self.should_evaluate(classification):
            return out(S.NOT_APPLICABLE, "not_a_refund_request")
        if classification.requires_clarification:
            return out(S.NEEDS_CLARIFICATION, "classification_requires_clarification")

        # Оба источника order_id недоверенные; при расхождении — не угадываем.
        ticket_oid = ticket.order_id.strip() if ticket.order_id else None
        llm_oid = (classification.entities.order_id or "").strip() or None
        if ticket_oid and llm_oid and ticket_oid != llm_oid:
            return out(S.ORDER_ID_CONFLICT, "order_id_conflict")
        order_id = ticket_oid or llm_oid
        if not order_id:
            return out(S.NEEDS_CLARIFICATION, "missing_order_id")

        return self.evaluate_order(order_id, ticket.customer_id)

    def evaluate_order(self, order_id: str, customer_id: str) -> RefundEvaluationResult:
        """Единая цепочка: ownership -> проверенные факты -> RefundPolicyEngine.

        `customer_id` ДОЛЖЕН быть доверенной идентичностью, заданной
        приложением (не текстом клиента, не выводом LLM, не аргументом tool call).
        """

        S = RefundEvaluationStatus

        def out(status, code, **kw):
            return RefundEvaluationResult(status=status, reason_code=code, **kw)

        try:
            owner = self.provider.get_order_customer_id(order_id)
            # Не раскрываем существование чужого заказа: без facts и decision.
            if owner != customer_id:
                return out(S.OWNERSHIP_NOT_VERIFIED, "order_not_owned_by_customer")
            facts = self.provider.get_refund_facts(order_id)
        except OrderNotFoundError:
            return out(S.ORDER_NOT_FOUND, "order_not_found")
        except FactsProviderError:
            return out(S.FACTS_UNAVAILABLE, "facts_unavailable")
        except Exception:  # любой сбой провайдера — не ALLOW, без текста ошибки
            return out(S.FACTS_UNAVAILABLE, "facts_unavailable")

        try:
            decision = self.engine.evaluate(facts=facts, today=self.today)
        except Exception:
            return out(S.POLICY_ERROR, "policy_engine_error", order_id=facts.order_id)

        return out(
            S.EVALUATED,
            decision.reason_code,
            order_id=facts.order_id,
            decision=decision,
            provenance=dict(facts.provenance),
        )


def default_refund_evaluator(today: date | None = None) -> RefundEvaluationOrchestrator:
    """Композиция по умолчанию (доверенные зависимости, фиксированная бизнес-дата)."""

    from supportops_agent.business import mock_data

    return RefundEvaluationOrchestrator(
        VerifiedRefundFactsProvider(),
        RefundPolicyEngine(),
        today or mock_data.BUSINESS_DATE,
    )


def check_refund_eligibility(
    order_id: str,
    *,
    trusted_customer_id: str | None = None,
    today: date | None = None,
) -> dict[str, object]:
    """Handler инструмента check_refund_eligibility. Только оценка, не возврат.

    Правил возврата здесь нет: решает RefundPolicyEngine. `trusted_customer_id`
    подставляет ToolExecutor из доверенного контекста приложения; в аргументах
    LLM такого поля нет. Без него заказ не раскрывается и не оценивается.
    """

    if not trusted_customer_id:
        return {"outcome": "identity_verification_required"}

    result = default_refund_evaluator(today).evaluate_order(
        order_id.strip(), trusted_customer_id
    )
    view = result.public_view()
    return {
        "outcome": view.pop("status"),
        **view,
        "note": "Policy assessment only. No refund has been issued or approved.",
    }
