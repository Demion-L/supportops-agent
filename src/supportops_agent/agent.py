import json
import threading
from datetime import UTC, datetime

from openai import OpenAI
from pydantic import ValidationError

from .classification import (
    ClassificationError,
    ClassificationResult,
    TicketClassification,
    TicketClassifier,
    format_classification_context,
)
from .agent_state import AgentState
from .authorization import (
    DEFAULT_POLICY,
    ExecutionContext,
    ToolAuthorizationPolicy,
)
from .models import AgentRun, AgentRunStatus, AgentStepType, SupportTicket
from .refund_evaluation import (
    RefundEvaluationOrchestrator,
    RefundEvaluationResult,
    RefundEvaluationStatus,
)
from .refund_response import build_policy_context, compose_final_answer
from .run_manager import AgentRunManager
from .tool_executor import (
    INVALID_ARGUMENTS_CODE,
    TOOL_REGISTRY,
    ToolCall,
    ToolResult,
    execute_tool_call,
)
from supportops_agent.tool_definitions import get_llm_tool_schemas


MAX_AGENT_STEPS = 10
MODEL_NAME = "gpt-4.1-mini"

# Fail-closed ответ при ожидаемой ошибке классификации (M03-D3): фиксированный,
# не генерируется LLM, не зависит от тикета и не содержит диагностики.
CLASSIFICATION_FAILURE_RESPONSE = (
    "We're unable to process your request right now. "
    "Please try again later or contact support."
)

# Допустимые значения ClassificationError.kind в trace (F-1); прочее -> "unknown".
_SAFE_CLASSIFICATION_KINDS = frozenset({"invalid_output", "api_error"})



class ConcurrentRunError(RuntimeError):
    """Второй run() на экземпляре, у которого уже есть активный прогон.

    Сообщение статичное: ни ticket, ни customer, ни текст обращения в него
    не попадают.
    """


def _failure_code(exc: BaseException) -> str:
    """Детерминированная безопасная категория сбоя прогона (R1, P3).

    Закрытая таблица по isinstance; текст и имя класса исключения наружу не
    идут. validation_error — только pydantic ValidationError (в т.ч. неизвестный
    инструмент из ToolCall) и JSONDecodeError; внутренний ValueError этим кодом
    не маркируется. Прочие Exception — agent_execution_error; не-Exception —
    unknown_error. Код только диагностический: на статус, re-raise, авторизацию
    и ответ клиенту не влияет.
    """

    if isinstance(exc, ClassificationError):
        return "classification_error"
    if isinstance(exc, (ValidationError, json.JSONDecodeError)):
        return "validation_error"
    if isinstance(exc, Exception):
        return "agent_execution_error"
    return "unknown_error"


