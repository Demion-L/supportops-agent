from agent_commons.dto import (AskRequest, FeedbackRequest, FeedbackStage, ValidateRequest, Verdict)
from agent_commons.vocab import ActionId
from conftest import Rig, delivery, make_rec

WRONG = make_rec("rec_wrong", action=ActionId.OFFER_GOODWILL_CREDIT, confidence=0.95)
RIGHT = make_rec("rec_right", action=ActionId.REQUEST_CARRIER_STATUS, applicability={"order_status": "shipped"}, confidence=0.6)


def ask(rig, task):
    return rig.svc.port().ask(AskRequest("agent_a", "delivery_delay", dict(task.context))).candidates


def test_negative_outcome_is_considered_for_similar_task():  # §12
    tasks = [delivery(i) for i in range(3)]
    rig = Rig(tasks, recs=[WRONG, RIGHT])
    before = ask(rig, tasks[0])
    assert before[0].recommendation.recommendation_id == "rec_wrong"  # confidence 0.95 побеждает вслепую
    tr0 = rig.run(tasks[0])
    assert tr0.source == "commons:rec_wrong" and not rig.world.oracle().case_state("case_0000").resolved
    after = ask(rig, tasks[1])
    assert after[0].recommendation.recommendation_id == "rec_right"
    assert next(c for c in after if c.recommendation.recommendation_id == "rec_wrong").score < 0.4
    tr1 = rig.run(tasks[1])
    assert tr1.source == "commons:rec_right" and rig.world.oracle().case_state("case_0001").resolved


def test_positive_outcome_raises_score_in_context():
    tasks = [delivery(i) for i in range(3)]
    rig = Rig(tasks, recs=[RIGHT])
    s0 = ask(rig, tasks[0])[0].score
    rig.run(tasks[0]); rig.run(tasks[1])
    s2 = ask(rig, tasks[2])[0]
    assert s0 == 0.5 and s2.score > 0.8 and s2.context_trials == 2


def test_reputation_is_contextual_not_global_truth():  # S9
    rec = make_rec("rec_ctx", action=ActionId.REQUEST_CARRIER_STATUS, confidence=0.9)  # верна для shipped, нет для processing
    tasks = [delivery(0, "shipped"), delivery(1, "shipped"), delivery(2, "processing"), delivery(3, "shipped")]
    rig = Rig(tasks, recs=[rec])
    for t in tasks[:3]:
        rig.run(t)
    c_ship = ask(rig, tasks[3])[0]
    proc_like = ask(rig, delivery(9, "processing"))[0]
    assert c_ship.score > 0.6 and proc_like.score < 0.4  # провал в processing не убил shipped
    assert rig.run(tasks[3]).source == "commons:rec_ctx"


def test_global_scoring_ablation_blurs_context():
    rec = make_rec("rec_ctx", action=ActionId.REQUEST_CARRIER_STATUS, confidence=0.9)
    tasks = [delivery(0, "shipped"), delivery(1, "shipped"), delivery(2, "processing")]
    g = Rig(tasks, recs=[rec], svc_kwargs={"scoring_mode": "global"})
    for t in tasks:
        g.run(t)
    # в global-режиме провал в processing не отличается по контексту: оценка у контекстов совпадает
    assert ask(g, delivery(8, "processing"))[0].score == ask(g, delivery(9, "shipped"))[0].score


def test_agent_feedback_cannot_change_score():  # S7
    t = delivery(1)
    rig = Rig([t], recs=[WRONG], evaluator=False)
    s0 = ask(rig, t)[0].score
    p = rig.svc.port()
    for i in range(50):  # каждый кейс — отдельное «исполнение»; без Evaluator оценка не движется
        app = f"app_agent_a_{i}"
        p.validate(ValidateRequest(app, "agent_a", "rec_wrong", 1, dict(t.context), f"case_{i:04d}"))
        p.feedback(FeedbackRequest(app, "agent_a", FeedbackStage.APPLIED, "success"))
    assert ask(rig, t)[0].score == s0  # 50 «успешных» самоотчётов без Evaluator ничего не значат


def test_confidence_is_not_evidence():
    t = delivery(1, "processing")
    rig = Rig([t], recs=[make_rec("rec_hi", action=ActionId.OFFER_GOODWILL_CREDIT, confidence=1.0),
                         make_rec("rec_lo", action=ActionId.REQUEST_ORDER_DETAILS, confidence=0.1)])
    rig.run(t)  # hi пробуется первой (ничья по оценке => confidence как tie-break), проваливается
    cands = ask(rig, delivery(2, "processing"))
    assert cands[0].recommendation.recommendation_id == "rec_lo"


def test_repeated_application_accumulates_independent_evidence():
    tasks = [delivery(i) for i in range(5)]
    rig = Rig(tasks, recs=[RIGHT])
    for t in tasks:
        rig.run(t)
    assert rig.svc.ledger.stats[("rec_right", 1)]["all"] == [5.0, 5.0]
    assert sum(1 for a in rig.svc.ledger.apps.values() if a.verdict is Verdict.SOLVED) == 5
