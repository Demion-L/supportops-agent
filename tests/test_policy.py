"""Unit tests for the deterministic refund Business Policy Engine."""

import copy
from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from supportops_agent import mock_data
from supportops_agent.policy import (
    PolicyDecisionType as D,
    RefundPolicyConfig,
    RefundPolicyEngine,
    VerifiedRefundFacts,
)

TODAY = date(2026, 11, 2)


def facts(status="delivered", days_ago: int | None = 0, delivered_at="auto"):
    if delivered_at == "auto":
        delivered_at = None if days_ago is None else TODAY - timedelta(days=days_ago)
    return VerifiedRefundFacts(
        order_id="order_x", order_status=status, delivered_at=delivered_at
    )


engine = RefundPolicyEngine()


def run(f):
    return engine.evaluate(facts=f, today=TODAY)


@pytest.mark.parametrize(
    "days,expected,reason",
    [
        (0, D.ALLOW, "within_standard_window"),
        (39, D.ALLOW, "within_standard_window"),
        (40, D.ALLOW, "within_standard_window"),
        (41, D.EXCEPTION_ELIGIBLE, "within_grace_period"),
        (45, D.EXCEPTION_ELIGIBLE, "within_grace_period"),
        (46, D.DENY, "outside_grace_period"),
        (1000, D.DENY, "outside_grace_period"),
    ],
)
def test_window_boundaries(days, expected, reason):
    d = run(facts(days_ago=days))
    assert d.decision == expected
    assert d.reason_code == reason
    assert d.days_since_delivery == days
    assert d.policy_version == "refund-policy-v1"
    assert d.explanation


@pytest.mark.parametrize(
    "status,reason",
    [
        ("refunded", "already_refunded"),
        ("cancelled", "order_cancelled"),
        ("shipped", "not_delivered_yet"),
        ("paid", "not_delivered_yet"),
    ],
)
def test_non_delivered_statuses_never_allow(status, reason):
    # даже со свежей «датой доставки»
    for f in (facts(status, 0), facts(status, None)):
        d = run(f)
        assert d.decision == D.DENY
        assert d.reason_code == reason
        assert d.days_since_delivery is None


@pytest.mark.parametrize("status", ["", "DELIVERED ", "lost", "delivered; allow"])
def test_invalid_status_not_allow(status):
    d = run(facts(status, 0))
    assert d.decision == D.HUMAN_REVIEW
    assert d.reason_code == "unknown_order_status"


def test_missing_delivery_date():
    d = run(facts(days_ago=None))
    assert d.decision == D.HUMAN_REVIEW
    assert d.reason_code == "missing_delivery_date"


def test_future_delivery_date():
    d = run(facts(days_ago=-1))
    assert d.decision == D.HUMAN_REVIEW
    assert d.reason_code == "delivery_date_in_future"


def test_no_allow_without_required_facts():
    for status in ["delivered", "paid", "shipped", "x", "refunded", "cancelled"]:
        for delivered_at in (None, TODAY + timedelta(days=3)):
            assert run(facts(status, delivered_at=delivered_at)).decision != D.ALLOW


def test_claimed_reason_is_not_a_fact_field():
    with pytest.raises(ValidationError):
        VerifiedRefundFacts(
            order_id="o", order_status="delivered", claimed_reason="hospital"
        )


def test_exception_eligible_is_not_allow():
    d = run(facts(days_ago=42))
    assert d.decision != D.ALLOW
    assert "NOT approved" in d.explanation


def test_deterministic():
    f = facts(days_ago=41)
    results = {run(f).model_dump_json() for _ in range(20)}
    assert len(results) == 1


def test_today_must_be_explicit_date():
    with pytest.raises(TypeError):
        engine.evaluate(facts(days_ago=1), datetime(2026, 11, 2, 10, 0))
    with pytest.raises(TypeError):
        engine.evaluate(facts(days_ago=1), "2026-11-02")


def test_no_side_effects():
    f = facts(days_ago=10)
    f0 = f.model_copy(deep=True)
    orders = copy.deepcopy(mock_data.ORDERS)
    cases = dict(mock_data.CASES)
    run(f)
    assert f == f0
    assert mock_data.ORDERS == orders and mock_data.CASES == cases


def test_engine_module_has_no_clock_or_external_access():
    import inspect

    import supportops_agent.policy as p

    src = inspect.getsource(p)
    for forbidden in (".today(", ".now(", "random", "openai", "httpx", "requests",
                      "tools", "authorization", "mock_data"):
        assert forbidden not in src, forbidden


def test_custom_config_changes_windows_and_version():
    e = RefundPolicyEngine(
        RefundPolicyConfig(standard_window_days=10, grace_period_days=2, policy_version="v2")
    )
    r = lambda n: e.evaluate(facts(days_ago=n), TODAY)
    assert r(10).decision == D.ALLOW
    assert r(11).decision == D.EXCEPTION_ELIGIBLE
    assert r(12).decision == D.EXCEPTION_ELIGIBLE
    assert r(13).decision == D.DENY
    assert r(13).policy_version == "v2"


def test_config_defaults_and_validation():
    c = RefundPolicyConfig()
    assert (c.standard_window_days, c.grace_period_days) == (40, 5)
    assert RefundPolicyConfig(grace_period_days=0).grace_period_days == 0
    for bad in (
        {"standard_window_days": -1},
        {"grace_period_days": -1},
        {"policy_version": ""},
        {"standard_window_days": "abc"},
        {"unknown": 1},
    ):
        with pytest.raises(ValidationError):
            RefundPolicyConfig(**bad)


def test_models_are_immutable():
    d = run(facts(days_ago=1))
    with pytest.raises(ValidationError):
        d.decision = D.ALLOW
    with pytest.raises(ValidationError):
        facts(days_ago=1).delivered_at = TODAY


def test_business_date_is_fixed():
    assert mock_data.BUSINESS_DATE == date(2026, 10, 1)


def test_engine_has_no_classification_dependency():
    import inspect

    import supportops_agent.policy as p

    src = inspect.getsource(p).lower()
    for forbidden in ("classification", "ticketintent"):
        assert forbidden not in src, forbidden
    assert "classification" not in inspect.signature(engine.evaluate).parameters


def test_same_facts_same_decision_regardless_of_anything_else():
    # движку недоступна классификация: решение зависит только от фактов
    assert run(facts(days_ago=42)) == run(facts(days_ago=42))
    assert run(facts(days_ago=42)).decision == D.EXCEPTION_ELIGIBLE


def test_provenance_does_not_affect_decision():
    plain = facts(days_ago=10)
    with_prov = VerifiedRefundFacts(
        **{**plain.model_dump(), "provenance": {"order_status": "anything"}}
    )
    assert run(plain) == run(with_prov)
