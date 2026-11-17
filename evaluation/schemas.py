"""Схемы сценария, ожидаемого исхода и результата оценки.

Принцип: ожидаемый исход описывает РЕЗУЛЬТАТ (outcome, ответ, побочные эффекты,
выбор инструментов), а не факт «прогон завершился без исключения».
Каждое поле `Expected` необязательно: не заданное поле не проверяется и не
входит в знаменатель соответствующей метрики (см. metrics.py).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Category(StrEnum):
    ORDER_TRACKING = "order_tracking"
    CUSTOMER_LOOKUP = "customer_lookup"
    REFUND_ELIGIBILITY = "refund_eligibility"
    SUPPORT_CASE_CREATION = "support_case_creation"
    CLASSIFICATION_FAILURE = "classification_failure"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    MISSING_TRUSTED_IDENTITY = "missing_trusted_identity"
    UNAUTHORIZED_OBJECT_ACCESS = "unauthorized_object_access"
    STALE_CONTEXT = "stale_context"
    UNEXPECTED_TOOL_EXCEPTION = "unexpected_tool_exception"


class Outcome(StrEnum):
    """Три НЕсмешиваемых исхода прогона.

    FAILED   — run() выбросил исключение / AgentRun.status == FAILED.
    DEGRADED — AgentRun.status == COMPLETED и summary()["degraded"] (fail-closed).
    COMPLETED — AgentRun.status == COMPLETED, не degraded.
    """

    COMPLETED = "completed"
    DEGRADED = "degraded"
    FAILED = "failed"


ValidationStatus = Literal["valid", "invalid_json", "invalid_schema"]


# --- входные данные сценария (всё синтетическое) -------------------------------
class ScenarioTicket(_Strict):
    ticket_id: str
    customer_id: str
    message: str
    order_id: str | None = None


class ClassifierScript(_Strict):
    """Воспроизводимый ответ классификатора (replay), а не вызов LLM."""

    mode: Literal["result", "expected_error", "crash"]
    result: dict | None = None  # payload TicketClassification (mode=result)
    error_kind: Literal["invalid_output", "api_error"] | None = None

    @model_validator(mode="after")
    def _check(self) -> "ClassifierScript":
        if self.mode == "result" and self.result is None:
            raise ValueError("mode=result requires result")
        if self.mode == "expected_error" and self.error_kind is None:
            raise ValueError("mode=expected_error requires error_kind")
        return self


class ToolCallScript(_Strict):
    name: str
    arguments: dict | None = None
    raw_arguments: str | None = None  # для намеренно невалидного JSON

    @model_validator(mode="after")
    def _check(self) -> "ToolCallScript":
        if (self.arguments is None) == (self.raw_arguments is None):
            raise ValueError("exactly one of arguments / raw_arguments is required")
        return self


class LLMTurn(_Strict):
    """Один ответ fake LLM: вызовы инструментов, либо финальный текст, либо сбой."""

    tool_calls: list[ToolCallScript] = Field(default_factory=list)
    text: str = ""
    raise_error: bool = False


class ToolFault(_Strict):
    """Контролируемое исключение в обработчике инструмента (текст содержит секрет)."""

    tool: str
    exception: Literal["RuntimeError", "ValueError"]
    secret: str


class Probe(_Strict):
    """Прямой вызов публичного execute_function_call вне run() (stale context)."""

    when: Literal["before_run", "after_run"]
    tool: str
    arguments: dict


# --- ожидаемый исход -----------------------------------------------------------
class ExpectedClassification(_Strict):
    primary_intent: str | None = None
    intents: list[str] | None = None
    requires_clarification: bool | None = None
    exception_requested: bool | None = None

    def specified(self) -> bool:
        return any(v is not None for v in self.model_dump().values())


class ExpectedToolCall(_Strict):
    name: str
    validation_status: ValidationStatus = "valid"
    executed: bool | None = None
    # Ожидаемый код отказа Authorization Gate (напр. MISSING_CONTEXT).
    reason_code: str | None = None
    # Только для вызовов, дошедших до обработчика; без trusted_* ключей.
    arguments: dict | None = None

    @model_validator(mode="after")
    def _arguments_need_execution(self) -> "ExpectedToolCall":
        if self.arguments is not None and self.executed is not True:
            raise ValueError("expected arguments require executed=true")
        return self


class ExpectedTools(_Strict):
    """Ожидаемая последовательность ПОПЫТОК вызова (в run()), по порядку."""

    calls: list[ExpectedToolCall] = Field(default_factory=list)
    # Инструменты, обработчик которых не должен быть достигнут ни при каких условиях.
    forbidden: list[str] = Field(default_factory=list)


class Expected(_Strict):
    outcome: Outcome | None = None
    classification: ExpectedClassification | None = None
    tools: ExpectedTools | None = None
    answer_contains: list[str] = Field(default_factory=list)
    answer_not_contains: list[str] = Field(default_factory=list)
    cases_created: int | None = None
    failed_tool_calls: int | None = None
    exception_type: str | None = None
    # Поле "outcome" ToolResult.output для каждой пробы, по порядку.
    probe_outcomes: list[str] | None = None


class Scenario(_Strict):
    id: str = Field(pattern=r"^[a-z]{2,4}-\d{3}$")
    category: Category
    description: str
    ticket: ScenarioTicket
    # None -> классификация отключена (как SupportAgent(classifier=None)).
    classifier: ClassifierScript | None = None
    refund_evaluation: bool = False
    trusted_context: bool = True
    llm_script: list[LLMTurn] = Field(default_factory=list)
    tool_faults: list[ToolFault] = Field(default_factory=list)
    probes: list[Probe] = Field(default_factory=list)
    secret_canaries: list[str] = Field(default_factory=list)
    expected: Expected = Field(default_factory=Expected)


class Dataset(_Strict):
    schema_version: str
    dataset_id: str
    scenarios: list[Scenario]

    @model_validator(mode="after")
    def _check(self) -> "Dataset":
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
        ids = [s.id for s in self.scenarios]
        if len(set(ids)) != len(ids):
            raise ValueError("scenario ids must be unique")
        return self


# --- результат -----------------------------------------------------------------
class Mismatch(_Strict):
    dimension: str
    expected: str
    observed: str


class ObservedCall(_Strict):
    name: str
    validation_status: ValidationStatus | None
    executed: bool
    error_type: str | None = None
    reason_code: str | None = None


class ScenarioResult(_Strict):
    scenario_id: str
    category: Category
    # Если harness не смог выполнить сценарий — только harness_error заполнен.
    harness_error: str | None = None
    outcome: Outcome | None = None
    exception_type: str | None = None
    observed_classification: dict | None = None
    observed_calls: list[ObservedCall] = Field(default_factory=list)
    cases_created: int = 0
    failed_tool_calls: int = 0
    llm_calls: int = 0
    mismatches: list[Mismatch] = Field(default_factory=list)
    security_violations: list[str] = Field(default_factory=list)
    # Применимость и результат по метрикам (None = не применимо к сценарию).
    classification_correct: bool | None = None
    tool_selection_correct: bool | None = None
    argument_calls_total: int = 0
    argument_calls_matched: int = 0
    task_success: bool | None = None
