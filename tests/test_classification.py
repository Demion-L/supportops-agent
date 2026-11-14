import openai
import pytest
from pydantic import ValidationError

from supportops_agent import tool_executor
from supportops_agent.agent import CLASSIFICATION_FAILURE_RESPONSE, SupportAgent
from supportops_agent.classification import (
    ClassificationContext,
    ClassificationEntities,
    ClassificationError,
    ClassificationResult,
    OpenAITicketClassifier,
    TicketClassification,
    TicketIntent,
)
from supportops_agent.models import AgentRunStatus, AgentStepType, SupportTicket


def make_classification(intents, primary=None, *, order_id=None,
                        exception=False, reason=None, clarify=False):
    intents = [TicketIntent(i) for i in intents]
    return TicketClassification(
        intents=intents,
        primary_intent=TicketIntent(primary) if primary else intents[0],
        entities=ClassificationEntities(order_id=order_id),
        context=ClassificationContext(
            exception_requested=exception, claimed_reason=reason
        ),
        requires_clarification=clarify,
    )


def ticket(message="Where is my order_001?"):
    return SupportTicket(ticket_id="t1", customer_id="c1", message=message)


# ---- fake OpenAI -----------------------------------------------------------

class FakeUsage:
    input_tokens, output_tokens, total_tokens = 10, 5, 15


class FakeParsed:
    id = "cls_1"
    usage = FakeUsage()

    def __init__(self, parsed):
        self.output_parsed = parsed


class FakeResponse:
    def __init__(self, response_id, output=None, output_text=""):
        self.id = response_id
        self.output = output or []
        self.output_text = output_text
        self.usage = None


class FakeResponses:
    """parse() — классификация, create() — основной agent loop."""

    def __init__(self, parsed=None, parse_exc=None):
        self.parsed, self.parse_exc = parsed, parse_exc
        self.parse_calls, self.create_calls = [], []

    def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        if self.parse_exc:
            raise self.parse_exc
        return FakeParsed(self.parsed)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return FakeResponse("r1", output_text="done")


class FakeClient:
    def __init__(self, **kw):
        self.responses = FakeResponses(**kw)


class StubClassifier:
    def __init__(self, classification=None, exc=None):
        self.classification, self.exc, self.calls = classification, exc, 0

    def classify(self, ticket):
        self.calls += 1
        if self.exc:
            raise self.exc
        return ClassificationResult(self.classification, model="m", response_id="c1")


# ---- classifier: сценарии из ТЗ --------------------------------------------

@pytest.mark.parametrize(
    "message,expected",
    [
        ("Where is my order_001?",
         make_classification(["order_status"], order_id="order_001")),
        ("My order never arrived and I want a refund.",
         make_classification(["order_status", "refund_request"], "refund_request")),
        ("Your support team has ignored my messages for two weeks.",
         make_classification(["complaint"])),
        ("What are your business hours?", make_classification(["general_inquiry"])),
        ("asdf ???",
         make_classification(["unknown"], clarify=True)),
    ],
)
def test_classifier_returns_structured_result(message, expected):
    client = FakeClient(parsed=expected)
    result = OpenAITicketClassifier(client).classify(ticket(message))

    assert result.classification == expected
    assert result.usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    call = client.responses.parse_calls[0]
    assert call["text_format"] is TicketClassification
    assert call["input"] == message
    assert "tools" not in call  # классификатор не знает о бизнес-инструментах


def test_exception_context_is_a_claim_without_decision():
    expected = make_classification(
        ["refund_request"], exception=True, reason="hospitalization"
    )
    result = OpenAITicketClassifier(FakeClient(parsed=expected)).classify(
        ticket("I received my order 43 days ago, but I was hospitalized. "
               "Can I still return it?")
    )
    c = result.classification
    assert c.primary_intent == TicketIntent.REFUND_REQUEST
    assert c.context.exception_requested is True
    assert c.context.claimed_reason == "hospitalization"
    # в контракте нет полей-решений
    assert not {"refund_allowed", "eligible", "decision"} & set(
        TicketClassification.model_fields
    )


# ---- ошибки ------------------------------------------------------------------

def test_empty_parsed_output_raises_invalid_output():
    with pytest.raises(ClassificationError) as e:
        OpenAITicketClassifier(FakeClient(parsed=None)).classify(ticket())
    assert e.value.kind == "invalid_output"


def test_pydantic_violation_from_sdk_raises_invalid_output():
    try:
        TicketClassification.model_validate({"intents": []})
    except ValidationError as exc:
        err = exc
    with pytest.raises(ClassificationError) as e:
        OpenAITicketClassifier(FakeClient(parse_exc=err)).classify(ticket())
    assert e.value.kind == "invalid_output"


