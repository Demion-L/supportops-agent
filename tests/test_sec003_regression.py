"""SEC-003 / M04 / WP4: final security regression (tests only, no production changes).

End-to-end scenarios over SupportAgent.run with fake LLM/classifier. Markers are
synthetic. Real threads, synchronised only with Events + timeouts.
"""

import json
import threading

import pytest

from supportops_agent import mock_data
from supportops_agent import agent as agent_module
from supportops_agent.agent import (
    CLASSIFICATION_FAILURE_RESPONSE,
    ConcurrentRunError,
    SupportAgent,
)
from supportops_agent.authorization import support_agent_context
from supportops_agent.classification import (
    ClassificationContext,
    ClassificationEntities,
    ClassificationError,
    ClassificationResult,
    TicketClassification,
    TicketIntent,
)
from supportops_agent.models import AgentRunStatus, AgentStepType, SupportTicket
from supportops_agent.policy import RefundPolicyEngine
from supportops_agent.refund_evaluation import RefundEvaluationOrchestrator
from supportops_agent.refund_facts import VerifiedRefundFactsProvider
from supportops_agent.run_manager import AgentRunManager

TIMEOUT = 5
SECRET = "SYNTH_SECRET_ABC123"
EMAIL = "SYNTH_EMAIL@example.invalid"
CARD = "SYNTH_CARD_4111111111111111"
MARKERS = (SECRET, EMAIL, CARD)
FOREIGN_MARKERS = ("customer_002", "Alice", "alice@example.com", "UPS-0002", "order_002")
IDENTITY = {"outcome": "identity_verification_required"}


class Call:
    type = "function_call"

    def __init__(self, name, arguments, call_id="call_1"):
        self.call_id, self.name = call_id, name
        self.arguments = arguments if isinstance(arguments, str) else json.dumps(arguments)


class Response:
    def __init__(self, rid, output=None):
        self.id, self.output, self.output_text, self.usage = rid, output or [], "Safe answer.", None


class Client:
    """First create() returns the given tool calls; may block/raise on first call."""

    def __init__(self, *calls, raise_exc=None, block=False):
        self.calls_to_make, self.raise_exc, self.block = list(calls), raise_exc, block
        self.requests, self.n = [], 0
        self.entered, self.release = threading.Event(), threading.Event()
        self._lock = threading.Lock()
        self.responses = self

    def create(self, **kw):
        with self._lock:
            self.requests.append(kw)
            self.n += 1
            n = self.n
        if n == 1:
            self.entered.set()
            if self.block:
                assert self.release.wait(TIMEOUT), "deadlock guard"
            if self.raise_exc is not None:
                raise self.raise_exc
            if self.calls_to_make:
                return Response("r1", self.calls_to_make)
        return Response(f"r{n}")


class Cls:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc
        self.calls = 0

    def classify(self, ticket):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


def ticket(tid="ticket_A", cust="customer_001", message="please help"):
    return SupportTicket(ticket_id=tid, customer_id=cust, message=message)


def make_agent(client, classifier=None, **kw):
    return SupportAgent(client, classifier, context=support_agent_context(), **kw)


def blob(agent):
    return agent.last_run.model_dump_json() + json.dumps(agent.last_run.summary(), default=str)


def tool_steps(agent):
    return [s for s in agent.last_run.steps if s.step_type == AgentStepType.TOOL_EXECUTION]


def cases():
    return {k: v.model_dump(mode="json") for k, v in mock_data.CASES.items()}


@pytest.fixture(autouse=True)
def clean_cases():
    mock_data.CASES.clear()
    yield
    mock_data.CASES.clear()


def refund_classification(order_id="order_001"):
    return ClassificationResult(
        TicketClassification(
            intents=[TicketIntent.REFUND_REQUEST],
            primary_intent=TicketIntent.REFUND_REQUEST,
            entities=ClassificationEntities(order_id=order_id),
            context=ClassificationContext(exception_requested=False, claimed_reason=None),
            requires_clarification=False,
        ),
        model="m",
    )


