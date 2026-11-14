"""SEC003-M03: Classification fail-closed.

Ожидаемая ClassificationError -> детерминированный degraded-ответ без LLM,
инструментов и refund evaluation. Неожиданные исключения -> FAILED.
Счётчики на fake-клиенте/инструментах/evaluator — не проверка только текста ответа.
"""

import json

import pytest

from supportops_agent import mock_data
from supportops_agent import agent as agent_module
from supportops_agent.agent import CLASSIFICATION_FAILURE_RESPONSE, SupportAgent
from supportops_agent.authorization import support_agent_context
from supportops_agent.classification import (
    ClassificationError,
    ClassificationResult,
    TicketClassification,
    TicketIntent,
)
from supportops_agent.models import AgentRunStatus, AgentStepType, SupportTicket

KINDS = ["api_error", "invalid_output"]
TICKETS = {
    "standard": "Where is my order_001?",
    "refund": "I want a full refund for order_001 right now, my card is 4111111111111111.",
}
EXPECTED_TEXT = (
    "We're unable to process your request right now. "
    "Please try again later or contact support."
)


class FnCall:
    type = "function_call"
    name = "create_support_case"
    arguments = '{"reason": "r", "priority": "high"}'
    call_id = "call_1"


class Response:
    def __init__(self, response_id, output=None, output_text="done"):
        self.id, self.output, self.output_text, self.usage = (
            response_id, output or [], output_text, None
        )


class CountingClient:
    """Первый ответ модели — tool call: если LLM вызвали, инструмент бы исполнился."""

    def __init__(self):
        self.create_calls = []
        self.responses = self

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if len(self.create_calls) == 1:
            return Response("r1", output=[FnCall()])
        return Response("r2")


class StubClassifier:
    def __init__(self, classification=None, exc=None):
        self.classification, self.exc = classification, exc

    def classify(self, ticket):
        if self.exc:
            raise self.exc
        return ClassificationResult(self.classification, model="m", response_id="c1")


class CountingEvaluator:
    def __init__(self):
        self.calls = 0

    def evaluate(self, ticket, classification):
        self.calls += 1
        raise AssertionError("refund evaluation must not run")


def ticket(kind="standard"):
    return SupportTicket(
        ticket_id="t1", customer_id="customer_001", message=TICKETS[kind]
    )


@pytest.fixture
def tool_calls(monkeypatch):
    """Шпион на границе исполнения инструментов + защита от побочных эффектов."""
    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("tool executed during classification failure")

    monkeypatch.setattr(agent_module, "execute_tool_call", spy)
    mock_data.CASES.clear()
    yield calls
    mock_data.CASES.clear()


def failing_agent(kind, evaluator=None):
    client = CountingClient()
    agent = SupportAgent(
        client,
        classifier=StubClassifier(exc=ClassificationError(kind, "boom")),
        context=support_agent_context(),
        refund_evaluator=evaluator,
    )
    return agent, client


def types(agent):
    return [s.step_type for s in agent.last_run.steps]


# ---- T1/T2: ClassificationError (оба kind, обычный и refund-тикет) -----------

@pytest.mark.parametrize("ticket_kind", TICKETS)
@pytest.mark.parametrize("kind", KINDS)
def test_classification_error_returns_fixed_english_response(kind, ticket_kind, tool_calls):
    agent, client = failing_agent(kind, CountingEvaluator())

    answer = agent.run(ticket(ticket_kind))

    assert answer == EXPECTED_TEXT == CLASSIFICATION_FAILURE_RESPONSE
    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert types(agent) == [AgentStepType.CLASSIFICATION, AgentStepType.FINAL]


@pytest.mark.parametrize("kind", KINDS)
def test_refund_ticket_gets_no_refund_confirmation_or_promise(kind, tool_calls):
    evaluator = CountingEvaluator()
    agent, _ = failing_agent(kind, evaluator)

    answer = agent.run(ticket("refund")).lower()

    assert agent.refund_result is None
    assert evaluator.calls == 0
    assert AgentStepType.POLICY_DECISION not in types(agent)
    for claim in ("refund", "approved", "eligible", "verified", "will be processed"):
        assert claim not in answer


# ---- T6/T7/T8: нет LLM, инструментов, refund evaluation ----------------------

