"""SEC-003 / M04 / SR-4: metadata-only trace.

Диагностический trace не содержит сырых бизнес-данных, ПД и произвольных
значений от LLM и инструментов. Рабочий LLM-контекст при этом не страдает.
Маркеры синтетические; проверяется ВЕСЬ сериализованный trace.
"""

import json

import pytest

from supportops_agent.runtime import agent as agent_module
from supportops_agent.runtime.agent import (
    TRACE_MAX_STRING,
    TRACE_MAX_TOTAL_CHARS,
    UNKNOWN_TOOL_LABEL,
    SupportAgent,
    _bound_payload,
)
from supportops_agent.tools.authorization import support_agent_context
from supportops_agent.models import AgentRunStatus, AgentStepType, SupportTicket
from supportops_agent.tools.definitions import TOOL_REGISTRY

SECRET = "SYNTH_SECRET_ABC123"
EMAIL = "SYNTH_EMAIL@example.invalid"
CARD = "SYNTH_CARD_4111111111111111"
MARKERS = (SECRET, EMAIL, CARD)


class _Call:
    type = "function_call"

    def __init__(self, name, arguments, call_id="call_1"):
        self.call_id, self.name = call_id, name
        self.arguments = arguments if isinstance(arguments, str) else json.dumps(arguments)


class _Response:
    def __init__(self, rid, output=None, text="done"):
        self.id, self.output, self.output_text, self.usage = rid, output or [], text, None


class _Client:
    def __init__(self, *calls):
        self.calls_to_make, self.n, self.requests = list(calls), 0, []
        self.responses = self

    def create(self, **kw):
        self.requests.append(kw)
        self.n += 1
        if self.n == 1 and self.calls_to_make:
            return _Response("r1", self.calls_to_make)
        return _Response(f"r{self.n}", text="Safe answer.")


def _ticket(message="please help", ticket_id="ticket_A", customer="customer_001"):
    return SupportTicket(ticket_id=ticket_id, customer_id=customer, message=message)


def _run(*calls, message="please help", handler=None):
    client = _Client(*calls)
    agent = SupportAgent(client, context=support_agent_context())
    agent.run(_ticket(message))
    return agent, client


def _trace(agent):
    return agent.last_run.model_dump_json()


def _tool_steps(agent):
    return [s for s in agent.last_run.steps if s.step_type == AgentStepType.TOOL_EXECUTION]


def _assert_clean(agent):
    blob = _trace(agent) + json.dumps(agent.last_run.summary(), default=str)
    assert [m for m in MARKERS if m in blob] == []


# 1. секрет в известном аргументе
def test_known_argument_value_not_in_trace():
    agent, _ = _run(_Call("lookup_order", {"order_id": SECRET}))
    _assert_clean(agent)
    assert _tool_steps(agent)[0].input == {
        "call_index": 0,
        "tool": "lookup_order",
        "argument_count": 1,
        "validation_status": "valid",
    }


# 2. секрет в неизвестном аргументе
def test_unknown_argument_value_and_name_not_in_trace():
    agent, _ = _run(_Call("lookup_order", {"order_id": "order_001", "extra": SECRET}))
    _assert_clean(agent)
    step = _tool_steps(agent)[0]
    assert step.input["validation_status"] == "invalid_schema"
    assert step.input["argument_count"] == 2
    assert "extra" not in _trace(agent)


# 3. секрет во вложенном JSON
def test_nested_json_not_in_trace():
    agent, _ = _run(_Call("lookup_order", {"order_id": {"a": {"b": [SECRET, EMAIL]}}}))
    _assert_clean(agent)
    assert _tool_steps(agent)[0].input["validation_status"] == "invalid_schema"


# 4. секрет в имени ключа
def test_secret_in_key_name_not_in_trace():
    agent, _ = _run(_Call("lookup_order", {SECRET: CARD}))
    _assert_clean(agent)


