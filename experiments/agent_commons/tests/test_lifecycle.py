import pytest

from agent_commons.dto import (FeedbackRequest, FeedbackStage, ShareRequest, ValidateRequest, Verdict)
from agent_commons.errors import (ConflictError, DuplicateError, ForbiddenError, TransitionError,
                                  UnknownEntityError)
from agent_commons.lifecycle import APP_TRANSITIONS, REC_TRANSITIONS, AppState, RecState
from agent_commons.service import CommonsService
from conftest import make_rec

CTX = {"order_status": "shipped"}


def vreq(app, rid="rec_t1", ver=1, agent="agent_a", case="case_0001"):
    return ValidateRequest(app, agent, rid, ver, CTX, case)


def test_recommendation_lifecycle():
    svc = CommonsService()
    assert svc.port().share(ShareRequest(make_rec())).status == "VALIDATED"
    svc.revoke("rec_t1")
    assert svc.ledger.recs[("rec_t1", 1)].state is RecState.REVOKED
    with pytest.raises(UnknownEntityError):
        svc.revoke("rec_t1")  # уже отозвана
    assert REC_TRANSITIONS[RecState.REVOKED] == frozenset()


def test_version_rules():
    svc = CommonsService(); p = svc.port()
    p.share(ShareRequest(make_rec()))
    with pytest.raises(DuplicateError):
        p.share(ShareRequest(make_rec()))
    with pytest.raises(ConflictError):
        p.share(ShareRequest(make_rec(version=3)))
    p.share(ShareRequest(make_rec(version=2, confidence=0.9)))
    cand = p.ask(__import__("agent_commons.dto", fromlist=["AskRequest"]).AskRequest(
        "agent_a", "delivery_delay", CTX)).candidates
    assert [c.recommendation.version for c in cand] == [2]  # старая версия не рекомендуется


def test_application_full_lifecycle_and_many_applications_per_recommendation():
    svc = CommonsService(); p = svc.port(); sink = svc.evaluation_sink()
    p.share(ShareRequest(make_rec()))
    for i in (1, 2, 3):
        app = f"app_a_{i}"
        assert p.validate(vreq(app, case=f"case_{i:04d}")).ok
        p.feedback(FeedbackRequest(app, "agent_a", FeedbackStage.APPLIED))
        sink.record_observation(app, {"x": 1})
        sink.record_evaluation(app, Verdict.SOLVED, 1, "evaluator_c")
        assert svc.ledger.apps[app].history == ["PROPOSED", "VALIDATED", "APPLIED", "OBSERVED", "EVALUATED"]
    assert len(svc.ledger.apps) == 3 and svc.ledger.stats[("rec_t1", 1)]["all"] == [3.0, 3.0]


def test_illegal_application_transitions():
    svc = CommonsService(); p = svc.port(); sink = svc.evaluation_sink()
    p.share(ShareRequest(make_rec()))
    p.validate(vreq("app_a_1"))
    with pytest.raises(TransitionError):
        sink.record_evaluation("app_a_1", Verdict.SOLVED, 1, "evaluator_c")  # VALIDATED -> EVALUATED
    with pytest.raises(TransitionError):
        sink.record_observation("app_a_1", {})                                # VALIDATED -> OBSERVED
    p.feedback(FeedbackRequest("app_a_1", "agent_a", FeedbackStage.APPLIED))
    with pytest.raises(TransitionError):
        p.feedback(FeedbackRequest("app_a_1", "agent_a", FeedbackStage.APPLIED))  # повторное применение
    assert APP_TRANSITIONS[AppState.EVALUATED] == frozenset() == APP_TRANSITIONS[AppState.REJECTED]


def test_duplicate_and_spoofed_feedback():
    svc = CommonsService(); p = svc.port()
    p.share(ShareRequest(make_rec()))
    p.validate(vreq("app_a_1"))
    with pytest.raises(DuplicateError):
        p.validate(vreq("app_a_1"))                       # replay application_id
    with pytest.raises(ForbiddenError):
        p.feedback(FeedbackRequest("app_a_1", "agent_evil", FeedbackStage.APPLIED))
    with pytest.raises(UnknownEntityError):
        p.feedback(FeedbackRequest("app_zzz", "agent_a", FeedbackStage.APPLIED))


def test_inapplicable_and_stale_validation_rejects_application():
    svc = CommonsService(); p = svc.port()
    p.share(ShareRequest(make_rec(applicability={"order_status": "processing"})))
    r = p.validate(vreq("app_a_1"))
    assert not r.ok and not r.applicable and svc.ledger.apps["app_a_1"].state is AppState.REJECTED
    p.share(ShareRequest(make_rec(rid="rec_t2")))
    p.share(ShareRequest(make_rec(rid="rec_t2", version=2)))
    r2 = p.validate(vreq("app_a_2", rid="rec_t2", ver=1))
    assert not r2.ok and "stale_version" in r2.reasons