@pytest.mark.parametrize("ticket_kind", TICKETS)
@pytest.mark.parametrize("kind", KINDS)
def test_no_llm_no_tools_no_refund_evaluation(kind, ticket_kind, tool_calls):
    evaluator = CountingEvaluator()
    agent, client = failing_agent(kind, evaluator)

    agent.run(ticket(ticket_kind))

    assert len(client.create_calls) == 0  # T6: responses.create не вызывался
    assert tool_calls == []  # T7: ни один инструмент не исполнялся
    assert not mock_data.CASES  # побочных эффектов нет
    assert evaluator.calls == 0  # T8
    assert AgentStepType.TOOL_EXECUTION not in types(agent)
    assert AgentStepType.LLM_CALL not in types(agent)
    assert agent.state is None  # LLM-цикл не инициализировался


# ---- T9: trace и summary (структурно) ----------------------------------------

@pytest.mark.parametrize("kind", KINDS)
def test_trace_and_summary_structure(kind, tool_calls):
    agent, _ = failing_agent(kind)

    agent.run(ticket())

    cls_step, final_step = agent.last_run.steps
    assert cls_step.step_type == AgentStepType.CLASSIFICATION
    assert cls_step.error == f"ClassificationError: {kind}"
    assert cls_step.output == {"fallback": True}
    assert cls_step.input == {"message_length": len(TICKETS["standard"])}
    assert final_step.step_type == AgentStepType.FINAL
    assert final_step.error is None
    assert final_step.output == {
        "answer": EXPECTED_TEXT,
        "degraded": True,
        "classification_failed": True,
    }
    assert [s.step_number for s in agent.last_run.steps] == [1, 2]
    assert agent.last_run.finished_at is not None

    summary = agent.last_run.summary()
    assert summary["status"] == AgentRunStatus.COMPLETED  # статус не менялся
    assert summary["degraded"] is True
    assert summary["classification_failed"] is True
    assert summary["llm_calls"] == 0
    assert summary["tool_calls"] == 0
    assert summary["error"] is None


# ---- T3: classifier=None --------------------------------------------------------

def test_disabled_classifier_keeps_existing_pipeline():
    client = CountingClient()
    client.create = lambda **kw: (client.create_calls.append(kw), Response("r1"))[1]
    agent = SupportAgent(client, classifier=None, context=support_agent_context())

    assert agent.run(ticket()) == "done"

    assert len(client.create_calls) == 1
    assert types(agent) == [AgentStepType.LLM_CALL, AgentStepType.FINAL]
    summary = agent.last_run.summary()
    assert summary["degraded"] is False and summary["classification_failed"] is False
    assert summary["status"] == AgentRunStatus.COMPLETED


# ---- T4: успешная классификация --------------------------------------------------

def test_successful_classification_continues_pipeline():
    client = CountingClient()
    client.create = lambda **kw: (client.create_calls.append(kw), Response("r1"))[1]
    classification = TicketClassification.fallback()
    assert classification.primary_intent == TicketIntent.UNKNOWN
    agent = SupportAgent(
        client, classifier=StubClassifier(classification), context=support_agent_context()
    )

    assert agent.run(ticket()) == "done"

    assert len(client.create_calls) == 1
    assert types(agent) == [
        AgentStepType.CLASSIFICATION, AgentStepType.LLM_CALL, AgentStepType.FINAL
    ]
    summary = agent.last_run.summary()
    assert summary["degraded"] is False and summary["classification_failed"] is False


# ---- T5: неожиданное исключение -----------------------------------------------

@pytest.mark.parametrize("exc", [KeyError("bug"), RuntimeError("bug"), ValueError("bug")])
def test_unexpected_exception_still_fails_run(exc, tool_calls):
    client = CountingClient()
    agent = SupportAgent(
        client, classifier=StubClassifier(exc=exc), context=support_agent_context()
    )

    with pytest.raises(type(exc)):  # не подменяется клиентским fallback
        agent.run(ticket())

    run = agent.last_run
    assert run.status == AgentRunStatus.FAILED
    assert types(agent) == [AgentStepType.CLASSIFICATION, AgentStepType.FAILURE]
    assert AgentStepType.FINAL not in types(agent)
    assert run.steps[0].output == {"fallback": False, "unexpected_error": True}
    summary = run.summary()
    assert summary["degraded"] is False  # degraded — не признак любых ошибок
    assert summary["status"] == AgentRunStatus.FAILED
    assert summary["error"]
    assert client.create_calls == []


# ---- T10: нет утечек ------------------------------------------------------------

def test_no_sensitive_data_in_response_or_trace(tool_calls):
    pii = "john.doe@example.com card 4111111111111111 SECRET-INTERNAL-DETAIL"
    client = CountingClient()
    agent = SupportAgent(
        client,
        classifier=StubClassifier(exc=ClassificationError("invalid_output", pii)),
        context=support_agent_context(),
    )

    answer = agent.run(ticket("refund"))

    dump = json.dumps(agent.last_run.model_dump(mode="json")) + answer
    for secret in ("john.doe", "4111111111111111", "SECRET-INTERNAL", "boom"):
        assert secret not in dump
    assert TICKETS["refund"] not in dump  # текст обращения не попадает в trace
    assert answer == EXPECTED_TEXT