# 5. невалидный JSON с секретом
def test_invalid_json_not_in_trace():
    agent, _ = _run(_Call("lookup_order", '{"order_id": "' + SECRET))
    _assert_clean(agent)
    step = _tool_steps(agent)[0]
    assert step.input["validation_status"] == "invalid_json"
    assert step.input["argument_count"] == 0


# 6. полный текст обращения
def test_ticket_message_not_in_trace_only_length():
    message = f"my card {CARD} email {EMAIL} secret {SECRET}"
    agent, client = _run(_Call("lookup_order", {"order_id": "order_001"}), message=message)
    _assert_clean(agent)
    first = agent.last_run.steps[0]
    assert first.step_type == AgentStepType.LLM_CALL
    assert first.input == {"message_length": len(message)}
    # LLM по-прежнему получает исходный текст (работа агента не сломана).
    assert client.requests[0]["input"] == message


# 7. email в результате инструмента (lookup_customer возвращает email клиента)
def test_tool_output_email_not_in_trace_but_in_llm_context():
    agent, client = _run(_Call("lookup_customer", {"customer_id": "customer_001"}))
    trace = _trace(agent)
    assert "john@example.com" not in trace
    assert "john@example.com" in json.dumps(client.requests[1]["input"])
    step = _tool_steps(agent)[0]
    assert set(step.output) == {"output_field_count"}
    llm_step = [s for s in agent.last_run.steps if s.step_type == AgentStepType.LLM_CALL][1]
    assert llm_step.input == {"tool_output_count": 1}


# 8. произвольная строка в результате инструмента
def test_arbitrary_tool_output_string_not_in_trace(monkeypatch):
    monkeypatch.setattr(
        TOOL_REGISTRY["lookup_order"], "handler",
        lambda **_: {"note": SECRET, "mail": EMAIL, "nested": {"c": CARD}},
    )
    agent, client = _run(_Call("lookup_order", {"order_id": "order_001"}))
    _assert_clean(agent)
    assert SECRET in json.dumps(client.requests[1]["input"])  # LLM-контекст цел
    assert _tool_steps(agent)[0].output == {"output_field_count": 3}


# 9. очень длинный payload
def test_huge_payload_is_bounded_before_recording():
    huge = SECRET + "x" * 1_000_000
    agent, _ = _run(_Call("lookup_order", {"order_id": huge, **{f"k{i}": i for i in range(500)}}))
    assert len(_trace(agent)) < 20_000
    _assert_clean(agent)


def test_bound_payload_deterministic_and_covers_bypasses():
    deep = current = {}
    for _ in range(50):
        current["n"] = {}
        current = current["n"]
    wide = {f"{'k' * 1000}{i}": "v" * 10_000 for i in range(5000)}
    for payload in (deep, wide, {"s": "a" * 10**6}, {"l": [["z" * 999] * 99] * 99}):
        first = _bound_payload(payload)
        assert first == _bound_payload(payload)
        assert len(json.dumps(first)) <= TRACE_MAX_TOTAL_CHARS
    long_key = _bound_payload({"k" * 5_000: 1})  # меньше общего лимита: режется именно ключ
    assert list(long_key.values()) == [1]
    assert max(len(k) for k in long_key) < 100
    assert len(_bound_payload({"s": "a" * 10**6})["s"]) < TRACE_MAX_STRING + 32


def test_bound_does_not_alter_tool_arguments_or_llm_context(monkeypatch):
    seen = {}

    def handler(**kw):
        seen.update(kw)
        return {"ok": True}

    monkeypatch.setattr(TOOL_REGISTRY["lookup_order"], "handler", handler)
    order_id = "o" * 100  # максимум схемы
    _run(_Call("lookup_order", {"order_id": order_id}))
    assert seen["order_id"] == order_id


# 10-11. сериализация last_run и завершённый trace
def test_completed_trace_serialization_clean_end_to_end():
    agent, _ = _run(
        _Call("lookup_order", {"order_id": SECRET}, call_id=f"id_{SECRET}"),
        _Call("lookup_customer", {"customer_id": "customer_001"}, call_id=EMAIL),
        message=f"{CARD} {EMAIL}",
    )
    assert agent.last_run.status == AgentRunStatus.COMPLETED
    _assert_clean(agent)


