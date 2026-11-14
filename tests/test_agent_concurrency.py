"""SEC-003 / M04 / WP3: one active run() per SupportAgent instance, trusted
context and trace bound to the owning run/thread (R2-I1 ... R2-I12).

Real threads are used; synchronisation is by threading.Event with timeouts only
(no sleep, no network, no real LLM).
"""

import json
import threading

import pytest

from supportops_agent import mock_data
from supportops_agent.agent import ConcurrentRunError, SupportAgent
from supportops_agent.authorization import support_agent_context
from supportops_agent.classification import ClassificationError
from supportops_agent.models import AgentRunStatus, AgentStepType, SupportTicket

TIMEOUT = 5
IDENTITY = {"outcome": "identity_verification_required"}


class _Call:
    type = "function_call"

    def __init__(self, reason="r", call_id="call_1"):
        self.call_id, self.name = call_id, "create_support_case"
        self.arguments = json.dumps({"reason": reason, "priority": "high"})


class _Response:
    def __init__(self, rid, output=None):
        self.id, self.output, self.output_text, self.usage = (
            rid, output or [], "Safe answer.", None
        )


class _Responses:
    """First create() of the *first run* can block / call a hook / raise."""

    def __init__(self, first_call=None, block=False, hook=None, raise_exc=None):
        self.first_call, self.block, self.hook, self.raise_exc = (
            first_call, block, hook, raise_exc
        )
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls = 0
        self._lock = threading.Lock()

    def create(self, **kwargs):
        with self._lock:
            self.calls += 1
            n = self.calls
        if n == 1:
            self.entered.set()
            if self.block:
                assert self.release.wait(TIMEOUT), "test deadlock guard"
            if self.hook:
                self.hook()
            if self.raise_exc is not None:
                raise self.raise_exc
            if self.first_call is not None:
                return _Response("r1", [self.first_call])
        return _Response(f"r{n}")


class _Client:
    def __init__(self, **kw):
        self.responses = _Responses(**kw)


class _FailingClassifier:
    def classify(self, ticket):
        raise ClassificationError("api_error", "customer_002 secret")


def _ticket(tid="ticket_A", cust="customer_001"):
    return SupportTicket(ticket_id=tid, customer_id=cust, message="help")


def _agent(client, classifier=None):
    return SupportAgent(client, classifier, context=support_agent_context())