def test_each_run_resets_degraded_state(tool_calls):
    """Повторный run на том же агенте не наследует degraded прошлого прогона."""
    agent, client = failing_agent("api_error")
    agent.run(ticket())
    agent.classifier = StubClassifier(TicketClassification.fallback())
    client.create = lambda **kw: (client.create_calls.append(kw), Response("r1"))[1]

    assert agent.run(ticket()) == "done"
    assert agent.last_run.summary()["degraded"] is False


# ======================= M03 hardening (F-1, F-2, repeated runs) ===============

PII_KIND = "john.doe@example.com card 4111111111111111 INTERNAL-ID-7731"


def trace_dump(agent):
    return json.dumps(agent.last_run.model_dump(mode="json"))


# ---- F-1: ClassificationError.kind нормализуется allowlist-ом ----------------

@pytest.mark.parametrize("kind", KINDS)
def test_f1_known_kinds_preserved_in_trace(kind, tool_calls):
    agent, _ = failing_agent(kind)
    agent.run(ticket())
    assert agent.last_run.steps[0].error == f"ClassificationError: {kind}"


@pytest.mark.parametrize("bad_kind", [PII_KIND, "something_else", "", "API_ERROR"])
def test_f1_arbitrary_kind_becomes_unknown_without_pii(bad_kind, tool_calls):
    evaluator = CountingEvaluator()
    agent, client = failing_agent(bad_kind, evaluator)

    answer = agent.run(ticket("refund"))

    assert agent.last_run.steps[0].error == "ClassificationError: unknown"
    dump = trace_dump(agent) + answer
    for secret in ("john.doe", "4111111111111111", "INTERNAL-ID-7731", "something_else"):
        assert secret not in dump
    assert answer == EXPECTED_TEXT
    assert client.create_calls == [] and tool_calls == [] and evaluator.calls == 0
    assert agent.last_run.status == AgentRunStatus.COMPLETED


def test_f1_str_exc_not_in_trace_and_exception_not_mutated(tool_calls):
    exc = ClassificationError(PII_KIND, "detail with john.doe@example.com")
    agent = SupportAgent(
        CountingClient(), classifier=StubClassifier(exc=exc),
        context=support_agent_context(),
    )
    agent.run(ticket())
    assert exc.kind == PII_KIND  # исходное исключение не изменено
    assert "john.doe" not in trace_dump(agent)


@pytest.mark.parametrize("malformed", [None, 123, b"api_error", ["api_error"]])
def test_f1_malformed_kind_type_is_unknown(malformed, tool_calls):
    exc = ClassificationError("api_error", "d")
    exc.kind = malformed  # runtime-подмена без расширения публичной модели
    agent = SupportAgent(
        CountingClient(), classifier=StubClassifier(exc=exc),
        context=support_agent_context(),
    )
    assert agent.run(ticket()) == EXPECTED_TEXT
    assert agent.last_run.steps[0].error == "ClassificationError: unknown"


def test_f1_str_subclass_kind_is_unknown(tool_calls):
    class EvilStr(str):
        pass

    exc = ClassificationError("api_error", "d")
    exc.kind = EvilStr("api_error")  # type(...) is str -> отвергается
    agent = SupportAgent(
        CountingClient(), classifier=StubClassifier(exc=exc),
        context=support_agent_context(),
    )
    agent.run(ticket())
    assert agent.last_run.steps[0].error == "ClassificationError: unknown"


# ---- F-2: FAILED никогда не degraded -----------------------------------------

def test_f2_complete_failure_after_final_is_failed_not_degraded(monkeypatch, tool_calls):
    evaluator = CountingEvaluator()
    agent, client = failing_agent("api_error", evaluator)

    def boom(self):
        raise RuntimeError("complete exploded")

    monkeypatch.setattr(agent_module.AgentRunManager, "complete", boom)

    with pytest.raises(RuntimeError, match="complete exploded"):  # не поглощено
        agent.run(ticket())

    run = agent.last_run
    assert run.status == AgentRunStatus.FAILED
    assert run.summary()["degraded"] is False
    # trace сохраняет фактическую историю: FINAL остался, затем FAILURE
    assert types(agent) == [
        AgentStepType.CLASSIFICATION, AgentStepType.FINAL, AgentStepType.FAILURE
    ]
    assert run.steps[1].output["degraded"] is True
    assert client.create_calls == [] and tool_calls == [] and evaluator.calls == 0
    assert agent.refund_result is None