# 12. неизвестный инструмент
def test_unknown_tool_name_never_reaches_trace():
    client = _Client(_Call(SECRET, {"order_id": "x"}))
    agent = SupportAgent(client, context=support_agent_context())
    with pytest.raises(Exception):
        agent.run(_ticket())
    _assert_clean(agent)
    assert agent.last_run.status == AgentRunStatus.FAILED


def test_untrusted_tool_name_label_is_fixed():
    assert agent_module._safe_tool_label(SECRET) == UNKNOWN_TOOL_LABEL
    assert agent_module._safe_tool_label(None) == UNKNOWN_TOOL_LABEL
    assert agent_module._safe_tool_label(["lookup_order"]) == UNKNOWN_TOOL_LABEL
    assert agent_module._safe_tool_label("lookup_order") == "lookup_order"


def test_direct_execute_function_calls_unknown_tool_label():
    agent = SupportAgent(_Client(), context=support_agent_context())
    from supportops_agent.runtime.run_manager import AgentRunManager

    agent.run_manager = AgentRunManager("t")
    agent.execute_function_calls([_Call("lookup_order", {"order_id": "order_001"})])
    assert agent.last_run.steps[0].input["tool"] == "lookup_order"


# call_id: произвольная строка провайдера — в trace нет, связь с LLM цела
def test_call_id_not_in_trace_but_correlation_preserved():
    cid = f"call_{SECRET}"
    agent, client = _run(_Call("lookup_order", {"order_id": "order_001"}, call_id=cid))
    assert cid not in _trace(agent)
    outputs = client.requests[1]["input"]
    assert outputs[0]["call_id"] == cid and outputs[0]["type"] == "function_call_output"


# 13. LLM-контекст сохраняется (инструментальный результат в полном виде)
def test_llm_context_keeps_full_tool_result():
    _, client = _run(_Call("lookup_order", {"order_id": "order_001"}))
    out = client.requests[1]["input"][0]["output"]
    assert "order_001" in out and "customer_001" in out and "delivered" in out


# 14. Trusted Ticket Binding сохранён
def test_trusted_ticket_binding_still_enforced(monkeypatch):
    from supportops_agent.business import mock_data

    agent, _ = _run(
        _Call("create_support_case", {"reason": "damaged", "priority": "high"}),
    )
    assert [c.ticket_id for c in mock_data.CASES.values()] == ["ticket_A"]
    _assert_clean(agent)


# denied / error пути не добавляют сырых данных
def test_authorization_denied_path_has_only_codes():
    client = _Client(_Call("lookup_order", {"order_id": SECRET}))
    agent = SupportAgent(client, context=None)
    agent.run(_ticket())
    _assert_clean(agent)
    step = _tool_steps(agent)[0]
    assert step.input["validation_status"] == "valid"
    assert step.output is None and "MISSING_CONTEXT" in step.error


def test_reason_code_closed_set(monkeypatch):
    from supportops_agent.tools.executor import ToolResult

    result = ToolResult(
        call_id="c", name="lookup_order", error="x", error_type="authorization_denied",
        reason_code=SECRET, diagnostic_type="PolicyBoom",
    )
    monkeypatch.setattr(SupportAgent, "execute_function_call", lambda self, item: result)
    from supportops_agent.runtime.run_manager import AgentRunManager

    agent = SupportAgent(_Client(), context=support_agent_context())
    agent.run_manager = AgentRunManager("t")
    agent.execute_function_calls([_Call("lookup_order", {"order_id": "order_001"})])
    assert SECRET not in _trace(agent)
    assert agent.last_run.steps[0].input["reason_code"] == "unknown_reason"