class SupportAgent:
    def __init__(
        self,
        client: OpenAI,
        classifier: TicketClassifier | None = None,
        *,
        context: ExecutionContext | None = None,
        policy: ToolAuthorizationPolicy | None = DEFAULT_POLICY,
        refund_evaluator: RefundEvaluationOrchestrator | None = None,
    ):
        """`classifier=None` отключает классификацию (поведение как раньше).

        Граница доверия: `ticket.customer_id` считается подтверждённой
        идентичностью — его обязано установить приложение из аутентифицированной
        сессии. Текст обращения, вывод LLM и аргументы tool call идентичность
        не задают и не меняют (см. `execute_function_call`).

        `context` / `policy` — доверенные полномочия, задаются только кодом
        приложения (никогда из ответа модели). Неявных
        полномочий нет: без явно переданного `context` любой вызов бизнес-
        инструмента отклоняется Authorization Gate (MISSING_CONTEXT).
        """

        self.client = client
        self.classifier = classifier
        self.context = context
        self.policy = policy
        # Опционально: None -> оценка возврата отключена (поведение как раньше).
        # Результат идёт только в trace; не влияет на context/policy/tools.
        self.refund_evaluator = refund_evaluator
        self.run_manager: AgentRunManager | None = None
        self.refund_result: RefundEvaluationResult | None = None
        self.state: AgentState | None = None
        # Trusted context действителен только внутри run(). `state` сохраняется
        # для диагностики, но после завершения не даёт полномочий (SR-1).
        self._run_active = False
        # Один экземпляр — один активный run(). Lock (не RLock): повторный вход
        # из того же потока тоже отклоняется. Захват неблокирующий.
        self._run_lock = threading.Lock()
        # Поток, которому принадлежит активный прогон. Trusted context и запись
        # в trace доступны только ему; вне активного прогона None. Это ЕДИНСТВЕННЫЙ
        # источник решения о принадлежности (`_run_active` — только диагностика):
        # run() выставляет его из потока-владельца и сбрасывает из него же, поэтому
        # `owner == текущий поток` означает, что поток сейчас внутри run() и run не
        # может завершиться, пока этот же поток исполняет вызов.
        self._run_owner: int | None = None
        # Внутренний признак закрытия trace, независимый от `run.status`: run()
        # ставит его на менеджер завершённого прогона ДО попытки запечатать trace.
        # Если и fail(), и _seal_trace() отказали (status остался RUNNING), запись
        # через публичные execution-методы всё равно закрыта. Это не публичный
        # статус и не поле схемы trace.
        self._closed_manager: AgentRunManager | None = None

    def _trusted_state(self) -> AgentState | None:
        """Trusted context активного прогона — только его потоку-владельцу.

        Владелец читается ОДИН раз (атомарный снимок), решение принимается до
        обращения к `self.state`. Вне прогона (owner is None) и для чужого потока
        возвращается None -> fail-closed.
        """

        owner = self._run_owner
        if owner is None or owner != threading.get_ident():
            return None

        return self.state

    @property
    def last_run(self) -> AgentRun | None:
        """Return the most recent agent run."""

        if self.run_manager is None:
            return None

        return self.run_manager.run    

    @staticmethod
    def get_function_calls(response) -> list:
        """Extract function calls from an OpenAI response."""
        return [
            item for item in response.output
            if item.type == "function_call"
        ]

    def execute_function_call(self, item) -> ToolResult:

        try:
            tool_call = ToolCall(
                call_id=item.call_id,
                name=item.name,
                arguments=json.loads(item.arguments),
            )
        except (json.JSONDecodeError, ValidationError):
            if item.name not in TOOL_REGISTRY:
                raise

            # Текст ValidationError содержит значения аргументов от LLM: наружу
            # (LLM, trace) идёт только фиксированный код. item.name — ключ реестра.
            return ToolResult(
                call_id=item.call_id,
                name=item.name,
                error=(
                    f"Invalid arguments for tool '{item.name}' "
                    f"[{INVALID_ARGUMENTS_CODE}]"
                ),
                error_type=INVALID_ARGUMENTS_CODE,
            )

        # Идентичность берётся из доверенного тикета активного прогона, не из
        # аргументов модели. Вне run() (до первого, после COMPLETED/FAILED/
        # degraded) устаревший state полномочий не даёт -> fail-closed.
        # Во время активного прогона полномочия получает только его поток-владелец:
        # вызов из другого потока fail-closed.
        state = self._trusted_state()
        return execute_tool_call(
            tool_call,
            self.context,
            self.policy,
            trusted_customer_id=state.ticket.customer_id if state else None,
            trusted_ticket_id=state.ticket.ticket_id if state else None,
        )

    def execute_function_calls(self, function_calls: list) -> list[dict[str, str]]:

        tool_outputs = []

        for item in function_calls:
            started_at = datetime.now(UTC)
            tool_result = self.execute_function_call(item)
            finished_at = datetime.now(UTC)
            duration_ms = (finished_at - started_at).total_seconds() * 1000

            try:
                arguments = json.loads(item.arguments)
            except json.JSONDecodeError:
                arguments = item.arguments

            # Один снимок менеджера: проверка и запись — над одним объектом.
            manager = self._writable_manager()
            if manager is not None:
                manager.record(
                    AgentStepType.TOOL_EXECUTION,
                    input={
                        "call_id": item.call_id,
                        "tool": item.name,
                        "arguments": arguments,
                        **(
                            {
                                "reason_code": tool_result.reason_code,
                                "diagnostic_type": tool_result.diagnostic_type,
                            }
                            if tool_result.diagnostic_type
                            else {}
                        ),
                    },
                    output=tool_result.output,
                    error=tool_result.error,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                )

            tool_outputs.append(self.build_tool_output(tool_result))

        return tool_outputs

    def _writable_manager(self) -> AgentRunManager | None:
        """Менеджер trace, в который сейчас можно писать, иначе None.

        Менеджер читается ОДИН раз и ДО владельца: run() выставляет владельца
        раньше, чем публикует новый менеджер, поэтому чужой менеджер активного
        прогона не может быть получен вместе с owner=None. Запись разрешена,
        пока trace RUNNING и вызов не чужой. Завершённый (COMPLETED/FAILED, в т.ч.
        аварийно прерванный) trace неизменяем. Ручной AgentRunManager (RUNNING,
        без активного run()) по-прежнему пишется — прежний контракт.
        """

        manager = self.run_manager
        if manager is None:
            return None

        owner = self._run_owner
        if owner is not None and owner != threading.get_ident():
            return None

        # Независимо от status: закрытый run() trace не принимает событий, даже
        # если сам статус не удалось перевести из RUNNING (двойной отказ).
        if manager is self._closed_manager:
            return None

        if manager.run.status != AgentRunStatus.RUNNING:
            return None

        return manager

    @staticmethod
    def extract_token_usage(response) -> dict[str, int] | None:

        usage = getattr(response, "usage", None)

        if usage is None:
            return None

        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        }

    @staticmethod
    def build_tool_output(tool_result: ToolResult) -> dict[str, str]:
        """Convert an internal ToolResult into the Responses API format."""

        output = (
            str(tool_result.output)
            if tool_result.error is None
            else f"Tool execution error: {tool_result.error}"
        )

        return {
            "type": "function_call_output",
            "call_id": tool_result.call_id,
            "output": output,
        }

    def run(self, ticket: SupportTicket) -> str:
        """Один экземпляр — один активный run() (M04-D3).

        Второй вызов (из другого потока или повторный вход) немедленно получает
        ConcurrentRunError и ничего не меняет. Публичные execute_function_call(s)
        — внутренние шаги цикла: trusted context и запись в trace доступны
        только потоку, владеющему активным прогоном.
        """

        # Захват — первой операцией, до любой мутации и вне try/finally ниже:
        # отклонённый вызов не помечает FAILED чужой прогон и не освобождает
        # чужой lock.
        if not self._run_lock.acquire(blocking=False):
            raise ConcurrentRunError("Agent run already in progress")

        manager: AgentRunManager | None = None
        try:
            # Порядок важен: сначала сброс чужого state и владелец, потом
            # публикация нового менеджера (см. _writable_manager).
            self.state = None
            self.refund_result = None
            self._run_owner = threading.get_ident()
            manager = self.run_manager = AgentRunManager(ticket.ticket_id)
            self._run_active = True

            try:
                return self._run_agent_loop(ticket)
            except BaseException as exc:
                # Не подавляем: фиксируем безопасный код и пробрасываем как есть,
                # в т.ч. KeyboardInterrupt/SystemExit — trace не остаётся RUNNING.
                # str(exc) не пишем (PII/аргументы).
                try:
                    manager.fail(_failure_code(exc))
                except BaseException:  # noqa: BLE001
                    # Вторичный отказ диагностики не должен подменять исходное
                    # исключение (F-1). Trace закрывается в finally ниже.
                    # Ограничение: подавляется и вторичный KeyboardInterrupt/
                    # SystemExit внутри fail() — наружу идёт исходное исключение.
                    pass
                raise
        finally:
            try:
                if manager is not None:
                    # Сначала независимый признак закрытия, потом попытка seal:
                    # при отказе обоих механизмов запись всё равно закрыта.
                    self._closed_manager = manager
                    try:
                        self._seal_trace(manager)
                    except BaseException:  # noqa: BLE001
                        # Отказ seal не заменяет исходное исключение (F-1/F-2).
                        pass
            finally:
                self._run_active = False
                self._run_owner = None
                self._run_lock.release()

    @staticmethod
    def _seal_trace(manager: AgentRunManager) -> None:
        """Запретить выход из run() с trace в статусе RUNNING (fail-safe)."""

        if manager.run.status == AgentRunStatus.RUNNING:
            manager.run.status = AgentRunStatus.FAILED
            manager.run.finished_at = datetime.now(UTC)
        elif manager.run.finished_at is None:
            # fail() прервали между установкой статуса и времени завершения.
            manager.run.finished_at = datetime.now(UTC)

    def _run_agent_loop(self, ticket: SupportTicket) -> str:
        """Orchestrate the loop; delegates the rest to dedicated helpers."""

        self.state = None
        state = self._start(ticket)

        if state is None:
            # Fail-closed (M03): прогон уже завершён детерминированным ответом.
            return CLASSIFICATION_FAILURE_RESPONSE

        self.state = state

        for _ in range(MAX_AGENT_STEPS):
            function_calls = self.get_function_calls(self.state.response)

            if not function_calls:
                return self._finish()

            self._advance(function_calls)

        raise RuntimeError(
            f"Agent exceeded maximum number of steps: {MAX_AGENT_STEPS}"
        )

    def _start(self, ticket: SupportTicket) -> AgentState | None:
        """Classify, then start the LLM loop.

        Возвращает None, если классификация завершилась ожидаемой
        ClassificationError: прогон уже завершён детерминированным degraded-ответом,
        LLM, инструменты и оценка возврата не вызывались (fail-closed).
        """

        try:
            classification = self._classify(ticket)
        except ClassificationError:
            self._finish_classification_failed()
            return None

        self.refund_result = self._evaluate_refund(ticket, classification)
        # Тикет доступен инструментам (доверенная идентичность) уже с первого вызова.
        state = AgentState(ticket=ticket, response=None)
        self.state = state
        response = self._call_model(
            self._build_initial_input(ticket, classification, self.refund_result)
        )
        self._record_llm_step({"message": ticket.message}, response)
        state.response = response

        return state

    def _classify(self, ticket: SupportTicket) -> TicketClassification | None:
        """Classify before the loop.

        classifier=None -> None (classification disabled, pipeline unchanged).
        ClassificationError is traced and re-raised: `_start` fails closed.
        Any other exception is a defect: it is traced and propagated (run -> FAILED).
        """

        if self.classifier is None:
            return None

        started_at = datetime.now(UTC)
        # Текст обращения в trace не пишем (персональные данные) — только длину.
        input_payload = {"message_length": len(ticket.message)}

        try:
            result: ClassificationResult = self.classifier.classify(ticket)
        except ClassificationError as exc:
            # Ожидаемая ошибка классификации. В trace — только тип и kind:
            # str(exc) может содержать текст обращения (ValidationError).
            # kind нормализуется allowlist-ом: сторонний TicketClassifier не может
            # протащить произвольный текст (PII) в trace через exc.kind.
            safe_kind = (
                exc.kind
                if type(exc.kind) is str and exc.kind in _SAFE_CLASSIFICATION_KINDS
                else "unknown"
            )
            self._record_classification_error(
                input_payload, started_at, f"ClassificationError: {safe_kind}",
                output={"fallback": True},
            )
            raise
        except Exception:
            # Программный дефект: фиксируем диагностику и НЕ подавляем.
            # run() переведёт AgentRun в FAILED и пробросит исключение.
            # Ни текст, ни имя класса исключения в trace не пишутся.
            self._record_classification_error(
                input_payload, started_at,
                "Unexpected classifier error [classification_error]",
                output={"fallback": False, "unexpected_error": True},
            )
            raise

        finished_at = datetime.now(UTC)
        self.run_manager.record(
            AgentStepType.CLASSIFICATION,
            input=input_payload,
            output={
                "classification": result.classification.model_dump(mode="json"),
                "model": result.model,
                "response_id": result.response_id,
                "usage": result.usage,
            },
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(finished_at - started_at).total_seconds() * 1000,
        )
        return result.classification

    def _evaluate_refund(
        self, ticket: SupportTicket, classification: TicketClassification | None
    ) -> RefundEvaluationResult | None:
        """Детерминированная оценка возврата политикой (без LLM).

        Результат записывается в trace (шаг POLICY_DECISION), передаётся в
        контекст модели и определяет детерминированную refund-часть итогового
        ответа. Решение принимает политика, а не LLM: оценка не выдаёт
        полномочий и не меняет context/policy/доступные инструменты. Сбои не
        прерывают прогон и не превращаются в ALLOW. Для нерелевантных
        обращений (NOT_APPLICABLE) шаг не пишется и возвращается None.
        """

        if self.refund_evaluator is None:
            return None

        started_at = datetime.now(UTC)
        try:
            result: RefundEvaluationResult = self.refund_evaluator.evaluate(
                ticket, classification
            )
        except Exception:
            # Текст исключения в trace не пишем.
            result = RefundEvaluationResult(
                status=RefundEvaluationStatus.POLICY_ERROR,
                reason_code="refund_evaluation_error",
            )

        if result.status == RefundEvaluationStatus.NOT_APPLICABLE:
            return None

        decision = result.decision
        finished_at = datetime.now(UTC)
        self.run_manager.record(
            AgentStepType.POLICY_DECISION,
            input={"refund_evaluation": True},
            output={
                "status": result.status.value,
                "decision": decision.decision.value if decision else None,
                "reason_code": result.reason_code,
                "policy_version": decision.policy_version if decision else None,
                "order_id": result.order_id,
                "days_since_delivery": (
                    decision.days_since_delivery if decision else None
                ),
                "provenance": result.provenance,
            },
            error=(
                result.reason_code
                if result.status
                in (
                    RefundEvaluationStatus.FACTS_UNAVAILABLE,
                    RefundEvaluationStatus.POLICY_ERROR,
                )
                else None
            ),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(finished_at - started_at).total_seconds() * 1000,
        )
        return result

    def _record_classification_error(
        self, input_payload: dict, started_at: datetime, error: str, *, output: dict
    ) -> None:
        finished_at = datetime.now(UTC)
        self.run_manager.record(
            AgentStepType.CLASSIFICATION,
            input=input_payload,
            output=output,
            error=error,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(finished_at - started_at).total_seconds() * 1000,
        )

    @staticmethod
    def _build_initial_input(
        ticket: SupportTicket,
        classification: TicketClassification | None,
        refund_result: RefundEvaluationResult | None = None,
    ):
        context = []
        if classification is not None:
            context.append(format_classification_context(classification))
        if refund_result is not None:
            context.append(build_policy_context(refund_result))

        if not context:
            return ticket.message

        return [
            *({"role": "developer", "content": c} for c in context),
            {"role": "user", "content": ticket.message},
        ]

    def _advance(self, function_calls: list) -> None:
        tool_outputs = self.execute_function_calls(function_calls)

        self.state.response = self._call_model(
            tool_outputs,
            previous_response_id=self.state.response.id,
        )
        self.state.step_count += 1

        self._record_llm_step({"tool_outputs": tool_outputs}, self.state.response)

    def _finish(self) -> str:
        llm_text = self.state.response.output_text
        answer, dropped = compose_final_answer(self.refund_result, llm_text)

        output: dict[str, object] = {"answer": answer}
        if self.refund_result is not None:
            output["refund_llm_text_dropped"] = dropped

        self.run_manager.record(AgentStepType.FINAL, output=output)

        self.run_manager.complete()

        return answer

    def _finish_classification_failed(self) -> None:
        """Детерминированное завершение при ожидаемой ошибке классификации."""

        self.run_manager.record(
            AgentStepType.FINAL,
            output={
                "answer": CLASSIFICATION_FAILURE_RESPONSE,
                "degraded": True,
                "classification_failed": True,
            },
        )
        self.run_manager.complete()

    def _call_model(self, model_input, *, previous_response_id: str | None = None):
        kwargs = {"model": MODEL_NAME, "input": model_input}

        if previous_response_id is None:
            kwargs["tools"] = get_llm_tool_schemas()
        else:
            kwargs["previous_response_id"] = previous_response_id

        return self.client.responses.create(**kwargs)

    def _record_llm_step(self, input_payload: dict, response) -> None:
        self.run_manager.record(
            AgentStepType.LLM_CALL,
            input=input_payload,
            output={
                "response_id": response.id,
                "model": MODEL_NAME,
                "usage": self.extract_token_usage(response),
            },
        )