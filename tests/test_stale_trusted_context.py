"""SEC-003 / M04 / WP1 / SR-1: trusted context is valid only during an active run().

After run() ends (completed, failed or degraded) the agent keeps `state` for
diagnostics, but that state must NOT authorize business operations through the
public execute_function_call(s) methods.
"""

import json

import pytest

from supportops_agent.business import mock_data
from supportops_agent.runtime.agent import SupportAgent
from supportops_agent.tools.authorization import support_agent_context
from supportops_agent.classification import ClassificationError
from supportops_agent.models import AgentRunStatus, AgentStepType, SupportTicket

IDENTITY = {"outcome": "identity_verification_required"}


class _Call:
    type = "function_call"

    def __init__(self, reason="stale", call_id="call_1"):
        self.call_id, self.name = call_id, "create_support_case"
        self.arguments = json.dumps({"reason": reason, "priority": "high"})


class _Response:
    def __init__(self, rid, output=None, text=""):
        self.id, self.output, self.output_text, self.usage = rid, output or [], text, None


class _Responses:
    def __init__(self, first_call=None, fail_on=None):
        self.first_call, self.fail_on, self.n = first_call, fail_on, 0

    def create(self, **kwargs):
        self.n += 1
        if self.n == self.fail_on:
            raise RuntimeError("llm down")
        if self.n == 1 and self.first_call is not None:
            return _Response("r1", [self.first_call])
        return _Response(f"r{self.n}", text="done")


class _Client:
    def __init__(self, **kw):
        self.responses = _Responses(**kw)


class _FailingClassifier:
    def classify(self, ticket):
        raise ClassificationError("api_error", "boom")


@pytest.fixture(autouse=True)
def clean_cases(monkeypatch):
    monkeypatch.setattr(mock_data, "CASES", {})


def _ticket(tid, cust):
    return SupportTicket(ticket_id=tid, customer_id=cust, message="help")


def _agent(**kw):
    classifier = kw.pop("classifier", None)
    return SupportAgent(_Client(**kw), classifier, context=support_agent_context())


# SR1-T1
def test_sr1_t1_before_first_run_no_case_created():
    agent = _agent()
    result = agent.execute_function_call(_Call())
    assert result.output == IDENTITY
    assert mock_data.CASES == {}


# SR1-T2
def test_sr1_t2_after_completed_run_stale_context_cannot_create_case():
    agent = _agent()
    agent.run(_ticket("ticket_A", "customer_001"))
    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert mock_data.CASES == {}

    result = agent.execute_function_call(_Call())

    assert result.output == IDENTITY
    assert mock_data.CASES == {}


def test_sr1_t2_after_completed_run_existing_case_not_returned():
    # Legit run creates a case; a post-run call must not read it back either.
    agent = _agent(first_call=_Call("legit"))
    agent.run(_ticket("ticket_A", "customer_001"))
    assert len(mock_data.CASES) == 1
    before = {k: v.model_dump() for k, v in mock_data.CASES.items()}

    result = agent.execute_function_call(_Call("legit"))

    assert result.output == IDENTITY
    assert {k: v.model_dump() for k, v in mock_data.CASES.items()} == before


# SR1-T3
def test_sr1_t3_after_failed_run_context_not_reusable():
    agent = _agent(first_call=_Call("x"), fail_on=2)
    with pytest.raises(RuntimeError):
        agent.run(_ticket("ticket_A", "customer_001"))
    assert agent.last_run.status == AgentRunStatus.FAILED
    created = dict(mock_data.CASES)

    result = agent.execute_function_call(_Call("after-fail"))

    assert result.output == IDENTITY
    assert mock_data.CASES == created


# SR1-T4
def test_sr1_t4_after_degraded_run_context_not_reusable():
    agent = _agent(classifier=_FailingClassifier())
    agent.run(_ticket("ticket_A", "customer_001"))

    result = agent.execute_function_call(_Call())

    assert result.output == IDENTITY
    assert mock_data.CASES == {}


def test_sr1_t4_degraded_run_after_good_run_clears_previous_context():
    good = SupportAgent(_Client(), None, context=support_agent_context())
    good.run(_ticket("ticket_A", "customer_001"))
    good.classifier = _FailingClassifier()
    good.run(_ticket("ticket_B", "customer_002"))

    assert good.execute_function_call(_Call()).output == IDENTITY
    assert mock_data.CASES == {}


# SR1-T5
def test_sr1_t5_sequential_runs_no_context_authorizes_standalone_call():
    agent = _agent()
    agent.run(_ticket("ticket_A", "customer_001"))
    agent.run(_ticket("ticket_B", "customer_002"))

    result = agent.execute_function_call(_Call())

    assert result.output == IDENTITY
    assert mock_data.CASES == {}


def test_sr1_execute_function_calls_after_run_creates_no_case():
    agent = _agent()
    agent.run(_ticket("ticket_A", "customer_001"))

    outputs = agent.execute_function_calls([_Call()])

    assert outputs[0]["output"] == str(IDENTITY)
    assert mock_data.CASES == {}


# Positive control: inside a run the trusted context still works.
def test_sr1_active_run_still_binds_trusted_ticket():
    agent = _agent(first_call=_Call("legit"))
    agent.run(_ticket("ticket_A", "customer_001"))
    assert [c.ticket_id for c in mock_data.CASES.values()] == ["ticket_A"]
    step = next(s for s in agent.last_run.steps if s.step_type == AgentStepType.TOOL_EXECUTION)
    assert step.error is None and "outcome" not in step.output


def test_sr1_state_retained_for_diagnostics_after_run():
    agent = _agent()
    t = _ticket("ticket_A", "customer_001")
    agent.run(t)
    assert agent.state.ticket is t


# ---- SR-2: normalization consistency / collision invariants -----------------

from supportops_agent.business import support_tools  # noqa: E402
from supportops_agent.models import SupportCase  # noqa: E402


@pytest.mark.parametrize("variant", ["ticket_A", " ticket_A ", "ticket_A ", "\tticket_A\n"])
def test_sr2_equivalent_ticket_ids_share_one_normalized_case(variant):
    first = support_tools.create_support_case(
        "r", "high", trusted_ticket_id="ticket_A", trusted_customer_id="customer_001"
    )
    again = support_tools.create_support_case(
        "r", "low", trusted_ticket_id=variant, trusted_customer_id="customer_001"
    )
    assert first["outcome"] == "created"
    assert again["outcome"] == "already_exists"
    assert again["case"] == first["case"]  # priority not changed by retry
    assert len(mock_data.CASES) == 1
    assert {c.ticket_id for c in mock_data.CASES.values()} == {"ticket_A"}


def test_sr2_collision_with_foreign_ticket_leaks_nothing_and_overwrites_nothing():
    cid = support_tools._case_id("ticket_A", "r")
    foreign = SupportCase(case_id=cid, ticket_id="ticket_B", reason="SECRET", priority="low")
    mock_data.CASES[cid] = foreign
    before = foreign.model_dump()

    for variant in ("ticket_A", " ticket_A "):
        res = support_tools.create_support_case(
            "r", "high", trusted_ticket_id=variant, trusted_customer_id="customer_001"
        )
        assert res == {"outcome": "ticket_context_required"}
        assert "SECRET" not in json.dumps(res)

    assert mock_data.CASES[cid].model_dump() == before
    assert len(mock_data.CASES) == 1