def test_untrusted_tool_name_replaced_in_recorded_step(monkeypatch):
    from supportops_agent.runtime.run_manager import AgentRunManager
    from supportops_agent.tools.executor import ToolResult

    result = ToolResult(call_id="c", name="lookup_order", error="e", error_type="invalid_arguments")
    monkeypatch.setattr(SupportAgent, "execute_function_call", lambda self, item: result)
    agent = SupportAgent(_Client(), context=support_agent_context())
    agent.run_manager = AgentRunManager("t")
    agent.execute_function_calls([_Call(SECRET, {"a": 1})])
    assert SECRET not in _trace(agent)
    assert agent.last_run.steps[0].input["tool"] == UNKNOWN_TOOL_LABEL


# Ревью: ограничитель не должен бросать исключений и влиять на run()
class _BadStr(str):
    def __str__(self):
        raise RuntimeError("boom")


def test_bounder_never_raises_on_hostile_values():
    for payload in (
        {"a": 10**5000},
        {"a": _BadStr(SECRET)},
        {_BadStr(SECRET): 1},
        {1: "a", "1": "b", b"k": object()},
        {"l": [object(), {1, 2}]},
    ):
        out = _bound_payload(payload)
        assert isinstance(out, dict)
        json.dumps(out)
        assert SECRET not in json.dumps(out)


def test_bounder_failure_does_not_replace_classification_error(monkeypatch):
    from supportops_agent.classification import ClassificationError

    class _Cls:
        def classify(self, ticket):
            raise ClassificationError("api_error", "x")

    monkeypatch.setattr(agent_module, "_bound_value", lambda *a, **k: 1 / 0)
    agent = SupportAgent(_Client(), _Cls(), context=support_agent_context())
    answer = agent.run(_ticket())
    assert answer == agent_module.CLASSIFICATION_FAILURE_RESPONSE
    assert agent.last_run.status == AgentRunStatus.COMPLETED


# ===================== H5: финальная очистка trace =====================
from supportops_agent.classification import (  # noqa: E402
    ClassificationContext,
    ClassificationEntities,
    ClassificationError,
    ClassificationResult,
    TicketClassification,
    TicketIntent,
)
from supportops_agent.tools.authorization import (  # noqa: E402
    AuthorizationDecision,
    AuthorizationReason,
    AuthorizationResult,
)


def _classification(order_id=None, reason=None, intents=(TicketIntent.REFUND_REQUEST,)):
    return TicketClassification(
        intents=list(intents),
        primary_intent=intents[0],
        entities=ClassificationEntities(order_id=order_id),
        context=ClassificationContext(exception_requested=True, claimed_reason=reason),
        requires_clarification=False,
    )


class _Cls:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    def classify(self, ticket):
        if self.exc:
            raise self.exc
        return self.result


class _UsageClient(_Client):
    def __init__(self, usage, rid="r1", text="Safe answer."):
        super().__init__()
        self._usage, self._rid, self._text = usage, rid, text

    def create(self, **kw):
        self.requests.append(kw)
        r = _Response(self._rid, text=self._text)
        r.usage = self._usage
        return r


def _final(agent):
    return [s for s in agent.last_run.steps if s.step_type == AgentStepType.FINAL][0]


# 1. секрет в финальном ответе: клиент получает ответ, trace — только метаданные
def test_final_answer_secret_not_in_trace_but_returned_to_client():
    client = _UsageClient(None, text=f"Hi {SECRET} {EMAIL}")
    agent = SupportAgent(client, context=support_agent_context())
    answer = agent.run(_ticket())
    assert answer == f"Hi {SECRET} {EMAIL}"  # 15. клиентский ответ цел
    _assert_clean(agent)
    assert _final(agent).output == {
        "answer_length": len(answer),
        "response_generated": True,
    }