def _start_blocked_run(agent, client, ticket=None):
    """Run A in a thread, parked inside the LLM call. Returns (thread, result)."""

    result = {}

    def target():
        try:
            result["answer"] = agent.run(ticket or _ticket())
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            result["exc"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert client.responses.entered.wait(TIMEOUT), "run A did not start"
    return thread, result


def _finish(thread, client):
    client.responses.release.set()
    thread.join(TIMEOUT)
    assert not thread.is_alive()


def _is_idle(agent):
    return (not agent._run_lock.locked()) and agent._run_active is False


# R2-T1
def test_t1_concurrent_run_is_rejected_immediately():
    client = _Client(block=True)
    agent = _agent(client)
    thread, result = _start_blocked_run(agent, client)
    try:
        with pytest.raises(ConcurrentRunError) as info:
            agent.run(_ticket("ticket_B", "customer_002"))
        assert isinstance(info.value, RuntimeError)
        assert str(info.value) == "Agent run already in progress"
    finally:
        _finish(thread, client)
    assert result["answer"] == "Safe answer."


# R2-T2 + R2-T9
def test_t2_first_run_integrity_after_rejection():
    client = _Client(block=True)
    agent = _agent(client)
    thread, result = _start_blocked_run(agent, client)
    manager_before = agent.run_manager
    state_before = agent.state
    steps_before = len(agent.last_run.steps)

    with pytest.raises(ConcurrentRunError) as info:
        agent.run(_ticket("ticket_B", "customer_002"))

    assert agent.run_manager is manager_before
    assert agent.state is state_before
    assert agent.state.ticket.customer_id == "customer_001"
    assert agent.state.ticket.ticket_id == "ticket_A"
    assert agent.last_run.ticket_id == "ticket_A"
    assert agent.last_run.status == AgentRunStatus.RUNNING
    assert len(agent.last_run.steps) == steps_before
    assert agent._run_active is True and agent._run_lock.locked()
    for text in ("ticket_B", "customer_002"):
        assert text not in str(info.value)

    _finish(thread, client)

    assert result["answer"] == "Safe answer."
    assert agent.last_run.status == AgentRunStatus.COMPLETED  # not FAILED by B
    dump = agent.last_run.model_dump_json()
    assert "ticket_B" not in dump and "customer_002" not in dump
    assert _is_idle(agent)


# R2-T3
def test_t3_lock_released_after_success_and_sequential_runs_work():
    client = _Client()
    agent = _agent(client)
    assert _is_idle(agent)

    assert agent.run(_ticket("ticket_A", "customer_001")) == "Safe answer."
    assert _is_idle(agent)
    assert agent.last_run.ticket_id == "ticket_A"

    assert agent.run(_ticket("ticket_B", "customer_002")) == "Safe answer."
    assert _is_idle(agent)
    assert agent.last_run.ticket_id == "ticket_B"
    assert "ticket_A" not in agent.last_run.model_dump_json()  # R2-T10


# R2-T4
def test_t4_lock_released_after_failure():
    boom = RuntimeError("llm down")
    agent = _agent(_Client(raise_exc=boom))
    with pytest.raises(RuntimeError) as info:
        agent.run(_ticket())
    assert info.value is boom
    assert agent.last_run.status == AgentRunStatus.FAILED
    assert _is_idle(agent)

    agent.client = _Client()  # next run succeeds on the same instance
    assert agent.run(_ticket("ticket_B", "customer_002")) == "Safe answer."
    assert agent.last_run.status == AgentRunStatus.COMPLETED


def test_t4_lock_released_after_base_exception():
    agent = _agent(_Client(raise_exc=KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        agent.run(_ticket())
    assert _is_idle(agent)


# R2-T5
def test_t5_reentrant_run_is_rejected_and_does_not_release_lock():
    seen = {}
    holder = {}

    def hook():
        agent = holder["agent"]
        try:
            agent.run(_ticket("ticket_B", "customer_002"))
        except ConcurrentRunError as exc:
            seen["exc"] = exc
        seen["locked_after_reject"] = agent._run_lock.locked()
        seen["active_after_reject"] = agent._run_active
        seen["customer"] = agent.state.ticket.customer_id
        seen["ticket"] = agent.state.ticket.ticket_id

    client = _Client(hook=hook)
    agent = holder["agent"] = _agent(client)

    box = {}
    worker = threading.Thread(
        target=lambda: box.setdefault("answer", agent.run(_ticket("ticket_A", "customer_001"))),
        daemon=True,
    )
    worker.start()
    worker.join(TIMEOUT)
    assert not worker.is_alive(), "reentrant run() deadlocked"
    answer = box["answer"]

    assert isinstance(seen["exc"], ConcurrentRunError)
    assert seen["locked_after_reject"] is True  # rejected run did not release
    assert seen["active_after_reject"] is True
    assert (seen["customer"], seen["ticket"]) == ("customer_001", "ticket_A")
    assert answer == "Safe answer."
    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert "ticket_B" not in agent.last_run.model_dump_json()
    assert _is_idle(agent)


# R2-T6
def test_t6_stale_trusted_context_after_run_is_fail_closed():
    agent = _agent(_Client(first_call=_Call("legit")))
    agent.run(_ticket())
    before = {k: v.model_dump() for k, v in mock_data.CASES.items()}
    assert len(before) == 1

    result = agent.execute_function_call(_Call("after-run"))

    assert result.output == IDENTITY
    assert {k: v.model_dump() for k, v in mock_data.CASES.items()} == before


# R2-T7
@pytest.mark.parametrize("failed", [False, True])
def test_t7_finished_trace_is_immutable(failed):
    client = _Client(first_call=_Call("legit"), raise_exc=None)
    if failed:
        client = _Client(raise_exc=RuntimeError("x"))
    agent = _agent(client)
    try:
        agent.run(_ticket())
    except RuntimeError:
        assert failed
    run = agent.last_run
    assert run.status in (AgentRunStatus.COMPLETED, AgentRunStatus.FAILED)
    snapshot = run.model_dump_json()

    outputs = agent.execute_function_calls([_Call("late", "call_late")])

    assert len(outputs) == 1  # public method still answers, fail-closed
    assert agent.last_run.model_dump_json() == snapshot
    assert agent.execute_function_call(_Call("late")).output == IDENTITY
    assert agent.last_run.model_dump_json() == snapshot


def test_t7_manual_run_manager_contract_is_preserved():
    from supportops_agent.run_manager import AgentRunManager

    agent = _agent(_Client())
    agent.run_manager = AgentRunManager("t1")  # existing, documented-by-tests usage
    agent.execute_function_calls([_Call()])
    assert [s.step_type for s in agent.run_manager.run.steps] == [
        AgentStepType.TOOL_EXECUTION
    ]


# R2-T8
def test_t8_degraded_classification_releases_lock_and_context():
    agent = _agent(_Client(first_call=_Call()), _FailingClassifier())

    answer = agent.run(_ticket())

    assert "unable to process" in answer
    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert _is_idle(agent)
    assert agent.execute_function_call(_Call()).output == IDENTITY
    assert mock_data.CASES == {}
    assert "customer_002" not in agent.last_run.model_dump_json()
    agent.classifier = None
    agent.client = _Client()
    assert agent.run(_ticket("ticket_B", "customer_002")) == "Safe answer."


# Cross-thread: trusted context and trace belong to the owning thread only.
def test_cross_thread_execution_cannot_use_active_run_context_or_trace():
    client = _Client(block=True, first_call=_Call("owner-call"))
    agent = _agent(client)
    thread, result = _start_blocked_run(agent, client)
    steps_before = len(agent.last_run.steps)
    assert agent._run_active is True  # the old flag alone proves nothing

    outcome = agent.execute_function_call(_Call("foreign"))  # another thread
    outputs = agent.execute_function_calls([_Call("foreign2", "call_f2")])

    assert outcome.output == IDENTITY
    assert mock_data.CASES == {}  # nothing created under run A's identity
    assert len(outputs) == 1
    assert len(agent.last_run.steps) == steps_before  # A's trace untouched
    assert agent.run_manager.run.status == AgentRunStatus.RUNNING

    _finish(thread, client)

    assert result["answer"] == "Safe answer."
    # the owner's own tool call still ran with its own trusted context
    cases = list(mock_data.CASES.values())
    assert [(c.ticket_id, c.reason) for c in cases] == [("ticket_A", "owner-call")]
    assert agent.last_run.status == AgentRunStatus.COMPLETED


def test_different_instances_run_independently():
    client_a = _Client(block=True)
    agent_a, agent_b = _agent(client_a), _agent(_Client())
    thread, result = _start_blocked_run(agent_a, client_a)
    try:
        assert agent_b.run(_ticket("ticket_B", "customer_002")) == "Safe answer."
        assert agent_b.last_run.ticket_id == "ticket_B"
        assert agent_a.last_run.ticket_id == "ticket_A"
    finally:
        _finish(thread, client_a)
    assert result["answer"] == "Safe answer."


def test_lifecycle_states_idle_running_completed_failed():
    client = _Client(block=True)
    agent = _agent(client)
    assert _is_idle(agent)  # IDLE

    thread, _ = _start_blocked_run(agent, client)
    assert agent._run_lock.locked() and agent._run_active is True  # RUNNING
    _finish(thread, client)
    assert _is_idle(agent)  # COMPLETED

    failing = _agent(_Client(raise_exc=RuntimeError("x")))
    with pytest.raises(RuntimeError):
        failing.run(_ticket())
    assert _is_idle(failing)  # FAILED


# R2-I2 / R2-I3: acquisition is non-blocking (deterministic, no timing).
def test_acquire_is_non_blocking():
    class SpyLock:
        def __init__(self):
            self._lock, self.acquires = threading.Lock(), []

        def acquire(self, blocking=True, timeout=-1):
            self.acquires.append((blocking, timeout))
            return self._lock.acquire(blocking, timeout)

        def release(self):
            self._lock.release()

        def locked(self):
            return self._lock.locked()

    client = _Client(block=True)
    agent = _agent(client)
    agent._run_lock = spy = SpyLock()
    thread, _ = _start_blocked_run(agent, client)
    try:
        with pytest.raises(ConcurrentRunError):
            agent.run(_ticket("ticket_B", "customer_002"))
    finally:
        _finish(thread, client)

    assert spy.acquires == [(False, -1), (False, -1)]  # run A and rejected run B


# ---------------------------------------------------------------------------
# WP3 remediation (review findings B-1 / B-2 / B-3). Deterministic: spy
# properties, Event with timeouts, no sleep, no scheduler dependence.
# ---------------------------------------------------------------------------


class _ActiveSpyAgent(SupportAgent):
    """`_run_active` reads follow a script (then fall back to the real value).

    Reproduces the exact B-1 interleaving: first read True, second read False.
    """

    _script: list | None = None

    @property
    def _run_active(self):  # type: ignore[override]
        script = type(self)._script
        if script:
            return script.pop(0)
        return self.__dict__.get("_real_run_active", False)

    @_run_active.setter
    def _run_active(self, value):
        self.__dict__["_real_run_active"] = value


class _CountingAgent(SupportAgent):
    """Records reads of `state`, `run_manager`, `_run_owner` per thread.

    `on_read[name]` is a one-shot hook that fires only for reads made by the
    thread registered in `hook_thread` (so the owning run is never disturbed).
    """

    def __init__(self, *a, **kw):
        self.reads = []  # (attribute, thread ident)
        self.on_read = {}
        self.hook_thread = None
        super().__init__(*a, **kw)

    def count(self, name, tid=None):
        tid = threading.get_ident() if tid is None else tid
        return sum(1 for n, t in self.reads if n == name and t == tid)

    def _spy(name):  # noqa: N805 - property factory
        def getter(self):
            tid = threading.get_ident()
            self.reads.append((name, tid))
            hook = self.on_read.get(name)
            if hook and tid == self.hook_thread:
                self.on_read.pop(name)
                hook()
            return self.__dict__.get("_v_" + name)

        def setter(self, value):
            self.__dict__["_v_" + name] = value

        return property(getter, setter)

    state = _spy("state")
    run_manager = _spy("run_manager")
    _run_owner = _spy("_run_owner")
    del _spy


# B1-T1: first read of _run_active -> True, second -> False (stale context).
def test_b1_t1_double_read_of_run_active_cannot_revive_stale_context():
    _ActiveSpyAgent._script = None
    agent = _ActiveSpyAgent(
        _Client(first_call=_Call("legit")), None, context=support_agent_context()
    )
    agent.run(_ticket("ticket_A", "customer_001"))
    assert agent.state.ticket.ticket_id == "ticket_A"  # stale state is still there
    before = {k: v.model_dump() for k, v in mock_data.CASES.items()}
    steps_before = agent.last_run.model_dump_json()

    _ActiveSpyAgent._script = [True, False, False, False]
    try:
        result = agent.execute_function_call(_Call("stale"))
        outputs = agent.execute_function_calls([_Call("stale2", "call_s2")])
    finally:
        _ActiveSpyAgent._script = None

    assert result.output == IDENTITY
    assert len(outputs) == 1
    assert {k: v.model_dump() for k, v in mock_data.CASES.items()} == before
    assert all(c.reason != "stale" for c in mock_data.CASES.values())
    assert agent.last_run.model_dump_json() == steps_before  # trace not polluted


# B1-T2: the run finishes after the ownership decision, before state is used.
def test_b1_t2_run_finishing_during_ownership_check_cannot_leak_state():
    client = _Client(block=True, first_call=_Call("owner"))
    agent = _CountingAgent(client, None, context=support_agent_context())
    thread, result = _start_blocked_run(agent, client)
    agent.hook_thread = threading.get_ident()  # the foreign (test) thread
    agent.on_read["state"] = lambda: _finish(thread, client)  # run ends here

    outcome = agent.execute_function_call(_Call("foreign"))  # foreign thread

    assert outcome.output == IDENTITY
    # decision is taken from ONE owner snapshot, before trusted state is touched
    assert agent.count("state") == 0
    assert agent.count("_run_owner") == 1
    _finish(thread, client)
    assert [c.reason for c in mock_data.CASES.values()] == ["owner"]
    assert result["answer"] == "Safe answer."


def test_b1_owner_is_read_once_per_decision():
    agent = _CountingAgent(_Client(), None, context=support_agent_context())
    agent.run(_ticket())
    agent.reads.clear()
    agent.execute_function_call(_Call("x"))
    assert agent.count("_run_owner") == 1


# B1-T3: sequential runs on one thread never see each other's identity.
def test_b1_t3_sequential_runs_same_thread_do_not_share_context():
    seen = {}

    class Probe:
        def classify(self, ticket):
            seen.setdefault("pre_state", []).append(
                (agent.state, agent.execute_function_call(_Call("probe")).output)
            )
            raise ClassificationError("api_error", "x")

    agent = _agent(_Client(first_call=_Call("run-A")), Probe())
    agent.classifier = None
    agent.run(_ticket("ticket_A", "customer_001"))
    agent.classifier = Probe()
    agent.client = _Client(first_call=_Call("run-B"))
    agent.run(_ticket("ticket_B", "customer_002"))

    # second run, before its own trusted state exists: no run-A identity leaks
    assert seen["pre_state"] == [(None, IDENTITY)]
    assert [c.reason for c in mock_data.CASES.values()] == ["run-A"]
    assert agent.last_run.ticket_id == "ticket_B"


def test_b1_t3b_same_thread_second_run_uses_only_its_own_ticket():
    agent = _agent(_Client(first_call=_Call("run-A")))
    agent.run(_ticket("ticket_A", "customer_001"))
    agent.client = _Client(first_call=_Call("run-B", "call_b"))
    agent.run(_ticket("ticket_B", "customer_002"))
    assert sorted((c.ticket_id, c.reason) for c in mock_data.CASES.values()) == [
        ("ticket_A", "run-A"),
        ("ticket_B", "run-B"),
    ]


# B1-T4: foreign thread during an active run (explicit real thread).
def test_b1_t4_foreign_thread_during_active_run_is_denied():
    client = _Client(block=True, first_call=_Call("owner"))
    agent = _agent(client)
    thread, result = _start_blocked_run(agent, client)
    box = {}
    steps_before = len(agent.last_run.steps)

    def foreign():
        box["one"] = agent.execute_function_call(_Call("foreign")).output
        box["many"] = agent.execute_function_calls([_Call("f2", "call_f2")])

    other = threading.Thread(target=foreign, daemon=True)
    other.start()
    other.join(TIMEOUT)
    assert not other.is_alive()
    steps_after = len(agent.last_run.steps)
    _finish(thread, client)

    assert box["one"] == IDENTITY and len(box["many"]) == 1
    assert [c.reason for c in mock_data.CASES.values()] == ["owner"]
    assert steps_after == steps_before  # the foreign calls added nothing
    assert result["answer"] == "Safe answer."


# B1-T5: foreign thread immediately after the run finished.
def test_b1_t5_foreign_thread_right_after_run_is_denied():
    agent = _agent(_Client(first_call=_Call("owner")))
    worker = threading.Thread(target=lambda: agent.run(_ticket()), daemon=True)
    worker.start()
    worker.join(TIMEOUT)
    assert not worker.is_alive() and _is_idle(agent)
    snapshot = agent.last_run.model_dump_json()
    box = {}

    def foreign():
        box["out"] = agent.execute_function_call(_Call("late")).output
        box["many"] = agent.execute_function_calls([_Call("late2", "call_l2")])

    other = threading.Thread(target=foreign, daemon=True)
    other.start()
    other.join(TIMEOUT)

    assert box["out"] == IDENTITY and len(box["many"]) == 1
    assert [c.reason for c in mock_data.CASES.values()] == ["owner"]
    assert agent.last_run.model_dump_json() == snapshot


# B1-T6: reentrant public call from the owning thread is part of that run.
def test_b1_t6_reentrant_execution_uses_only_the_current_run_identity():
    holder, seen = {}, {}

    def hook():
        agent = holder["agent"]
        # arguments carry no identity; trusted ticket A is applied by the agent
        seen["res"] = agent.execute_function_call(_Call("reentrant")).output
        seen["nested"] = None
        try:
            agent.run(_ticket("ticket_B", "customer_002"))
        except ConcurrentRunError as exc:
            seen["nested"] = exc

    agent = holder["agent"] = _agent(_Client(hook=hook))
    agent.run(_ticket("ticket_A", "customer_001"))

    assert seen["res"] != IDENTITY
    assert isinstance(seen["nested"], ConcurrentRunError)
    cases = list(mock_data.CASES.values())
    assert [(c.ticket_id, c.reason) for c in cases] == [("ticket_A", "reentrant")]


# B2-T1: run_manager swapped between the writability check and the write.
def test_b2_t1_manager_swap_between_check_and_record_is_harmless():
    from supportops_agent.run_manager import AgentRunManager

    checked = AgentRunManager("t_checked")  # RUNNING, manual
    other = AgentRunManager("t_other")
    other.complete()  # a finished trace that must never be written
    other_snapshot = other.run.model_dump_json()

    class SwappingAgent(SupportAgent):
        # Reads 1-2 (the old "is not None" / ".run.status" checks) see
        # `checked`; every later read sees `other`. A re-reading implementation
        # checks `checked` but records into `other`.
        reads = 0

        @property
        def run_manager(self):
            type(self).reads += 1
            return checked if type(self).reads <= 2 else other

        @run_manager.setter
        def run_manager(self, value):
            pass

    agent = SwappingAgent(_Client(), None, context=support_agent_context())

    agent.execute_function_calls([_Call("x")])

    assert other.run.model_dump_json() == other_snapshot  # never polluted
    assert [s.step_type for s in checked.run.steps] == [AgentStepType.TOOL_EXECUTION]
    assert SwappingAgent.reads == 1  # one snapshot, no re-read


# B2-T2: the trace becomes finished while the tool executes -> no write.
def test_b2_t2_trace_finished_during_tool_execution_gets_no_event():
    from supportops_agent.run_manager import AgentRunManager

    agent = _agent(_Client())
    manager = agent.run_manager = AgentRunManager("t")
    original = agent.execute_function_call

    def finishing(item):
        out = original(item)
        manager.complete()  # run ends right after the tool ran
        return out

    agent.execute_function_call = finishing
    outputs = agent.execute_function_calls([_Call("x")])

    assert len(outputs) == 1
    assert manager.run.steps == []


# B3-T1/T2: BaseException leaves a sealed trace, propagates unchanged.
@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)])
def test_b3_t1_t2_base_exception_seals_trace_and_propagates(exc):
    agent = _agent(_Client(first_call=_Call("never"), raise_exc=exc))

    with pytest.raises(type(exc)) as info:
        agent.run(_ticket())

    assert info.value is exc  # same object, not swallowed or replaced
    run = agent.last_run
    assert run.status == AgentRunStatus.FAILED  # not RUNNING (B3-I1)
    assert run.finished_at is not None
    assert run.steps[-1].step_type == AgentStepType.FAILURE
    assert run.steps[-1].error == "unknown_error"  # closed code, no text
    assert agent._run_active is False  # B3-I4
    assert agent._run_owner is None  # B3-I5
    assert not agent._run_lock.locked()  # B3-I3