def test_api_failure_raises_api_error():
    exc = openai.OpenAIError("connection down")
    with pytest.raises(ClassificationError) as e:
        OpenAITicketClassifier(FakeClient(parse_exc=exc)).classify(ticket())
    assert e.value.kind == "api_error"


def test_programming_error_in_classifier_is_not_masked_as_api_error():
    with pytest.raises(TypeError):
        OpenAITicketClassifier(FakeClient(parse_exc=TypeError("bug"))).classify(
            ticket()
        )


def test_contract_invariants():
    with pytest.raises(ValidationError):
        make_classification(["order_status"], "refund_request")  # primary вне intents
    with pytest.raises(ValidationError):
        make_classification(["unknown", "complaint"], clarify=True)
    with pytest.raises(ValidationError):
        make_classification(["unknown"], clarify=False)
    with pytest.raises(ValidationError):
        make_classification(["complaint", "complaint"])
    with pytest.raises(ValidationError):
        TicketClassification.model_validate(
            {**make_classification(["complaint"]).model_dump(), "extra": 1}
        )


def test_fallback_is_unknown_and_needs_clarification():
    fb = TicketClassification.fallback()
    assert fb.intents == [TicketIntent.UNKNOWN]
    assert fb.requires_clarification is True


# ---- интеграция с SupportAgent -----------------------------------------------

def test_agent_records_classification_step_first_and_enriches_context():
    cls = make_classification(
        ["refund_request"], exception=True, reason="hospitalization"
    )
    client = FakeClient()
    agent = SupportAgent(client, classifier=StubClassifier(cls))

    assert agent.run(ticket("Can I return it? I was hospitalized.")) == "done"

    steps = agent.last_run.steps
    assert [s.step_type for s in steps] == [
        AgentStepType.CLASSIFICATION, AgentStepType.LLM_CALL, AgentStepType.FINAL
    ]
    assert [s.step_number for s in steps] == [1, 2, 3]
    assert steps[0].error is None
    assert steps[0].output["classification"]["primary_intent"] == "refund_request"
    assert "hospitalized" not in str(steps[0].input)  # текст обращения не в trace
    assert agent.last_run.status == AgentRunStatus.COMPLETED

    sent = client.responses.create_calls[0]
    assert "tools" in sent  # agent loop не ограничен
    dev, user = sent["input"]
    assert dev["role"] == "developer" and "unverified" in dev["content"]
    assert user == {"role": "user", "content": "Can I return it? I was hospitalized."}
    assert len(client.responses.create_calls) == 1  # без скрытых доп. LLM-вызовов


def test_agent_classification_failure_is_traced_and_run_fails_closed():
    # M03 (K1): ожидаемая ошибка классификации больше не продолжает прогон.
    client = FakeClient()
    agent = SupportAgent(client, classifier=StubClassifier(exc=ClassificationError(
        "api_error", "down")))

    assert agent.run(ticket("hello")) == CLASSIFICATION_FAILURE_RESPONSE

    first = agent.last_run.steps[0]
    assert first.step_type == AgentStepType.CLASSIFICATION
    assert first.error and "api_error" in first.error
    assert first.output == {"fallback": True}
    assert client.responses.create_calls == []  # LLM-цикл не запускался
    summary = agent.last_run.summary()
    assert summary["classification_failed"] is True and summary["degraded"] is True
    assert [s.step_type for s in agent.last_run.steps] == [
        AgentStepType.CLASSIFICATION, AgentStepType.FINAL
    ]
    assert [s.step_number for s in agent.last_run.steps] == [1, 2]


def test_agent_without_classifier_is_unchanged():
    client = FakeClient()
    agent = SupportAgent(client)
    agent.run(ticket("hi"))
    assert [s.step_type for s in agent.last_run.steps] == [
        AgentStepType.LLM_CALL, AgentStepType.FINAL
    ]
    assert client.responses.create_calls[0]["input"] == "hi"