# 2-3. claimed_reason и order_id; 4. неизвестный intent
def test_classification_secrets_not_in_trace_but_in_llm_context():
    cls = _classification(order_id=SECRET, reason=f"{EMAIL} {CARD}")
    client = _Client()
    agent = SupportAgent(client, _Cls(ClassificationResult(cls, model="gpt-4.1-mini")),
                         context=support_agent_context())
    agent.run(_ticket())
    _assert_clean(agent)
    step = agent.last_run.steps[0]
    assert step.step_type == AgentStepType.CLASSIFICATION
    assert step.output["intents"] == ["refund_request"]
    assert step.output["has_order_id"] is True and step.output["has_claimed_reason"] is True
    # бизнес-контекст LLM не изменён
    ctx = json.dumps(client.requests[0]["input"])
    assert SECRET in ctx and EMAIL in ctx
    # объект классификации не изменён
    assert cls.entities.order_id == SECRET and cls.context.claimed_reason == f"{EMAIL} {CARD}"


def test_unknown_intent_replaced_with_fixed_label():
    cls = TicketClassification.model_construct(
        intents=[SECRET, TicketIntent.COMPLAINT],
        primary_intent=SECRET,
        entities=ClassificationEntities(order_id=None),
        context=ClassificationContext(exception_requested=False, claimed_reason=None),
        requires_clarification=False,
    )
    out = agent_module._classification_diagnostics(cls)
    assert SECRET not in json.dumps(out)
    assert out["intents"] == ["unknown_intent", "complaint"]
    assert out["primary_intent"] == "unknown_intent"
    assert agent_module._safe_model(SECRET) == "unknown_model"


def test_classification_diagnostics_never_raise_on_broken_object():
    class _Broken:
        @property
        def intents(self):
            raise RuntimeError(SECRET)

    assert agent_module._classification_diagnostics(_Broken()) == {
        "classification_summary_unavailable": True
    }


# 5. секрет в response_id (классификация и LLM_CALL)
def test_response_id_never_in_trace():
    cls = _classification()
    res = ClassificationResult(cls, model="gpt-4.1-mini", response_id=f"id_{SECRET}")
    agent = SupportAgent(_UsageClient(None, rid=f"resp_{SECRET}"), _Cls(res),
                         context=support_agent_context())
    agent.run(_ticket())
    _assert_clean(agent)
    for step in agent.last_run.steps:
        assert "response_id" not in (step.output or {})


