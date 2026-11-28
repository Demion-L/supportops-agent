"""SEC-003 / M04 / WP2: exception text never reaches LLM context, trace, summary
or customer-facing responses (R1-I1 ... R1-I12).

Only the closed set of codes from the approved design may be emitted instead of
`str(exc)`: validation_error, tool_execution_error, classification_error,
agent_execution_error, unknown_error (+ the special `invalid_arguments`).
"""

import json

import pytest

from supportops_agent.business import mock_data
from supportops_agent.runtime.agent import (
    CLASSIFICATION_FAILURE_RESPONSE,
    SupportAgent,
    _failure_code,
)
from supportops_agent.tools.authorization import support_agent_context
from supportops_agent.classification import ClassificationError
from supportops_agent.models import AgentRunStatus, AgentStepType, SupportTicket
from supportops_agent.tools.definitions import TOOL_REGISTRY
from supportops_agent.tools.executor import INVALID_ARGUMENTS_CODE, SAFE_ERROR_CODES

PII = "customer_email=private@example.test"
SECRET = "secret_customer_data"
ARG_SECRET = "SYNTH_ARG_SECRET_42"
FALLBACK = (
    "We're unable to process your request right now. "
    "Please try again later or contact support."
)


class _Call:
    type = "function_call"

    def __init__(self, name, arguments, call_id="call_1"):
        self.call_id, self.name = call_id, name
        self.arguments = arguments if isinstance(arguments, str) else json.dumps(arguments)


class _Response:
    def __init__(self, rid, output=None, text="done"):
        self.id, self.output, self.output_text, self.usage = rid, output or [], text, None


class _Responses:
    def __init__(self, first_call=None, raise_exc=None):
        self.first_call, self.raise_exc = first_call, raise_exc
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc is not None:
            raise self.raise_exc
        if len(self.calls) == 1 and self.first_call is not None:
            return _Response("r1", [self.first_call])
        return _Response(f"r{len(self.calls)}", text="Safe customer answer.")


class _Client:
    def __init__(self, **kw):
        self.responses = _Responses(**kw)


class _Classifier:
    def __init__(self, exc):
        self.exc, self.calls = exc, 0

    def classify(self, ticket):
        self.calls += 1
        raise self.exc


def _ticket(tid="ticket_A", cust="customer_001"):
    return SupportTicket(ticket_id=tid, customer_id=cust, message="help me")


def _agent(client, classifier=None):
    return SupportAgent(client, classifier, context=support_agent_context())


def _artifacts(agent):
    """Everything that can leak: trace JSON, summary JSON."""
    run = agent.last_run
    return run.model_dump_json() + json.dumps(run.summary(), default=str)


def _llm_inputs(client):
    return json.dumps([c.get("input") for c in client.responses.calls], default=str)


def _boom(exc):
    def handler(**_):
        raise exc

    return handler


# R1-T1 / P1
def test_t1_tool_handler_exception_text_is_not_exposed(monkeypatch):
    monkeypatch.setattr(
        TOOL_REGISTRY["lookup_order"], "handler", _boom(RuntimeError(PII))
    )
    client = _Client(first_call=_Call("lookup_order", {"order_id": "order_001"}))
    agent = _agent(client)

    answer = agent.run(_ticket())

    assert agent.last_run.status == AgentRunStatus.COMPLETED
    step = [s for s in agent.last_run.steps if s.step_type == AgentStepType.TOOL_EXECUTION][0]
    assert step.error == "Tool 'lookup_order' failed [tool_execution_error]"
    assert PII not in _artifacts(agent)
    assert PII not in _llm_inputs(client)
    assert "private@example.test" not in answer
    assert "tool_execution_error" in _llm_inputs(client)


def test_t1_tool_result_error_has_no_exception_text(monkeypatch):
    monkeypatch.setattr(
        TOOL_REGISTRY["lookup_order"], "handler", _boom(ValueError(PII))
    )
    agent = _agent(_Client())
    agent.state = None

    result = agent.execute_function_call(
        _Call("lookup_order", {"order_id": "order_001"})
    )

    assert result.error_type == "execution_failed"
    assert result.output is None
    assert PII not in result.model_dump_json()