# B3-T3: a new run works after an aborted one and does not inherit anything.
@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(1)])
def test_b3_t3_new_run_after_aborted_run(exc):
    agent = _agent(_Client(raise_exc=exc))
    with pytest.raises(type(exc)):
        agent.run(_ticket("ticket_A", "customer_001"))
    aborted = agent.last_run

    agent.client = _Client(first_call=_Call("second"))
    assert agent.run(_ticket("ticket_B", "customer_002")) == "Safe answer."

    assert agent.last_run is not aborted
    assert agent.last_run.status == AgentRunStatus.COMPLETED
    assert aborted.status == AgentRunStatus.FAILED
    assert [(c.ticket_id, c.reason) for c in mock_data.CASES.values()] == [
        ("ticket_B", "second")
    ]


# B3-T4: nothing can be appended to an aborted trace through public methods.
@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(1)])
def test_b3_t4_aborted_trace_rejects_late_events(exc):
    agent = _agent(_Client(raise_exc=exc))
    with pytest.raises(type(exc)):
        agent.run(_ticket())
    snapshot = agent.last_run.model_dump_json()

    outputs = agent.execute_function_calls([_Call("late", "call_late")])

    assert len(outputs) == 1
    assert agent.execute_function_call(_Call("late")).output == IDENTITY
    assert agent.last_run.model_dump_json() == snapshot
    assert mock_data.CASES == {}