# 6-7. неизвестные поля usage и невалидные метрики
class _Usage:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_usage_unknown_fields_and_invalid_metrics():
    ok = {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
    assert agent_module._safe_usage(ok) == ok
    assert agent_module._safe_usage({**ok, "note": SECRET, "x": {"a": SECRET}}) == ok
    assert agent_module._safe_usage({**ok, "input_tokens": SECRET}) == {
        "output_tokens": 4, "total_tokens": 7}
    for bad in (SECRET, ["x"], 5, {"total_tokens": SECRET}, {"total_tokens": -1},
                {"total_tokens": float("nan")}, {"total_tokens": float("inf")},
                {"total_tokens": True}, {"total_tokens": 10**12}, {"total_tokens": 1.5},
                {"input_tokens": 1}, {}):
        assert agent_module._safe_usage(bad) is None
    # через реальный прогон: usage сторонних объектов не просачивается
    client = _UsageClient(_Usage(input_tokens=1, output_tokens=2, total_tokens=3, junk=SECRET))
    agent = SupportAgent(client, context=support_agent_context())
    agent.run(_ticket())
    _assert_clean(agent)
    assert agent.last_run.summary()["total_tokens"] == 3
    # невалидный total: summary не падает
    client = _UsageClient(_Usage(input_tokens=1, output_tokens=2, total_tokens=SECRET))
    agent = SupportAgent(client, context=support_agent_context())
    agent.run(_ticket())
    _assert_clean(agent)
    assert agent.last_run.summary()["total_tokens"] is None


# 8. секрет в reason_code (кастомная политика, результат в обход валидации)
def test_policy_reason_code_secret_not_in_error_or_trace():
    class _EvilPolicy:
        def authorize(self, tool, context):
            return AuthorizationResult.model_construct(
                decision=AuthorizationDecision.DENY, reason_code=SECRET)

    client = _Client(_Call("lookup_order", {"order_id": "order_001"}))
    agent = SupportAgent(client, context=support_agent_context(), policy=_EvilPolicy())
    agent.run(_ticket())
    _assert_clean(agent)
    step = _tool_steps(agent)[0]
    assert "POLICY_INVALID_RESULT" in step.error
    assert SECRET not in json.dumps(client.requests[1]["input"])  # и в контексте LLM


def test_policy_valid_reason_unchanged_and_allow_not_affected():
    client = _Client(_Call("lookup_order", {"order_id": "order_001"}))
    agent = SupportAgent(client, context=None)
    agent.run(_ticket())
    assert "MISSING_CONTEXT" in _tool_steps(agent)[0].error
    agent, _ = _run(_Call("lookup_order", {"order_id": "order_001"}))
    assert _tool_steps(agent)[0].error is None


# 9. успешный workflow / 13-14. сериализация и summary
def test_success_workflow_full_serialization_and_summary():
    agent, _ = _run(_Call("lookup_order", {"order_id": SECRET}), message=f"{SECRET}{CARD}")
    _assert_clean(agent)
    json.loads(agent.last_run.model_dump_json())
    assert set(agent.last_run.summary()) >= {"status", "degraded", "total_tokens", "error"}


# 10. degraded classification (M03-контракт)
def test_degraded_classification_contract_preserved():
    exc = ClassificationError("api_error", f"{SECRET} {EMAIL}")
    client = _Client()
    agent = SupportAgent(client, _Cls(exc=exc), context=support_agent_context())
    answer = agent.run(_ticket())
    assert answer == agent_module.CLASSIFICATION_FAILURE_RESPONSE
    assert client.requests == []  # нет LLM/tool-вызовов
    _assert_clean(agent)
    assert _final(agent).output == {
        "answer_length": len(answer),
        "response_generated": True,
        "degraded": True,
        "classification_failed": True,
    }
    s = agent.last_run.summary()
    assert s["status"] == AgentRunStatus.COMPLETED and s["degraded"] is True


# 11. unexpected classification failure
def test_unexpected_classification_failure_clean_and_propagated():
    agent = SupportAgent(_Client(), _Cls(exc=RuntimeError(SECRET)),
                         context=support_agent_context())
    with pytest.raises(RuntimeError):
        agent.run(_ticket())
    _assert_clean(agent)
    assert agent.last_run.status == AgentRunStatus.FAILED


# 12. authorization denied — полный trace без маркеров (см. также выше)
def test_authorization_denied_full_trace_clean():
    agent = SupportAgent(_Client(_Call("lookup_order", {"order_id": SECRET})), context=None)
    agent.run(_ticket(message=f"{SECRET} {EMAIL}"))
    _assert_clean(agent)


# model: сторонняя строка не попадает в trace
def test_third_party_model_name_not_in_trace():
    res = ClassificationResult(_classification(), model=f"model_{SECRET}")
    agent = SupportAgent(_Client(), _Cls(res), context=support_agent_context())
    agent.run(_ticket())
    _assert_clean(agent)


def test_classification_usage_sanitized_in_trace():
    res = ClassificationResult(
        _classification(), model="gpt-4.1-mini",
        usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "note": SECRET},
    )
    agent = SupportAgent(_Client(), _Cls(res), context=support_agent_context())
    agent.run(_ticket())
    _assert_clean(agent)
    assert agent.last_run.steps[0].output["usage"] == {
        "input_tokens": 1, "output_tokens": 2, "total_tokens": 3}