# ---------------- WP4-T1: cross-customer authorization ----------------
@pytest.mark.parametrize("name,args", [
    ("lookup_order", {"order_id": "order_002"}),
    ("lookup_customer", {"customer_id": "customer_002"}),
    ("get_order_tracking", {"order_id": "order_002"}),
])
def test_t1_cross_customer_access_denied_without_leak(name, args):
    client = Client(Call(name, args))
    agent = make_agent(client)
    agent.run(ticket(cust="customer_001"))

    out = client.requests[1]["input"][0]["output"]  # what the LLM received
    own_input = json.dumps(args)  # the denial may echo the caller's own argument
    for m in FOREIGN_MARKERS:
        if m not in own_input:
            assert m not in out
        assert m not in blob(agent)
    assert tool_steps(agent)[0].output is None or all(
        m not in json.dumps(tool_steps(agent)[0].output) for m in FOREIGN_MARKERS
    )
    assert mock_data.CASES == {}
    assert agent.last_run.status == AgentRunStatus.COMPLETED


def test_t1_foreign_and_missing_objects_indistinguishable():
    outs = []
    for oid in ("order_002", "order_999"):
        client = Client(Call("lookup_order", {"order_id": oid}))
        make_agent(client).run(ticket())
        outs.append(client.requests[1]["input"][0]["output"].replace(oid, "X"))
    assert outs[0] == outs[1]


# ---------------- WP4-T2: classification failure ----------------
@pytest.mark.parametrize("kind", ["api_error", "invalid_output"])
def test_t2_classification_error_fail_closed(kind, monkeypatch):
    executed = []
    monkeypatch.setattr(
        agent_module, "execute_tool_call",
        lambda *a, **k: executed.append(1) or (_ for _ in ()).throw(AssertionError("tool ran")),
    )
    client = Client(Call("create_support_case", {"reason": "r", "priority": "high"}))
    agent = make_agent(client, Cls(exc=ClassificationError(kind, f"{SECRET} {EMAIL}")))

    answer = agent.run(ticket(message=f"my card {CARD}"))

    assert answer == CLASSIFICATION_FAILURE_RESPONSE
    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert agent.last_run.summary()["classification_failed"] is True
    assert client.requests == [] and executed == [] and mock_data.CASES == {}
    assert [m for m in MARKERS if m in blob(agent)] == []


def test_t2_unexpected_classifier_error_is_failed_and_clean():
    agent = make_agent(Client(), Cls(exc=RuntimeError(f"{SECRET} {EMAIL}")))
    with pytest.raises(RuntimeError):
        agent.run(ticket())
    assert agent.last_run.status == AgentRunStatus.FAILED
    assert [m for m in MARKERS if m in blob(agent)] == []


# ---------------- WP4-T3: trusted ticket binding ----------------
def test_t3_llm_cannot_substitute_ticket_id():
    client = Client(Call("create_support_case", {
        "ticket_id": "ticket_victim", "reason": "payment_problem", "priority": "high"}))
    agent = make_agent(client)
    agent.run(ticket("ticket_mine"))

    assert mock_data.CASES == {}
    assert agent.state.ticket.ticket_id == "ticket_mine"
    assert agent.state.ticket.customer_id == "customer_001"
    assert "ticket_victim" not in json.dumps(agent.last_run.model_dump(mode="json")["steps"][-1:])
    assert "Invalid arguments" in (tool_steps(agent)[0].error or "")


def test_t3_valid_case_is_bound_to_trusted_ticket_only():
    client = Client(Call("create_support_case", {"reason": "payment_problem", "priority": "high"}))
    make_agent(client).run(ticket("ticket_mine"))
    created = list(mock_data.CASES.values())
    assert len(created) == 1
    assert created[0].ticket_id == "ticket_mine" and created[0].reason == "payment_problem"


# ---------------- WP4-T4: exception sanitization ----------------
class _Policy:
    def __init__(self, exc):
        self.exc = exc

    def authorize(self, tool, context):
        raise self.exc


@pytest.mark.parametrize("exc_cls_name", ["RuntimeError", SECRET])
def test_t4_policy_exception_sanitized_fail_closed(exc_cls_name, caplog):
    exc_cls = RuntimeError if exc_cls_name == "RuntimeError" else type(SECRET, (Exception,), {})
    client = Client(Call("lookup_order", {"order_id": "order_001"}))
    agent = make_agent(client, policy=_Policy(exc_cls(f"{SECRET} {EMAIL} {CARD}")))
    with caplog.at_level("DEBUG"):
        agent.run(ticket())

    llm_ctx = json.dumps(client.requests[1]["input"])
    assert [m for m in MARKERS if m in blob(agent) + llm_ctx + caplog.text] == []
    assert "john@example.com" not in llm_ctx  # fail-closed: no data disclosed
    step = tool_steps(agent)[0]
    assert step.input["reason_code"] == "POLICY_ERROR"
    assert step.input["diagnostic_type"] == ("RuntimeError" if exc_cls is RuntimeError else "PolicyError")


