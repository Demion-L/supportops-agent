"""Регрессии по независимому ревью (security-engineer)."""
import pytest

from agent_commons.agents import AgentConfig, SupportAgent
from agent_commons.dto import (AskRequest, FeedbackRequest, FeedbackStage, Recommendation,
                               ValidateRequest, Verdict)
from agent_commons.errors import CommonsError, DuplicateError, LogCorruptError
from agent_commons.eventlog import EventLog
from agent_commons.experiment import ARMS, run_arm
from agent_commons.policy import LocalPolicy
from agent_commons.service import CommonsService
from agent_commons.simulator import World
from agent_commons.tasks import Task
from agent_commons.vocab import ActionId, ProblemType
from conftest import Rig, delivery, make_rec


def test_one_world_execution_cannot_back_many_applications():  # B1/B2
    t = delivery(1, "processing")
    rig = Rig([t], recs=[make_rec("rec_a"), make_rec("rec_b")])
    rig.world.execute(t.task_id, ActionId.REQUEST_CARRIER_STATUS)
    p = rig.svc.port()
    p.validate(ValidateRequest("app_x_1", "agent_a", "rec_a", 1, dict(t.context), t.task_id))
    p.feedback(FeedbackRequest("app_x_1", "agent_a", FeedbackStage.APPLIED))
    p.validate(ValidateRequest("app_x_2", "agent_a", "rec_b", 1, dict(t.context), t.task_id))
    with pytest.raises(DuplicateError):  # то же действие в том же кейсе по другой рекомендации
        p.feedback(FeedbackRequest("app_x_2", "agent_a", FeedbackStage.APPLIED))
    rig.evaluator.process_pending()
    assert rig.svc.ledger.stats[("rec_a", 1)]["all"] == [0.0, 1.0] and ("rec_b", 1) not in rig.svc.ledger.stats


def test_returned_recommendation_cannot_mutate_ledger():  # B4
    t = delivery(1)
    rig = Rig([t], recs=[make_rec(applicability={"order_status": "shipped"})])
    cand = rig.svc.port().ask(AskRequest("agent_a", "delivery_delay", dict(t.context))).candidates[0]
    with pytest.raises(TypeError):
        cand.recommendation.applicability["order_status"] = "processing"
    assert rig.svc.ledger.recs[("rec_t1", 1)].rec.applicability == {"order_status": "shipped"}


def test_agent_survives_any_commons_error_and_checks_problem_type():  # C3, C4
    class Broken:
        def ask(self, r): raise DuplicateError("boom")
    t = delivery(1)
    w = World([t])
    tr = SupportAgent("agent_a", AgentConfig("t"), Broken(), LocalPolicy(), w.execute).solve(t)
    assert tr.source == "playbook" and w.oracle().case_state(t.task_id).resolved
    # сервер подсовывает рекомендацию другого типа проблемы
    billing = Task("case_0002", ProblemType.BILLING_ERROR, {"invoice_issue": "amount_mismatch"})
    wrong_type = make_rec("rec_wt", pt=ProblemType.DELIVERY_DELAY, action=ActionId.OFFER_GOODWILL_CREDIT, confidence=0.99)
    rig = Rig([billing], recs=[wrong_type])

    class Liar:
        def __init__(self, i): self.i = i
        def ask(self, r):
            from agent_commons.dto import AskResponse, Candidate
            return AskResponse((Candidate(wrong_type, 0.9, 0, 0),))
        def validate(self, r): return self.i.validate(r)
        def feedback(self, r): return self.i.feedback(r)
    rig.agent._port = Liar(rig.svc.port())
    tr = rig.agent.solve(billing)
    assert tr.considered[0]["decision"] == "not_applicable" and tr.source == "playbook"


def test_poison_event_is_not_persisted(tmp_path):  # D1
    p = tmp_path / "l.jsonl"
    rig = Rig([delivery(1)], log_path=p, recs=[make_rec(applicability={})])
    rig.svc.port().validate(ValidateRequest("app_x_1", "agent_a", "rec_t1", 1, {}, "case_0001"))
    rig.svc.port().feedback(FeedbackRequest("app_x_1", "agent_a", FeedbackStage.APPLIED))
    n = len(rig.svc.ledger.log.events)
    with pytest.raises(CommonsError):
        rig.svc.evaluation_sink().record_observation("app_x_1", "oops")  # не dict
    assert len(rig.svc.ledger.log.events) == n
    CommonsService(EventLog(p))  # лог по-прежнему открывается


def test_failed_write_is_rolled_back(tmp_path, monkeypatch):  # D4
    import os
    p = tmp_path / "l.jsonl"
    log = EventLog(p)
    log.append("x", {"a": 1})
    size = p.stat().st_size
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("ENOSPC")))
    with pytest.raises(OSError):
        log.append("x", {"a": 2})
    monkeypatch.undo()
    assert p.stat().st_size == size and len(log.events) == 1
    log.append("x", {"a": 3})
    assert len(EventLog(p).events) == 2


def test_small_streams_do_not_crash_metrics():  # A6
    for n in (1, 3):
        r = run_arm(ARMS["commons"], 0, n)
        assert len(r["success_by_quarter"]) == 4


def test_harmful_rejection_counts_only_considered_candidates():  # A4
    r = run_arm(ARMS["commons_policy_gap"], 0, 100)
    assert r["harmful_considered"] <= r["harmful_surfaced"]
    assert r["harmful_executed_rate"] > 0  # без политики вред возможен…
    assert r["harmful_executed_rate"] < run_arm(ARMS["naive_trust"], 0, 100)["harmful_executed_rate"]  # …но обучение его подавляет