# R1-T2 / P2
@pytest.mark.parametrize(
    "arguments",
    [
        {"order_id": "order_001", "note": ARG_SECRET},  # extra_forbidden
        {"order_id": {"x": ARG_SECRET}},  # wrong type, input_value echoed by pydantic
        {"order_id": ARG_SECRET * 100},  # too long, input_value echoed
        '{"order_id": "' + ARG_SECRET,  # broken JSON
    ],
)
def test_t2_validation_error_exposes_only_invalid_arguments(arguments):
    client = _Client(first_call=_Call("lookup_order", arguments))
    agent = _agent(client)

    agent.run(_ticket())

    step = [s for s in agent.last_run.steps if s.step_type == AgentStepType.TOOL_EXECUTION][0]
    assert step.error == "Invalid arguments for tool 'lookup_order' [invalid_arguments]"
    assert INVALID_ARGUMENTS_CODE == "invalid_arguments"
    assert ARG_SECRET not in step.error
    assert ARG_SECRET not in _llm_inputs(client)
    result = agent.execute_function_call(_Call("lookup_order", arguments))
    assert result.error_type == "invalid_arguments"
    assert ARG_SECRET not in result.model_dump_json()


# R1-T3 / P3
def test_t3_unexpected_agent_failure_is_failed_and_propagated():
    exc = RuntimeError(PII)
    client = _Client(raise_exc=exc)
    agent = _agent(client)

    with pytest.raises(RuntimeError) as info:
        agent.run(_ticket())

    assert info.value is exc  # original exception is re-raised untouched
    run = agent.last_run
    assert run.status == AgentRunStatus.FAILED
    assert run.finished_at is not None
    assert run.steps[-1].step_type == AgentStepType.FAILURE
    assert run.steps[-1].error == "agent_execution_error"
    assert run.summary()["error"] == "agent_execution_error"
    assert PII not in _artifacts(agent)


# R1-T4 / P4
def test_t4_unexpected_classifier_failure():
    exc = RuntimeError(SECRET)
    classifier = _Classifier(exc)
    client = _Client()
    agent = _agent(client, classifier)

    with pytest.raises(RuntimeError) as info:
        agent.run(_ticket())

    assert info.value is exc
    run = agent.last_run
    assert run.status == AgentRunStatus.FAILED
    cls_step, fail_step = run.steps
    assert cls_step.step_type == AgentStepType.CLASSIFICATION
    assert cls_step.error == "Unexpected classifier error [classification_error]"
    assert fail_step.step_type == AgentStepType.FAILURE
    assert SECRET not in _artifacts(agent)
    assert client.responses.calls == []  # no LLM call, hence no tools


# R1-T5 / M03
@pytest.mark.parametrize("kind", ["api_error", "invalid_output", "weird " + SECRET])
def test_t5_expected_classification_error_keeps_m03_contract(kind):
    classifier = _Classifier(ClassificationError(kind, SECRET))
    client = _Client()
    agent = _agent(client, classifier)

    answer = agent.run(_ticket())

    assert answer == FALLBACK == CLASSIFICATION_FAILURE_RESPONSE
    run = agent.last_run
    assert run.status == AgentRunStatus.COMPLETED
    final = run.steps[-1]
    assert final.output["degraded"] is True
    assert client.responses.calls == []
    assert mock_data.CASES == {}
    assert SECRET not in _artifacts(agent)
    assert not [s for s in run.steps if s.step_type == AgentStepType.TOOL_EXECUTION]


# R1-T6 / P5
def test_t6_unknown_tool_does_not_leak_arguments():
    call = _Call("drop_database_" + ARG_SECRET, {"token": ARG_SECRET})
    client = _Client(first_call=call)
    agent = _agent(client)

    with pytest.raises(ValueError):  # existing semantics: propagates
        agent.run(_ticket())

    run = agent.last_run
    assert run.status == AgentRunStatus.FAILED
    assert run.steps[-1].error == "validation_error"
    assert ARG_SECRET not in _artifacts(agent)
    assert ARG_SECRET not in _llm_inputs(client)


# R1-T7
def test_t7_classifier_none_behaviour_preserved():
    client = _Client(first_call=_Call("lookup_order", {"order_id": "order_001"}))
    agent = _agent(client, classifier=None)

    answer = agent.run(_ticket())

    assert answer == "Safe customer answer."
    run = agent.last_run
    assert run.status == AgentRunStatus.COMPLETED
    assert not [s for s in run.steps if s.step_type == AgentStepType.CLASSIFICATION]
    assert not [s for s in run.steps if s.error]


# R1-T8
def test_t8_customer_response_has_no_diagnostics(monkeypatch):
    monkeypatch.setattr(
        TOOL_REGISTRY["lookup_order"], "handler", _boom(RuntimeError(PII))
    )
    client = _Client(first_call=_Call("lookup_order", {"order_id": "order_001"}))
    agent = _agent(client)

    answer = agent.run(_ticket())

    for text in (PII, "private@example.test", "tool_execution_error", "RuntimeError"):
        assert text not in answer
    assert CLASSIFICATION_FAILURE_RESPONSE == FALLBACK