def test_classifier_does_not_execute_business_tools(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("business tool called")

    monkeypatch.setattr(tool_executor, "execute_tool_call", boom)
    client = FakeClient(parsed=make_classification(["order_status"]))
    OpenAITicketClassifier(client).classify(ticket())
    assert client.responses.create_calls == []


# ---- классификация: ожидаемые vs неожиданные ошибки ---------------------------

def test_unexpected_classifier_error_fails_run_with_diagnostics():
    client = FakeClient()
    agent = SupportAgent(client, classifier=StubClassifier(exc=KeyError("bug")))

    with pytest.raises(KeyError):
        agent.run(ticket("secret text"))

    run = agent.last_run
    assert run.status == AgentRunStatus.FAILED
    assert run.finished_at is not None
    assert [s.step_type for s in run.steps] == [
        AgentStepType.CLASSIFICATION, AgentStepType.FAILURE
    ]
    assert [s.step_number for s in run.steps] == [1, 2]
    cls_step = run.steps[0]
    assert cls_step.error == "Unexpected classifier error [classification_error]"
    assert "KeyError" not in cls_step.error and "bug" not in cls_step.error
    assert cls_step.output == {"fallback": False, "unexpected_error": True}
    assert "secret text" not in str(cls_step.input)
    assert run.steps[1].error == "agent_execution_error"  # безопасная категория в FAILURE
    assert client.responses.create_calls == []  # основной LLM-вызов не выполнялся
    assert run.summary()["classification_failed"] is True


@pytest.mark.parametrize("kind", ["api_error", "invalid_output"])
def test_expected_classification_errors_fail_closed_with_controlled_fallback(kind):
    client = FakeClient()
    agent = SupportAgent(
        client, classifier=StubClassifier(exc=ClassificationError(kind, "x"))
    )
    assert agent.run(ticket("hi")) == CLASSIFICATION_FAILURE_RESPONSE
    assert client.responses.create_calls == []
    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert agent.last_run.steps[0].output == {"fallback": True}


# ---- безопасность fallback ------------------------------------------------------
# Инвариант: ошибка классификации не расширяет полномочия агента.
# Авторизация (authorization.py) не зависит от классификации; см. tests/test_authorization.py.

class FnCall:
    type = "function_call"

    def __init__(self, name, arguments, call_id="call_1"):
        self.name, self.arguments, self.call_id = name, arguments, call_id


class ScriptedResponses(FakeResponses):
    """Первый ответ модели — вызов инструмента, затем финальный ответ."""

    def __init__(self, call):
        super().__init__()
        self.call = call

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if len(self.create_calls) == 1:
            r = FakeResponse("r1", output=[self.call])
        else:
            r = FakeResponse("r2", output_text="done")
        return r


def make_agent(call, classifier, context=None):
    client = FakeClient()
    client.responses = ScriptedResponses(call)
    return SupportAgent(client, classifier=classifier, context=context), client


FAILING = lambda: StubClassifier(exc=ClassificationError("api_error", "down"))  # noqa: E731
UNKNOWN = lambda: StubClassifier(TicketClassification.fallback())  # noqa: E731
# Классификаторы, при которых прогон продолжается. FAILING сюда не входит:
# ожидаемая ошибка классификации завершает прогон до LLM-цикла (M03,
# см. tests/test_classification_fail_closed.py).
CLASSIFIERS = {"none": lambda: None, "unknown": UNKNOWN}


@pytest.mark.parametrize("name", CLASSIFIERS)
def test_tool_availability_does_not_depend_on_classification(name):
    agent, client = make_agent(FnCall("lookup_order", '{"order_id": "x"}'),
                               CLASSIFIERS[name]())
    agent.run(ticket())
    tools = client.responses.create_calls[0]["tools"]
    assert [t["name"] for t in tools] == list(tool_executor.TOOL_REGISTRY)


@pytest.mark.parametrize("name", CLASSIFIERS)
def test_unregistered_tool_still_rejected_regardless_of_classification(name):
    agent, _ = make_agent(FnCall("issue_refund", '{"order_id": "o1"}'),
                          CLASSIFIERS[name]())
    with pytest.raises(Exception):
        agent.run(ticket())
    assert agent.last_run.status == AgentRunStatus.FAILED


@pytest.mark.parametrize("name", CLASSIFIERS)
def test_invalid_tool_arguments_still_rejected_regardless_of_classification(name):
    agent, _ = make_agent(
        FnCall("create_support_case", '{"reason": "r"}'),
        CLASSIFIERS[name](),
    )
    agent.run(ticket())
    tool_step = [s for s in agent.last_run.steps
                 if s.step_type == AgentStepType.TOOL_EXECUTION][0]
    assert tool_step.error and "Invalid arguments" in tool_step.error


def test_authorization_does_not_depend_on_classification():
    """Полномочия задаёт только явный доверенный context, не классификация."""
    from supportops_agent import mock_data
    from supportops_agent.authorization import support_agent_context

    args = '{"reason": "r", "priority": "high"}'
    for name in CLASSIFIERS:
        mock_data.CASES.clear()
        agent, _ = make_agent(
            FnCall("create_support_case", args), CLASSIFIERS[name](),
            context=support_agent_context(),
        )
        agent.run(ticket())
        assert len(mock_data.CASES) == 1, name
