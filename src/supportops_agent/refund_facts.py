"""Verified Facts Provider: единственный доверенный путь к VerifiedRefundFacts.

Читает ТОЛЬКО существующий источник заказов (`tools.lookup_order` -> ORDERS).
Не вызывает LLM, не читает текст обращения / claimed_reason /
exception_requested, не меняет бизнес-состояние и не использует
TRACKING.last_update или created_at как дату доставки.

Граница доверия: между кодом приложения и входами LLM/клиента. Объект фактов
создаётся только здесь; orchestration не принимает факты извне.
"""

from . import tools
from .policy import VerifiedRefundFacts


class FactsProviderError(Exception):
    """Базовая ошибка провайдера. `code` — безопасный машинный код."""

    code = "facts_unavailable"

    def __init__(self, code: str | None = None):
        # Намеренно без текста внутренних исключений.
        super().__init__(code or self.code)
        self.code = code or self.code


class OrderNotFoundError(FactsProviderError):
    code = "order_not_found"


class VerifiedRefundFactsProvider:
    def get_order_customer_id(self, order_id: str) -> str:
        """Владелец заказа по доверенным данным (для проверки ownership)."""

        return self._order(order_id).customer_id

    def get_refund_facts(self, order_id: str) -> VerifiedRefundFacts:
        order = self._order(order_id)

        provenance = {
            "order_status": "OrderRecord.status",
            "delivered_at": (
                "OrderRecord.delivered_at"
                if order.delivered_at is not None
                else "OrderRecord.delivered_at (absent)"
            ),
        }
        return VerifiedRefundFacts(
            order_id=order.order_id,
            order_status=order.status.value,
            delivered_at=order.delivered_at,
            provenance=provenance,
        )

    @staticmethod
    def _order(order_id: str):
        try:
            order = tools.lookup_order(order_id)
        except ValueError:  # пустой идентификатор
            raise OrderNotFoundError() from None
        if order is None:
            raise OrderNotFoundError()
        return order