# R1-T9: WP1 regression
def test_t9_wp1_trusted_binding_and_stale_guard_still_work():
    client = _Client(
        first_call=_Call(
            "create_support_case", {"reason": "need help", "priority": "high"}
        )
    )
    agent = _agent(client)
    agent.run(_ticket("ticket_A", "customer_001"))

    assert len(mock_data.CASES) == 1
    case = next(iter(mock_data.CASES.values()))
    assert case.ticket_id == "ticket_A"

    # stale guard: after run() the trusted context is gone
    before = dict(mock_data.CASES)
    result = agent.execute_function_call(
        _Call("create_support_case", {"reason": "other", "priority": "low"})
    )
    assert result.output == {"outcome": "identity_verification_required"}
    assert mock_data.CASES == before

    # model-supplied ticket_id is rejected, not honoured
    forged = agent.execute_function_call(
        _Call(
            "create_support_case",
            {"ticket_id": "ticket_X", "reason": "r", "priority": "low"},
        )
    )
    assert forged.error_type == "invalid_arguments"


# R1-T10: closed set of codes
def test_t10_safe_codes_belong_to_closed_set():
    assert SAFE_ERROR_CODES == {
        "validation_error",
        "tool_execution_error",
        "classification_error",
        "agent_execution_error",
        "unknown_error",
    }

    class PIIError(Exception):
        pass

    class PIIValueError(ValueError):
        pass

    samples = [
        RuntimeError(PII),
        PIIError(PII),
        PIIValueError(PII),
        KeyError(PII),
        ClassificationError("api_error", PII),
        OSError(PII),
    ]
    for exc in samples:
        code = _failure_code(exc)
        assert code in SAFE_ERROR_CODES
        assert PII not in code and type(exc).__name__ not in code

    assert _failure_code(PIIValueError(PII)) == "agent_execution_error"
    assert _failure_code(ClassificationError("x", PII)) == "classification_error"
    assert _failure_code(PIIError(PII)) == "agent_execution_error"
    assert _failure_code(KeyboardInterrupt()) == "unknown_error"


def test_t10_every_emitted_code_is_from_closed_set(monkeypatch):
    monkeypatch.setattr(
        TOOL_REGISTRY["lookup_order"], "handler", _boom(RuntimeError(PII))
    )
    allowed = SAFE_ERROR_CODES | {INVALID_ARGUMENTS_CODE}
    scenarios = [
        _agent(_Client(raise_exc=RuntimeError(PII))),
        _agent(_Client(), _Classifier(RuntimeError(SECRET))),
        _agent(_Client(first_call=_Call("lookup_order", {"order_id": "order_001"}))),
    ]
    for agent in scenarios:
        try:
            agent.run(_ticket())
        except Exception:
            pass
        for step in agent.last_run.steps:
            if step.error is None:
                continue
            assert any(code in step.error for code in allowed), step.error
            assert PII not in step.error and SECRET not in step.error


# Review finding: subclass name of ClassificationError must not reach the trace.
def test_expected_classification_error_subclass_name_not_in_trace():
    class PrivateNamedError_synthetic_pii(ClassificationError):
        pass

    classifier = _Classifier(PrivateNamedError_synthetic_pii("api_error", SECRET))
    agent = _agent(_Client(), classifier)

    assert agent.run(_ticket()) == FALLBACK
    cls_step = agent.last_run.steps[0]
    assert cls_step.error == "ClassificationError: api_error"
    assert "synthetic_pii" not in _artifacts(agent)


# WP2-R1: an internal ValueError must not be labelled as a validation error.
def test_wp2_r1_internal_value_error_is_not_validation_error():
    exc = ValueError("Internal database configuration failure")
    assert _failure_code(exc) == "agent_execution_error"

    agent = _agent(_Client(raise_exc=exc))
    with pytest.raises(ValueError) as info:
        agent.run(_ticket())
    assert info.value is exc
    assert agent.last_run.status == AgentRunStatus.FAILED
    assert agent.last_run.steps[-1].error == "agent_execution_error"
    assert "database" not in _artifacts(agent)


def test_wp2_r1_json_and_pydantic_errors_are_validation_error():
    import json as _json

    from pydantic import TypeAdapter, ValidationError

    with pytest.raises(ValidationError) as v:
        TypeAdapter(int).validate_python(PII)
    assert _failure_code(v.value) == "validation_error"
    assert _failure_code(_json.JSONDecodeError("m", PII, 0)) == "validation_error"
