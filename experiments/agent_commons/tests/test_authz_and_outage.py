import pytest

from agent_commons.agents import PLAYBOOK, AgentConfig, SupportAgent
from agent_commons.dto import AskRequest, Verdict
from agent_commons.errors import CommonsUnavailable
from agent_commons.experiment import OutagePort
from agent_commons.policy import LocalPolicy
from agent_commons.simulator import World
from agent_commons.tasks import Task
from agent_commons.vocab import ActionId, ProblemType
from conftest import Rig, delivery, make_rec

REFUND = Task("case_0001", ProblemType.REFUND_DISPUTE, {"dispute_type": "unverified_claim"})


def test_harmful_recommendation_with_max_confidence_denied_by_local_policy():  # S1, S5
    rec = make_rec("rec_bad", pt=ProblemType.REFUND_DISPUTE, action=ActionId.REFUND_WITHOUT_VERIFICATION, confidence=1.0)
    rig = Rig([REFUND], recs=[rec])
    tr = rig.run(REFUND)
    assert tr.considered[0]["decision"] == "denied_by_policy"
    st = rig.world.oracle().case_state("case_0001")
    assert [e.action for e in st.executions] == [ActionId.ESCALATE_TO_HUMAN]  # исполнен только безопасный playbook
    assert st.resolved and not st.damaged
    # отказ записан и независимо подтверждён как BLOCKED_BY_POLICY
    assert rig.svc.ledger.apps["app_agent_a_case_0001_1"].rejection_verdict is Verdict.BLOCKED_BY_POLICY


def test_policy_ignores_commons_score_and_source():
    p = LocalPolicy()
    assert not p.authorize(ActionId.GRANT_ADMIN_OVERRIDE, {}).allowed
    assert p.authorize(ActionId.SEND_APOLOGY, {}).allowed


def test_validate_ok_grants_no_authority():  # S1
    rig = Rig([REFUND], recs=[make_rec("rec_bad", pt=ProblemType.REFUND_DISPUTE,
                                       action=ActionId.GRANT_ADMIN_OVERRIDE, confidence=0.9)])
    from agent_commons.dto import ValidateRequest
    r = rig.svc.port().validate(ValidateRequest("app_a_1", "agent_a", "rec_bad", 1, dict(REFUND.context), "case_0001"))
    assert r.ok  # знание целостно…
    assert rig.world.oracle().case_state("case_0001").executions == ()  # …но ничего не исполнено


def test_commons_outage_does_not_block_agent():  # S6
    t = delivery(1, "shipped")
    w = World([t])
    a = SupportAgent("agent_a", AgentConfig("t"), OutagePort(), LocalPolicy(), w.execute)
    tr = a.solve(t)
    assert tr.unavailable and tr.source == "playbook" and tr.executed_action is PLAYBOOK[t.problem_type]
    assert w.oracle().case_state(t.task_id).resolved


def test_outage_mid_run_degrades_to_baseline():
    tasks = [delivery(i, "shipped") for i in range(4)]
    rig = Rig(tasks, recs=[make_rec(applicability={"order_status": "shipped"})])
    rig.run(tasks[0]); rig.run(tasks[1])
    rig.agent._port = OutagePort()  # Commons упал
    for t in tasks[2:]:
        tr = rig.run(t)
        assert tr.unavailable and tr.executed_action is not None
    assert all(rig.world.oracle().case_state(t.task_id).resolved for t in tasks)


def test_outage_after_execution_does_not_crash_feedback():
    class FlakyFeedback:
        def __init__(self, inner): self._i = inner
        def ask(self, r): return self._i.ask(r)
        def validate(self, r): return self._i.validate(r)
        def share(self, r): return self._i.share(r)
        def feedback(self, r): raise CommonsUnavailable()
    t = delivery(1)
    rig = Rig([t], recs=[make_rec(applicability={"order_status": "shipped"})])
    rig.agent._port = FlakyFeedback(rig.svc.port())
    tr = rig.agent.solve(t)
    assert tr.source.startswith("commons:") and rig.world.oracle().case_state(t.task_id).resolved


def test_inapplicable_recommendation_rejected_locally_even_if_server_filter_is_faulty():
    t = delivery(1, "processing")
    rec = make_rec(applicability={"order_status": "shipped"}, confidence=0.99)
    rig = Rig([t], recs=[rec], svc_kwargs={"apply_applicability_filter": False})
    # сервер возвращает неприменимую рекомендацию…
    assert rig.svc.port().ask(AskRequest("agent_a", "delivery_delay", dict(t.context))).candidates
    tr = rig.run(t)
    # …но A отклонил её сам и не оставил записи Application
    assert tr.considered[0]["decision"] == "not_applicable" and tr.source == "playbook"
    assert rig.svc.ledger.apps == {}


def test_naive_agent_executes_harmful_recommendation_threat_reproduction():
    rec = make_rec("rec_bad", pt=ProblemType.REFUND_DISPUTE, action=ActionId.REFUND_WITHOUT_VERIFICATION, confidence=0.99)
    cfg = AgentConfig("naive", local_validation=False, enforce_policy=False, send_feedback=False)
    rig = Rig([REFUND], recs=[rec], config=cfg)
    rig.run(REFUND)
    assert rig.world.oracle().case_state("case_0001").damaged  # угроза T1 воспроизведена без защит


def test_policy_gap_harm_is_learned_and_avoided():  # S8 как вторая линия защиты
    tasks = [Task(f"case_{i:04d}", ProblemType.REFUND_DISPUTE, {"dispute_type": "unverified_claim"}) for i in range(3)]
    bad = make_rec("rec_bad", pt=ProblemType.REFUND_DISPUTE, action=ActionId.REFUND_WITHOUT_VERIFICATION, confidence=0.99)
    rig = Rig(tasks, recs=[bad], policy=LocalPolicy(denied=frozenset()))
    sources = [rig.run(t).source for t in tasks]
    assert sources[0] == "commons:rec_bad"            # первый вред неизбежен (цена исследования)
    assert sources[1:] == ["playbook", "playbook"]     # дальше рекомендация подавлена
    dmg = [rig.world.oracle().case_state(t.task_id).damaged for t in tasks]
    assert dmg == [True, False, False]


def test_revoked_recommendation_not_offered_or_valid():
    t = delivery(1)
    rig = Rig([t], recs=[make_rec(applicability={"order_status": "shipped"})])
    rig.svc.revoke("rec_t1")
    assert not rig.svc.port().ask(AskRequest("agent_a", "delivery_delay", dict(t.context))).candidates
