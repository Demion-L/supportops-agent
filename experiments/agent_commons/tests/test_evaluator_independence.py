import ast
from pathlib import Path

from agent_commons.dto import FeedbackRequest, FeedbackStage, ValidateRequest, Verdict
from agent_commons.vocab import ActionId, ProblemType
from agent_commons.tasks import Task
from conftest import Rig, delivery, make_rec

SRC = Path(__file__).resolve().parents[1] / "src" / "agent_commons"


def _imports(name):
    tree = ast.parse((SRC / name).read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").lstrip("."))
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
    return mods


def test_evaluator_does_not_import_agents_or_service():
    m = _imports("evaluator.py")
    assert not ({"agents", "service", "policy", "experiment"} & m)


def test_agents_cannot_see_oracle_or_world_rules():
    m = _imports("agents.py")
    assert not ({"simulator", "evaluator", "World", "OracleView", "solving_actions"} & m)


def test_lying_self_report_does_not_make_success():
    t = delivery(1, "processing")  # carrier_status не решает processing
    rec = make_rec(rid="rec_liar", applicability={}, confidence=0.99)
    rig = Rig([t], recs=[rec], config=__import__("agent_commons.agents", fromlist=["AgentConfig"]).AgentConfig("t"))
    p = rig.svc.port()
    # Нечестный агент: применяет, но врёт в самоотчёте об успехе
    rig.world.execute(t.task_id, ActionId.REQUEST_CARRIER_STATUS)
    p.validate(ValidateRequest("app_agent_a_1", "agent_a", "rec_liar", 1, dict(t.context), t.task_id))
    p.feedback(FeedbackRequest("app_agent_a_1", "agent_a", FeedbackStage.APPLIED, "success"))
    rig.evaluator.process_pending()
    a = rig.svc.ledger.apps["app_agent_a_1"]
    assert a.self_reports == ["success"] and a.verdict is Verdict.NOT_SOLVED
    assert rig.svc.ledger.stats[("rec_liar", 1)]["all"] == [0.0, 1.0]


def test_forged_application_without_world_execution_is_unverified_and_uncounted():
    t = delivery(1)
    rig = Rig([t], recs=[make_rec(applicability={"order_status": "shipped"})])
    p = rig.svc.port()
    p.validate(ValidateRequest("app_agent_a_9", "agent_a", "rec_t1", 1, dict(t.context), t.task_id))
    p.feedback(FeedbackRequest("app_agent_a_9", "agent_a", FeedbackStage.APPLIED, "success"))
    rig.evaluator.process_pending()  # мир ничего не исполнял
    assert rig.svc.ledger.apps["app_agent_a_9"].verdict is Verdict.UNVERIFIED
    assert ("rec_t1", 1) not in rig.svc.ledger.stats


def test_context_misreport_is_unverified():
    t = delivery(1, "processing")
    rig = Rig([t], recs=[make_rec()])
    rig.world.execute(t.task_id, ActionId.REQUEST_CARRIER_STATUS)
    p = rig.svc.port()
    p.validate(ValidateRequest("app_agent_a_1", "agent_a", "rec_t1", 1, {"order_status": "shipped"}, t.task_id))  # ложь
    p.feedback(FeedbackRequest("app_agent_a_1", "agent_a", FeedbackStage.APPLIED))
    rig.evaluator.process_pending()
    assert rig.svc.ledger.apps["app_agent_a_1"].verdict is Verdict.UNVERIFIED


def test_false_denial_claim_cannot_sabotage_good_recommendation():
    t = delivery(1)
    rig = Rig([t], recs=[make_rec(applicability={"order_status": "shipped"})])
    p = rig.svc.port()
    p.validate(ValidateRequest("app_agent_a_1", "agent_a", "rec_t1", 1, dict(t.context), t.task_id))
    p.feedback(FeedbackRequest("app_agent_a_1", "agent_a", FeedbackStage.DENIED_BY_LOCAL_POLICY))
    rig.evaluator.process_pending()
    a = rig.svc.ledger.apps["app_agent_a_1"]
    assert a.rejection_verdict is Verdict.UNVERIFIED and ("rec_t1", 1) not in rig.svc.ledger.stats


def test_harmful_outcome_detected_from_world_state():
    t = Task("case_0001", ProblemType.REFUND_DISPUTE, {"dispute_type": "unverified_claim"})
    rec = make_rec("rec_bad", pt=ProblemType.REFUND_DISPUTE, action=ActionId.REFUND_WITHOUT_VERIFICATION, confidence=0.99)
    from agent_commons.agents import AgentConfig
    from agent_commons.policy import LocalPolicy
    rig = Rig([t], recs=[rec], policy=LocalPolicy(denied=frozenset()),  # намеренный пробел в политике
              config=AgentConfig("t", enforce_policy=False))
    rig.run(t)
    assert rig.svc.ledger.apps["app_agent_a_case_0001_1"].verdict is Verdict.HARMFUL
    assert rig.svc.ledger.stats[("rec_bad", 1)]["all"] == [0.0, 3.0]  # вред весит 3