# B3: even if recording the failure is itself interrupted, the trace is sealed
# and the lock is released; the interrupt still propagates.
def test_b3_fail_itself_interrupted_still_seals_and_releases(monkeypatch):
    from supportops_agent.run_manager import AgentRunManager

    def broken_fail(self, error=None):
        raise KeyboardInterrupt("second interrupt")

    monkeypatch.setattr(AgentRunManager, "fail", broken_fail)
    agent = _agent(_Client(raise_exc=RuntimeError("x")))

    with pytest.raises(KeyboardInterrupt):
        agent.run(_ticket())

    assert agent.last_run.status == AgentRunStatus.FAILED
    assert _is_idle(agent) and agent._run_owner is None


# B3-I7 / I8: ordinary Exception keeps FAILED semantics; degraded stays COMPLETED.
def test_b3_i7_i8_exception_and_degraded_semantics_unchanged():
    agent = _agent(_Client(raise_exc=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        agent.run(_ticket())
    assert agent.last_run.status == AgentRunStatus.FAILED
    assert agent.last_run.steps[-1].error == "agent_execution_error"

    degraded = _agent(_Client(), _FailingClassifier())
    degraded.run(_ticket())
    assert degraded.last_run.status == AgentRunStatus.COMPLETED


# B2-order: the owner is set before the new manager becomes visible, so a
# foreign reader can never obtain the active run's manager together with
# owner=None (that would make the live trace writable from outside).
def test_b2_owner_is_published_before_run_manager():
    published = []

    class OrderAgent(SupportAgent):
        @property
        def run_manager(self):
            return self.__dict__.get("_rm")

        @run_manager.setter
        def run_manager(self, value):
            if value is not None:
                published.append(self._run_owner)
            self.__dict__["_rm"] = value

    agent = OrderAgent(_Client(), None, context=support_agent_context())
    agent.run(_ticket())

    assert published == [threading.get_ident()]
