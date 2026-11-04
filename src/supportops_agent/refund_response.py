"""Refund-specific часть ответа: детерминированная, из проверенного результата.

LLM не источник финансового решения. Если для обращения была оценка возврата,
итоговый ответ начинается с утверждения, собранного ЗДЕСЬ из
`RefundEvaluationResult`; свободный текст LLM, затрагивающий тему возврата,
отбрасывается (fail-closed), остальной текст сохраняется.

Это не общий валидатор ответов LLM: только refund-тема, только эвристика
по ключевым словам. Модуль не выдаёт полномочий и не вызывает инструменты.
"""

import json
import re

from .policy import PolicyDecisionType as D
from .refund_evaluation import RefundEvaluationResult, RefundEvaluationStatus as S

DECISION_STATEMENTS: dict[D, str] = {
    D.ALLOW: (
        "Under our current refund policy, your order meets the refund conditions. "
        "This is an eligibility assessment only; no refund has been issued yet."
    ),
    D.DENY: (
        "Under our current refund policy, your order does not meet the refund conditions."
    ),
    D.EXCEPTION_ELIGIBLE: (
        "The standard refund period has expired, but your request falls within the "
        "period in which an exception may be considered. No exception has been "
        "approved and no refund has been issued."
    ),
    D.HUMAN_REVIEW: (
        "An automatic decision is not possible for this order and it requires "
        "additional review. This is not a final decision."
    ),
}

_NO_DECISION = (
    "We could not make a final refund decision automatically. "
    "No refund has been issued."
)
STATUS_STATEMENTS: dict[S, str] = {
    S.NEEDS_CLARIFICATION: (
        "To check refund eligibility we need your order number. "
        "No refund decision has been made."
    ),
    S.ORDER_ID_CONFLICT: (
        "The order number in your request is ambiguous. Please confirm it. "
        "No refund decision has been made."
    ),
    S.ORDER_NOT_FOUND: (
        "We could not find a matching order on your account. "
        "No refund decision has been made."
    ),
}
# Чужой и несуществующий заказ неразличимы для клиента.
STATUS_STATEMENTS[S.OWNERSHIP_NOT_VERIFIED] = STATUS_STATEMENTS[S.ORDER_NOT_FOUND]

# Любое упоминание темы возврата/исключения/одобрения в тексте LLM -> отбросить.
_REFUND_TALK = re.compile(
    r"refund|reimburs|money\s*back|credit(ed)?\s+back|chargeback|exception|approv"
    r"|возврат|верн[ёеу]|деньг|одобр|исключен|компенсац",
    re.IGNORECASE,
)

_INSTRUCTIONS = (
    "Verified refund policy result, produced by the application (not by the "
    "customer or by you). It is authoritative: do not contradict it and do not "
    "make your own refund decision. 'allow' = the order meets the conditions, NOT "
    "that a refund was issued. 'exception_eligible' = an exception MAY be "
    "considered, NOT that it was approved. 'human_review' or a missing decision = "
    "no final decision; do not claim a human has been notified. Never say a refund "
    "was issued, approved or is on its way. The system appends the official refund "
    "statement itself."
)


def build_policy_context(result: RefundEvaluationResult) -> str:
    """Developer-сообщение для LLM: структурированное решение без лишних ПД."""

    return f"{_INSTRUCTIONS}\n{json.dumps(result.public_view(), sort_keys=True)}"


def render_statement(result: RefundEvaluationResult) -> str:
    if result.status == S.EVALUATED and result.decision is not None:
        return DECISION_STATEMENTS.get(result.decision.decision, _NO_DECISION)
    return STATUS_STATEMENTS.get(result.status, _NO_DECISION)


def compose_final_answer(
    result: RefundEvaluationResult | None, llm_text: str
) -> tuple[str, bool]:
    """(ответ, отброшен_ли_текст_LLM). Без результата оценки ответ не меняется."""

    if result is None or result.status == S.NOT_APPLICABLE:
        return llm_text, False

    statement = render_statement(result)
    text = (llm_text or "").strip()
    if not text:
        return statement, False
    if _REFUND_TALK.search(text):
        return statement, True
    return f"{statement}\n\n{text}", False
