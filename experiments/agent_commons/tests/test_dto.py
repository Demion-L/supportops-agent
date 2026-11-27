import math
import pytest

from agent_commons.dto import AskRequest, FeedbackRequest, Recommendation, ValidateRequest
from agent_commons.errors import ValidationError
from conftest import make_rec

GOOD = {"recommendation_id": "rec_001", "version": 1, "problem_type": "delivery_delay",
        "proposed_action": "request_carrier_status", "applicability": {"order_status": "shipped"},
        "confidence": 0.7, "author_id": "agent_b"}


def test_valid_recommendation_roundtrip():
    r = Recommendation.from_dict(GOOD)
    assert r.to_dict() == GOOD


@pytest.mark.parametrize("patch", [
    {"extra": 1},                                   # неизвестное поле
    {"recommendation_id": "REC 1"},
    {"version": 0}, {"version": True}, {"version": "1"},
    {"confidence": 1.5}, {"confidence": -0.1}, {"confidence": float("nan")}, {"confidence": True}, {"confidence": "0.5"},
    {"problem_type": "weather"},
    {"proposed_action": "__import__('os').system('id')"},   # код вместо метки (S4)
    {"proposed_action": "rm -rf /"},
    {"applicability": {"order_status": "teleported"}},
    {"applicability": {"customer_email": "a@b.c"}},         # ПДн-ключ (S3)
    {"applicability": {"order_status": "user@example.com"}},
    {"author_id": "Bad Author!"},
])
def test_invalid_recommendation_rejected(patch):
    with pytest.raises(ValidationError):
        Recommendation.from_dict({**GOOD, **patch})


def test_missing_field_rejected():
    d = dict(GOOD); d.pop("confidence")
    with pytest.raises(ValidationError):
        Recommendation.from_dict(d)


def test_ask_rejects_internal_state_fields():
    base = {"requester_id": "agent_a", "problem_type": "delivery_delay", "context": {}, "limit": 3}
    AskRequest.from_dict(base)
    for leak in ({"customer_name": "X"}, {"internal_notes": "secret"}, {"api_key": "sk-123"}):
        with pytest.raises(ValidationError):
            AskRequest.from_dict({**base, **leak})
    with pytest.raises(ValidationError):
        AskRequest.from_dict({**base, "context": {"notes": "free text"}})


def test_other_dto_validation():
    with pytest.raises(ValidationError):
        FeedbackRequest("app_x_1", "agent_a", "applied", self_reported_outcome="great success!")
    with pytest.raises(ValidationError):
        FeedbackRequest("bad id", "agent_a", "applied")
    with pytest.raises(ValidationError):
        ValidateRequest("app_x_1", "agent_a", "rec_a", 1, {"order_status": "shipped"}, "Case With Spaces")
    assert math.isclose(make_rec(confidence=1).confidence, 1.0)