@pytest.mark.parametrize(
    "status,final_degraded,expected",
    [
        (AgentRunStatus.COMPLETED, True, True),
        (AgentRunStatus.COMPLETED, False, False),
        (AgentRunStatus.FAILED, True, False),
        (AgentRunStatus.FAILED, False, False),
    ],
)
def test_f2_degraded_truth_table(status, final_degraded, expected):
    from supportops_agent.models import AgentRun, AgentStep

    run = AgentRun(ticket_id="t1", status=status)
    run.steps.append(
        AgentStep(
            step_number=1, step_type=AgentStepType.FINAL,
            output={"answer": "x", "degraded": final_degraded},
        )
    )
    assert run.summary()["degraded"] is expected


# ---- Повторные запуски одного агента -------------------------------------------

def _ok_client():
    client = CountingClient()
    client.create = lambda **kw: (client.create_calls.append(kw), Response("r1"))[1]
    return client


def test_r1_success_then_classification_error(tool_calls):
    client = _ok_client()
    agent = SupportAgent(
        client,
        classifier=StubClassifier(TicketClassification.fallback()),
        context=support_agent_context(),
        refund_evaluator=None,
    )
    assert agent.run(ticket()) == "done"
    first_run = agent.last_run
    llm_calls_before = len(client.create_calls)
    agent.refund_result = "stale"  # остаточное состояние прошлого прогона

    agent.classifier = StubClassifier(exc=ClassificationError("api_error", "x"))
    answer = agent.run(ticket())

    assert answer == EXPECTED_TEXT
    assert agent.last_run is not first_run
    assert agent.last_run.summary()["degraded"] is True
    assert first_run.summary()["degraded"] is False
    assert len(client.create_calls) == llm_calls_before  # LLM после ошибки не вызван
    assert types(agent) == [AgentStepType.CLASSIFICATION, AgentStepType.FINAL]
    assert agent.refund_result is None
    assert agent.state is None


def test_r2_classification_error_then_success(tool_calls):
    client = _ok_client()
    agent = SupportAgent(
        client,
        classifier=StubClassifier(exc=ClassificationError("invalid_output", "x")),
        context=support_agent_context(),
    )
    agent.run(ticket())
    degraded_run = agent.last_run

    agent.classifier = StubClassifier(TicketClassification.fallback())
    assert agent.run(ticket()) == "done"

    summary = agent.last_run.summary()
    assert summary["degraded"] is False and summary["classification_failed"] is False
    assert degraded_run.summary()["degraded"] is True
    assert types(agent) == [
        AgentStepType.CLASSIFICATION, AgentStepType.LLM_CALL, AgentStepType.FINAL
    ]
    assert all(
        (s.output or {}).get("classification_failed") is not True
        for s in agent.last_run.steps
    )


def test_r3_classification_error_then_unexpected_exception(tool_calls):
    client = CountingClient()
    agent = SupportAgent(
        client,
        classifier=StubClassifier(exc=ClassificationError("api_error", "x")),
        context=support_agent_context(),
    )
    agent.run(ticket())

    agent.classifier = StubClassifier(exc=RuntimeError("bug"))
    with pytest.raises(RuntimeError, match="bug"):
        agent.run(ticket())

    run = agent.last_run
    assert run.status == AgentRunStatus.FAILED
    assert run.summary()["degraded"] is False
    assert AgentStepType.FINAL not in types(agent)  # старый FINAL не унаследован
    assert agent.refund_result is None
    assert client.create_calls == []


# ---- Граница LLM: classification LLM (parse) vs agent LLM (create) ------------

class SplitResponses:
    def __init__(self):
        self.parse_calls, self.create_calls = [], []

    def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        # Классификатор обратился к LLM, затем ответ непригоден -> invalid_output.
        return type("P", (), {"output_parsed": None, "id": "c", "usage": None})()

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        raise AssertionError("agent LLM must not run after ClassificationError")


def test_classification_llm_allowed_agent_llm_forbidden(tool_calls):
    from supportops_agent.classification import OpenAITicketClassifier

    client = type("C", (), {})()
    client.responses = SplitResponses()
    agent = SupportAgent(
        client,
        classifier=OpenAITicketClassifier(client),
        context=support_agent_context(),
    )

    assert agent.run(ticket()) == EXPECTED_TEXT

    assert len(client.responses.parse_calls) == 1   # classification LLM: допустим
    assert client.responses.create_calls == []      # agent LLM: запрещён
    assert agent.last_run.steps[0].error == "ClassificationError: invalid_output"
    assert tool_calls == []