def test_t4_run_exception_semantics_preserved_and_sanitized():
    boom = RuntimeError(f"{SECRET} {EMAIL}")
    agent = make_agent(Client(raise_exc=boom))
    with pytest.raises(RuntimeError) as info:
        agent.run(ticket())
    assert info.value is boom  # original object propagates unchanged
    assert agent.last_run.status == AgentRunStatus.FAILED
    assert [m for m in MARKERS if m in blob(agent)] == []


# ---------------- WP4-T5: concurrent run isolation ----------------
def test_t5_concurrent_run_rejected_contexts_and_traces_isolated():
    client = Client(Call("create_support_case", {"reason": "r", "priority": "high"}), block=True)
    agent = make_agent(client)
    result = {}

    def target():
        try:
            result["answer"] = agent.run(ticket("ticket_A", "customer_001"))
        except BaseException as exc:  # noqa: BLE001
            result["exc"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    assert client.entered.wait(TIMEOUT)
    first_trace, first_state = agent.last_run, agent.state

    with pytest.raises(ConcurrentRunError) as info:
        agent.run(ticket("ticket_B", "customer_002"))
    assert "ticket_B" not in str(info.value) and "customer_002" not in str(info.value)
    assert agent.last_run is first_trace and agent.state is first_state
    assert agent.state.ticket.customer_id == "customer_001"
    assert agent.last_run.status == AgentRunStatus.RUNNING

    client.release.set()
    t.join(TIMEOUT)
    assert not t.is_alive() and result["answer"] == "Safe answer."
    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert "ticket_B" not in blob(agent) and "customer_002" not in blob(agent)
    assert [c.ticket_id for c in mock_data.CASES.values()] == ["ticket_A"]
    assert not agent._run_lock.locked() and agent._run_active is False


# ---------------- WP4-T6: trace lifecycle ----------------
def _idle(agent):
    return (not agent._run_lock.locked()) and agent._run_active is False


def test_t6_completed_and_no_write_after_finish():
    agent = make_agent(Client(Call("create_support_case", {"reason": "r", "priority": "high"})))
    agent.run(ticket())
    assert agent.last_run.status == AgentRunStatus.COMPLETED and _idle(agent)
    snap = agent.last_run.model_dump_json()
    late = agent.execute_function_call(Call("create_support_case", {"reason": "late", "priority": "high"}))
    assert late.output == IDENTITY
    assert agent.last_run.model_dump_json() == snap
    assert len(mock_data.CASES) == 1


def test_t6_failed_lifecycle():
    agent = make_agent(Client(raise_exc=RuntimeError("x")))
    with pytest.raises(RuntimeError):
        agent.run(ticket())
    assert agent.last_run.status == AgentRunStatus.FAILED and _idle(agent)
    assert agent.last_run.finished_at is not None


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)])
def test_t6_base_exception_seals_and_propagates(exc):
    agent = make_agent(Client(raise_exc=exc))
    with pytest.raises(type(exc)) as info:
        agent.run(ticket())
    assert info.value is exc
    assert agent.last_run.status == AgentRunStatus.FAILED
    assert agent.last_run.steps[-1].step_type == AgentStepType.FAILURE
    assert _idle(agent) and agent._run_owner is None


@pytest.mark.parametrize("original", [RuntimeError("o"), KeyboardInterrupt("o")])
def test_t6_double_failure_preserves_original_and_closes_trace(monkeypatch, original):
    def broken(self, error=None):
        raise ValueError(SECRET)

    monkeypatch.setattr(AgentRunManager, "fail", broken)
    agent = make_agent(Client(raise_exc=original))
    with pytest.raises(type(original)) as info:
        agent.run(ticket())
    assert info.value is original
    assert agent.last_run.status == AgentRunStatus.FAILED and _idle(agent)
    snap = agent.last_run.model_dump_json()
    agent.execute_function_call(Call("create_support_case", {"reason": "late", "priority": "high"}))
    assert agent.last_run.model_dump_json() == snap and mock_data.CASES == {}
    assert SECRET not in snap
    monkeypatch.undo()
    agent.client = Client()
    assert agent.run(ticket("ticket_B", "customer_002")) == "Safe answer."  # next run works