# ===== H5-review: POLICY_DECISION и diagnostic_type =====
def test_policy_decision_hostile_evaluator_not_in_trace():
    from supportops_agent.business.policy import PolicyDecisionType
    from supportops_agent.business.refund_evaluation import (
        RefundEvaluationResult, RefundEvaluationStatus)

    hostile = RefundEvaluationResult.model_construct(
        status=RefundEvaluationStatus.POLICY_ERROR,
        reason_code=f"rc_{SECRET}",
        order_id=f"oid_{EMAIL}",
        decision=type("D", (), {
            "decision": PolicyDecisionType.DENY, "policy_version": f"pv_{CARD}",
            "days_since_delivery": SECRET})(),
        provenance={"k": SECRET},
    )

    class _Ev:
        def evaluate(self, ticket, classification):
            return hostile

    agent = SupportAgent(_Client(), _Cls(ClassificationResult(_classification(), model="m")),
                         context=support_agent_context(), refund_evaluator=_Ev())
    agent.run(_ticket())
    _assert_clean(agent)
    step = [s for s in agent.last_run.steps if s.step_type == AgentStepType.POLICY_DECISION][0]
    assert step.output["reason_code"] == "unknown_reason"
    assert step.output["policy_version"] is None
    assert step.output["days_since_delivery"] is None
    assert step.output["decision"] == "deny"
    assert step.error == "unknown_reason"
    bad = type("R", (), {"status": SECRET, "reason_code": SECRET, "order_id": SECRET,
                         "provenance": SECRET, "decision": type("D", (), {
                             "decision": SECRET, "policy_version": SECRET,
                             "days_since_delivery": SECRET})()})()
    out = agent_module._policy_decision_diagnostics(bad)
    assert SECRET not in json.dumps(out) and out["decision"] is None


def test_policy_diagnostics_never_raise():
    assert agent_module._policy_decision_diagnostics(object()) == {
        "policy_summary_unavailable": True}


def test_diagnostic_type_normalized():
    assert agent_module._safe_diagnostic_type("RuntimeError") == "RuntimeError"
    for bad in (SECRET + " x", f"a\n{EMAIL}", "", None, "x" * 100):
        assert agent_module._safe_diagnostic_type(bad) == "PolicyError"


def test_diagnostic_type_hostile_exception_class_name_end_to_end():
    boom = type(f"{SECRET} {EMAIL}", (Exception,), {})

    class _Raising:
        def authorize(self, tool, context):
            raise boom("x")

    client = _Client(_Call("lookup_order", {"order_id": "order_001"}))
    agent = SupportAgent(client, context=support_agent_context(), policy=_Raising())
    agent.run(_ticket())
    _assert_clean(agent)
    step = _tool_steps(agent)[0]
    assert step.input["diagnostic_type"] == "PolicyError"
    assert step.input["reason_code"] == "POLICY_ERROR"


def test_diagnostic_type_syntactically_valid_secret_class_name_end_to_end(caplog):
    """SR-4 MEDIUM: валидный идентификатор не должен попадать в trace/summary/логи."""
    boom = type(SECRET, (Exception,), {})  # имя = SYNTH_SECRET_ABC123

    class _Raising:
        def authorize(self, tool, context):
            raise boom("x")

    client = _Client(_Call("lookup_order", {"order_id": "order_001"}))
    agent = SupportAgent(client, context=support_agent_context(), policy=_Raising())
    with caplog.at_level("DEBUG"):
        agent.run(_ticket())
    assert SECRET not in agent.last_run.model_dump_json()
    assert SECRET not in json.dumps(agent.last_run.summary(), default=str)
    assert SECRET not in caplog.text
    assert "diagnostic_type=PolicyError" in caplog.text
    step = _tool_steps(agent)[0]
    assert step.input["diagnostic_type"] == "PolicyError"
    assert step.input["reason_code"] == "POLICY_ERROR"
    # fail-closed: инструмент не выполнялся
    assert step.output is None or "order" not in json.dumps(step.output)
    assert agent.last_run.status == AgentRunStatus.COMPLETED


def test_diagnostic_type_allowlist_is_closed():
    assert agent_module._safe_diagnostic_type(SECRET) == "PolicyError"
    assert agent_module._safe_diagnostic_type("RuntimeError") == "RuntimeError"
