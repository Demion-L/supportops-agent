"""Verified Facts Provider: источники, provenance, read-only, отсутствие фактов."""

import copy
import inspect
from datetime import date

import pytest

from supportops_agent.business import mock_data
from supportops_agent.business import refund_facts
from supportops_agent.models import OrderRecord, OrderStatus
from supportops_agent.business.policy import VerifiedRefundFacts
from supportops_agent.business.refund_facts import OrderNotFoundError, VerifiedRefundFactsProvider

provider = VerifiedRefundFactsProvider()


def snapshot():
    return copy.deepcopy(
        (mock_data.ORDERS, mock_data.CUSTOMERS, mock_data.TRACKING, mock_data.CASES)
    )


def test_delivered_order_facts_and_provenance():
    f = provider.get_refund_facts("order_001")
    assert isinstance(f, VerifiedRefundFacts)
    assert (f.order_id, f.order_status) == ("order_001", "delivered")
    assert f.delivered_at == date(2026, 7, 24)
    assert f.provenance == {
        "order_status": "OrderRecord.status",
        "delivered_at": "OrderRecord.delivered_at",
    }


def test_delivery_date_is_not_tracking_last_update_or_created_at():
    for oid in ("order_001", "order_008"):
        f = provider.get_refund_facts(oid)
        assert f.delivered_at != date.fromisoformat(mock_data.TRACKING[oid]["last_update"])
        assert f.delivered_at != mock_data.ORDERS[oid].created_at


@pytest.mark.parametrize("oid", ["order_002", "order_003", "order_007"])
def test_undelivered_orders_have_no_delivery_date(oid):
    f = provider.get_refund_facts(oid)
    assert f.delivered_at is None
    assert f.order_status in ("paid", "shipped")


def test_delivered_without_date_is_reported_explicitly(monkeypatch):
    monkeypatch.setitem(
        mock_data.ORDERS,
        "order_nodate",
        OrderRecord(
            order_id="order_nodate", customer_id="customer_001",
            status=OrderStatus.DELIVERED, amount_cents=100, currency="EUR",
            created_at=date(2026, 9, 1),
        ),
    )
    f = provider.get_refund_facts("order_nodate")
    assert f.delivered_at is None
    assert f.provenance["delivered_at"] == "OrderRecord.delivered_at (absent)"


@pytest.mark.parametrize("oid", ["order_999", "", "   "])
def test_missing_order_raises_and_fabricates_nothing(oid):
    with pytest.raises(OrderNotFoundError) as e:
        provider.get_refund_facts(oid)
    assert e.value.code == "order_not_found"
    with pytest.raises(OrderNotFoundError):
        provider.get_order_customer_id(oid)


def test_owner_comes_from_order_record():
    assert provider.get_order_customer_id("order_001") == "customer_001"


def test_provider_is_read_only():
    before = snapshot()
    for oid in mock_data.ORDERS:
        provider.get_refund_facts(oid)
        provider.get_order_customer_id(oid)
    assert snapshot() == before


def test_provider_has_no_llm_or_customer_claim_access():
    import ast

    tree = ast.parse(inspect.getsource(refund_facts))
    for node in ast.walk(tree):  # docstring-и не считаем кодом
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    code = ast.dump(tree)
    for forbidden in ("openai", "claimed_reason", "exception_requested",
                      "TRACKING", "last_update", "created_at", "classification"):
        assert forbidden not in code, forbidden
    params = inspect.signature(provider.get_refund_facts).parameters
    assert list(params) == ["order_id"]


def test_delivered_at_preserved_in_domain_serialization():
    dumped = mock_data.ORDERS["order_001"].model_dump()
    assert dumped["delivered_at"] == date(2026, 7, 24)