# ---------------- WP4-T7: happy path ----------------
def test_t7_happy_path_full_flow():
    client = Client(
        Call("lookup_order", {"order_id": "order_001"}, "c1"),
        Call("create_support_case", {"reason": "payment_problem", "priority": "high"}, "c2"),
    )
    cls = Cls(refund_classification("order_001"))
    evaluator = RefundEvaluationOrchestrator(
        VerifiedRefundFactsProvider(), RefundPolicyEngine(), mock_data.BUSINESS_DATE)
    agent = make_agent(client, cls, refund_evaluator=evaluator)
    answer = agent.run(ticket("ticket_A", "customer_001", "Refund for order_001 please"))

    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert cls.calls == 1 and answer
    # business tools worked, bound to trusted identity
    created = list(mock_data.CASES.values())
    assert len(created) == 1 and created[0].ticket_id == "ticket_A"
    # LLM context keeps the real data (sanitization is trace-only)
    outputs = {o["call_id"]: o["output"] for o in client.requests[1]["input"]
               if o.get("type") == "function_call_output"}
    assert "order_001" in outputs["c1"] and "12999" in outputs["c1"]
    assert "customer_001" in outputs["c1"]
    # deterministic policy decision recorded, metadata-only trace
    policy = [s for s in agent.last_run.steps if s.step_type == AgentStepType.POLICY_DECISION]
    assert len(policy) == 1 and policy[0].output["decision"] in ("allow", "deny", "needs_review")
    dump = blob(agent)
    assert "12999" not in dump and "john@example.com" not in dump
    assert len(tool_steps(agent)) == 2


# ---------------- WP4-T8: final metadata invariant ----------------
def test_t8_final_metadata_invariant_with_synthetic_markers():
    cls = TicketClassification(
        intents=[TicketIntent.REFUND_REQUEST], primary_intent=TicketIntent.REFUND_REQUEST,
        entities=ClassificationEntities(order_id=SECRET),
        context=ClassificationContext(exception_requested=True, claimed_reason=f"{EMAIL} {CARD}"),
        requires_clarification=False,
    )
    client = Client(
        Call("lookup_order", {"order_id": SECRET}, f"id_{SECRET}"),
        Call("create_support_case", {"reason": f"{EMAIL} {CARD}", "priority": "high",
                                     "ticket_id": SECRET}, "c3"),
        Call("lookup_customer", f"{{not json {SECRET}", "c4"),
    )
    evaluator = RefundEvaluationOrchestrator(
        VerifiedRefundFactsProvider(), RefundPolicyEngine(), mock_data.BUSINESS_DATE)
    agent = make_agent(client, Cls(ClassificationResult(cls, model="m", response_id=SECRET)),
                       refund_evaluator=evaluator)
    agent.run(ticket(message=f"{SECRET} {EMAIL} {CARD}"))
    assert [m for m in MARKERS if m in blob(agent)] == []
    # marker survives only in the LLM context (not sanitized there)
    assert SECRET in json.dumps(client.requests[0]["input"])


def test_t8_unknown_tool_name_run_fails_with_clean_trace():
    """Pre-existing semantics: unknown tool -> run FAILED, exception propagates."""
    agent = make_agent(Client(Call(f"tool_{SECRET}", {"x": EMAIL})))
    with pytest.raises(Exception):
        agent.run(ticket())
    assert agent.last_run.status == AgentRunStatus.FAILED
    assert [m for m in MARKERS if m in blob(agent)] == []


def test_t5_foreign_thread_cannot_use_active_run_context():
    client = Client(block=True)
    agent = make_agent(client)
    t = threading.Thread(target=lambda: agent.run(ticket()), daemon=True)
    t.start()
    assert client.entered.wait(TIMEOUT)
    seen = {}
    f = threading.Thread(target=lambda: seen.update(
        r=agent.execute_function_call(
            Call("create_support_case", {"reason": "x", "priority": "high"}))), daemon=True)
    f.start()
    f.join(TIMEOUT)
    snap_steps = len(agent.last_run.steps)
    client.release.set()
    t.join(TIMEOUT)
    assert seen["r"].output == IDENTITY
    assert mock_data.CASES == {}
    assert snap_steps == 0 or agent.last_run.status == AgentRunStatus.COMPLETED
