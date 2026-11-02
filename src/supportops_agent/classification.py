from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import SupportTicket

CLASSIFIER_MODEL_NAME = "gpt-4.1-mini"

CLASSIFIER_INSTRUCTIONS = """\
You classify customer support messages. You only describe what the customer \
wants and what circumstances they report. You never decide whether a refund, \
exception or any other action is allowed.

Rules:
- A message may contain several intents; list all of them in `intents` and \
choose the most important one as `primary_intent` (it must be in `intents`).
- Intents: order_status (where is my order / delivery state), refund_request \
(wants money back or to return an item), complaint (dissatisfaction with \
service), general_inquiry (general question not about a specific order), \
unknown (cannot be classified reliably).
- If you cannot classify reliably, use ONLY `unknown` and set \
`requires_clarification` to true.
- `entities.order_id`: copy only an order id literally present in the message, \
otherwise null.
- `context.exception_requested`: true if the customer asks for an exception \
to a normal rule (e.g. a return after the deadline because of special \
circumstances).
- `context.claimed_reason`: a short neutral phrase for the circumstance the \
customer CLAIMS (e.g. "hospitalization"), otherwise null. It is an unverified \
claim, not a fact.
- Set `requires_clarification` to true when essential information is missing \
or the request is ambiguous.
"""


class TicketIntent(StrEnum):
    ORDER_STATUS = "order_status"
    REFUND_REQUEST = "refund_request"
    COMPLAINT = "complaint"
    GENERAL_INQUIRY = "general_inquiry"
    UNKNOWN = "unknown"


class ClassificationEntities(BaseModel):
    """Минимальный набор извлекаемых сущностей."""

    model_config = ConfigDict(extra="forbid")

    order_id: str | None = Field(
        description="Order id literally mentioned in the message, or null."
    )


class ClassificationContext(BaseModel):
    """Обстоятельства, о которых сообщает клиент (утверждения, не факты)."""

    model_config = ConfigDict(extra="forbid")

    exception_requested: bool = Field(
        description="True if the customer asks for an exception to a normal rule."
    )
    claimed_reason: str | None = Field(
        description="Short phrase for the circumstance CLAIMED by the customer "
        "(unverified), or null."
    )


class TicketClassification(BaseModel):
    """Результат классификации обращения."""

    model_config = ConfigDict(extra="forbid")

    intents: list[TicketIntent]
    primary_intent: TicketIntent
    entities: ClassificationEntities
    context: ClassificationContext
    requires_clarification: bool

    @model_validator(mode="after")
    def _check_consistency(self) -> "TicketClassification":
        if not self.intents:
            raise ValueError("intents must not be empty")
        if len(set(self.intents)) != len(self.intents):
            raise ValueError("intents must be unique")
        if self.primary_intent not in self.intents:
            raise ValueError("primary_intent must be one of intents")
        # Стратегия для unknown: только единственный intent + уточнение.
        if TicketIntent.UNKNOWN in self.intents:
            if self.intents != [TicketIntent.UNKNOWN]:
                raise ValueError("unknown cannot be combined with other intents")
            if not self.requires_clarification:
                raise ValueError("unknown requires requires_clarification=true")
        return self

    @classmethod
    def fallback(cls) -> "TicketClassification":
        """Контролируемый fallback: «не удалось классифицировать»."""

        return cls(
            intents=[TicketIntent.UNKNOWN],
            primary_intent=TicketIntent.UNKNOWN,
            entities=ClassificationEntities(order_id=None),
            context=ClassificationContext(
                exception_requested=False, claimed_reason=None
            ),
            requires_clarification=True,
        )


@dataclass(frozen=True)
class ClassificationResult:
    """Классификация + метаданные LLM-вызова для execution trace."""

    classification: TicketClassification
    model: str | None = None
    response_id: str | None = None
    usage: dict[str, int] | None = None


class ClassificationError(Exception):
    """Классификация не удалась. `kind` — машинно-читаемая причина."""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


class TicketClassifier(Protocol):
    """Интерфейс классификатора; в тестах подменяется любой реализацией."""

    def classify(self, ticket: SupportTicket) -> ClassificationResult: ...


class OpenAITicketClassifier:
    """LLM-классификатор на Responses API со structured output."""

    def __init__(self, client: OpenAI, model: str = CLASSIFIER_MODEL_NAME):
        self.client = client
        self.model = model

    def classify(self, ticket: SupportTicket) -> ClassificationResult:
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=CLASSIFIER_INSTRUCTIONS,
                input=ticket.message,
                text_format=TicketClassification,
            )
        except ValidationError as exc:
            raise ClassificationError("invalid_output", str(exc)) from exc
        except OpenAIError as exc:
            # Только ошибки SDK/API; остальные исключения — программные дефекты
            # и не должны маскироваться под ожидаемую ошибку классификации.
            raise ClassificationError(
                "api_error", f"{type(exc).__name__}: {exc}"
            ) from exc

        parsed = getattr(response, "output_parsed", None)
        if not isinstance(parsed, TicketClassification):
            raise ClassificationError(
                "invalid_output", "model returned no parsable classification"
            )

        usage = getattr(response, "usage", None)
        return ClassificationResult(
            classification=parsed,
            model=self.model,
            response_id=getattr(response, "id", None),
            usage=None
            if usage is None
            else {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
            },
        )


def format_classification_context(classification: TicketClassification) -> str:
    """Текст для обогащения контекста агента. Только справка, не ограничение."""

    lines = [
        "Preliminary ticket classification (advisory; you may use any tools):",
        f"- intents: {', '.join(i.value for i in classification.intents)}",
        f"- primary intent: {classification.primary_intent.value}",
    ]
    if classification.entities.order_id:
        lines.append(f"- order id mentioned: {classification.entities.order_id}")
    if classification.context.exception_requested:
        lines.append("- customer requests an exception to a normal rule")
    if classification.context.claimed_reason:
        lines.append(
            f"- customer CLAIMS (unverified): {classification.context.claimed_reason}"
        )
    if classification.requires_clarification:
        lines.append("- clarification from the customer may be needed")
    return "\n".join(lines)
