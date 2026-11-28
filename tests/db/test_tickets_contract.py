"""One behavioural contract, run against BOTH the in-memory fake and PostgreSQL.

T1 atomicity, T2 replay, T3 conflict, T4 concurrent create, T5 cross-tenant,
T6 foreign ticket, T7 unknown ticket, T8 rollback, T9 unavailable, T10 malformed input.
"""

from __future__ import annotations

import inspect
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from hermes_service.db.errors import (
    IdempotencyConflict,
    InvalidRepositoryInput,
    StorageFailure,
    StorageUnavailable,
)
from hermes_service.repositories.fakes import InMemoryTicketRepository
from hermes_service.repositories.tickets import PgTicketRepository
from ticket_harness import FakeHarness, PgHarness

A, B = "tenant-a", "tenant-b"
KEY = "key-0000001"


@pytest.fixture(params=["fake", pytest.param("pg", marks=pytest.mark.postgres)])
def h(request):
    if request.param == "fake":
        return FakeHarness()
    return PgHarness(request.getfixturevalue("migrated_pool"))


def create(repo, tenant=A, key=KEY, customer="cust-1", message="hello", order="ord-1"):
    return repo.create_ticket(
        tenant_id=tenant, idempotency_key=key, customer_id=customer, message=message, order_id=order
    )


# ---- create / get / replay / conflict -------------------------------------------------
def test_create_then_get(h):
    r = create(h.repo)
    assert (r.status, r.replayed) == ("accepted", False)
    assert r.ticket_id.startswith("tkt_") and r.run_id.startswith("run_")
    view = h.repo.get_ticket(tenant_id=A, ticket_id=r.ticket_id)
    assert view is not None
    assert (view.ticket_id, view.run_id, view.status) == (r.ticket_id, r.run_id, "accepted")
    assert view.answer is None and view.failure_code is None
    assert h.counts() == (1, 1)


def test_ticket_view_exposes_no_request_data(h):
    r = create(h.repo, message="secret-marker-text")
    view = h.repo.get_ticket(tenant_id=A, ticket_id=r.ticket_id)
    assert "secret-marker-text" not in repr(view)
    assert not {"message", "customer_id", "order_id"} & set(vars(view))


def test_replay_returns_same_ids_and_writes_nothing(h):  # T2
    first = create(h.repo)
    again = create(h.repo)
    assert again.replayed is True
    assert (again.ticket_id, again.run_id, again.created_at) == (first.ticket_id, first.run_id, first.created_at)
    assert h.counts() == (1, 1)


def test_replay_reports_current_run_status(h):
    first = create(h.repo)
    h.force_run_failed(A, first.ticket_id, "worker_lost")
    again = create(h.repo)
    assert again.replayed and again.status == "failed"
    assert again.run_id == first.run_id
    assert h.counts() == (1, 1)


@pytest.mark.parametrize(
    "variant",
    [{"message": "other"}, {"customer": "cust-2"}, {"order": "ord-2"}, {"order": None}],
)
def test_same_key_other_payload_conflicts(h, variant):  # T3
    first = create(h.repo)
    with pytest.raises(IdempotencyConflict) as exc:
        create(h.repo, **variant)
    assert first.ticket_id not in str(exc.value)
    assert h.counts() == (1, 1)


def test_null_order_replays(h):
    a = create(h.repo, order=None)
    b = create(h.repo, order=None)
    assert b.replayed and b.ticket_id == a.ticket_id


# ---- tenant isolation -----------------------------------------------------------------
def test_same_key_in_two_tenants_is_independent(h):  # T5
    a = create(h.repo, tenant=A)
    b = create(h.repo, tenant=B, message="different body")  # would conflict if keys leaked across tenants
    assert not a.replayed and not b.replayed
    assert a.ticket_id != b.ticket_id and a.run_id != b.run_id
    assert h.counts() == (2, 2)


def test_foreign_unknown_malformed_ticket_are_indistinguishable(h):  # T6, T7
    mine = create(h.repo, tenant=A)
    foreign = h.repo.get_ticket(tenant_id=B, ticket_id=mine.ticket_id)
    unknown = h.repo.get_ticket(tenant_id=B, ticket_id="tkt_" + "0" * 32)
    malformed = [
        h.repo.get_ticket(tenant_id=B, ticket_id=bad)
        for bad in ("", "x", "tkt_", "tkt_" + "G" * 32, mine.ticket_id + "\n", mine.ticket_id.upper(), "' OR 1=1 --")
    ]
    assert foreign is None and unknown is None and all(m is None for m in malformed)
    assert h.repo.get_ticket(tenant_id=A, ticket_id=mine.ticket_id) is not None


def test_replay_in_other_tenant_cannot_reach_foreign_ticket(h):
    a = create(h.repo, tenant=A)
    b = create(h.repo, tenant=B)
    assert b.ticket_id != a.ticket_id
    assert h.repo.get_ticket(tenant_id=B, ticket_id=a.ticket_id) is None


def test_tenant_id_is_required_keyword_only(h):
    for name in ("create_ticket", "get_ticket"):
        p = inspect.signature(getattr(h.repo, name)).parameters["tenant_id"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        h.repo.get_ticket(ticket_id="tkt_" + "0" * 32)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        h.repo.create_ticket(  # type: ignore[call-arg]
            idempotency_key=KEY, customer_id="c", message="m", order_id=None
        )
    with pytest.raises(TypeError):
        h.repo.get_ticket(A, "tkt_" + "0" * 32)  # type: ignore[misc]


# ---- malformed input (T10) -----------------------------------------------------------
@pytest.mark.parametrize(
    "tenant",
    ["", "Tenant", "-a", "a_b", "a" * 64, "a'; DROP TABLE tickets;--", "tenant-a\n", "ténant", None, 5],
)
def test_invalid_tenant_rejected_without_leak(h, tenant):
    with pytest.raises(InvalidRepositoryInput) as exc:
        create(h.repo, tenant=tenant)
    assert str(tenant) not in str(exc.value) or str(tenant) in ("", "None", "5")
    with pytest.raises(InvalidRepositoryInput):
        h.repo.get_ticket(tenant_id=tenant, ticket_id="tkt_" + "0" * 32)
    assert h.counts() == (0, 0)


@pytest.mark.parametrize(
    "key", ["", "short", "a" * 101, "has space 123", "key;DROP TABLE x", "key-0000001\n", "ключ-0000001", None]
)
def test_invalid_idempotency_key_rejected(h, key):
    with pytest.raises(InvalidRepositoryInput):
        create(h.repo, key=key)
    assert h.counts() == (0, 0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("customer", ""), ("customer", "c" * 101), ("customer", "a\x00b"), ("customer", 7),
        ("message", ""), ("message", "m" * 4001), ("message", "a\x00b"), ("message", "bad\ud800surrogate"),
        ("order", ""), ("order", "o" * 101), ("order", "a\x00b"),
    ],
)
def test_invalid_text_fields_rejected(h, field, value):
    with pytest.raises(InvalidRepositoryInput) as exc:
        create(h.repo, **{field: value})
    assert "Invalid" in str(exc.value) and "\x00" not in str(exc.value)
    assert h.counts() == (0, 0)


def test_boundary_lengths_and_unicode_are_accepted(h):
    r = create(h.repo, customer="c" * 100, message="я" * 4000, order="o" * 100)
    assert create(h.repo, customer="c" * 100, message="я" * 4000, order="o" * 100).ticket_id == r.ticket_id


def test_injection_payload_is_stored_as_data(h):
    payload = "'; DROP TABLE tickets; --"
    r = create(h.repo, message=payload, customer=payload, order=payload)
    assert h.repo.get_ticket(tenant_id=A, ticket_id=r.ticket_id) is not None
    assert h.counts() == (1, 1)  # table still exists and holds the row


# ---- atomicity / rollback (T1, T8) ----------------------------------------------------
def test_failure_between_inserts_leaves_nothing(h):
    with h.fail_between_inserts():
        with pytest.raises(StorageFailure) as exc:
            create(h.repo)
    assert "injected" not in str(exc.value)
    assert h.counts() == (0, 0)
    # the key was not consumed: a retry creates a fresh, complete ticket
    r = create(h.repo)
    assert not r.replayed and h.counts() == (1, 1)


def test_failure_does_not_disturb_existing_data(h):
    keep = create(h.repo, key="key-keep-001")
    with h.fail_between_inserts():
        with pytest.raises(StorageFailure):
            create(h.repo, key="key-new-0001")
    assert h.counts() == (1, 1)
    assert h.repo.get_ticket(tenant_id=A, ticket_id=keep.ticket_id) is not None


# ---- unavailable (T9) -----------------------------------------------------------------
def test_unavailable_storage_has_fixed_error(h):
    created = create(h.repo)
    h.make_unavailable()
    with pytest.raises(StorageUnavailable) as e1:
        create(h.repo, key="key-other-01")
    with pytest.raises(StorageUnavailable) as e2:
        h.repo.get_ticket(tenant_id=A, ticket_id=created.ticket_id)
    assert str(e1.value) == str(e2.value) == "Storage is unavailable."
    # malformed ids never reach storage: still the same indistinguishable None
    assert h.repo.get_ticket(tenant_id=A, ticket_id="nope") is None
    # a replay of an existing key also fails closed rather than answering from stale state
    with pytest.raises(StorageUnavailable):
        create(h.repo)


# ---- concurrency (T4) -----------------------------------------------------------------
def _race(h, calls):
    barrier = threading.Barrier(len(calls))

    def run(call):
        barrier.wait()
        try:
            return call()
        except Exception as e:  # noqa: BLE001 - collected for assertions
            return e

    with ThreadPoolExecutor(max_workers=len(calls)) as ex:
        return list(ex.map(run, calls))


def test_20_concurrent_identical_requests_create_exactly_one(h):
    results = _race(h, [lambda: create(h.repo) for _ in range(20)])
    assert all(not isinstance(r, Exception) for r in results), results
    assert len({r.ticket_id for r in results}) == 1 and len({r.run_id for r in results}) == 1
    assert sum(not r.replayed for r in results) == 1
    assert sum(r.replayed for r in results) == 19
    assert h.counts() == (1, 1)


def test_concurrent_same_key_different_payloads_one_winner(h):
    """20 requests, 10 per payload. Exactly one wins; its 9 same-payload siblings replay it;
    the 10 requests with the other payload conflict: 1 + 9 + 10 = 20."""
    calls = [(lambda i=i: create(h.repo, message="payload-%d" % (i % 2))) for i in range(20)]
    results = _race(h, calls)
    assert len(results) == 20
    by_payload = {0: [], 1: []}
    for i, r in enumerate(results):  # _race returns results in submission order
        by_payload[i % 2].append(r)
    winners = [r for r in results if not isinstance(r, Exception) and not r.replayed]
    replays = [r for r in results if not isinstance(r, Exception) and r.replayed]
    conflicts = [r for r in results if isinstance(r, IdempotencyConflict)]
    others = [r for r in results if isinstance(r, Exception) and not isinstance(r, IdempotencyConflict)]
    assert not others, others
    assert (len(winners), len(replays), len(conflicts)) == (1, 9, 10)
    winner = winners[0]
    assert all(r.ticket_id == winner.ticket_id and r.run_id == winner.run_id for r in replays)
    # every success belongs to one payload group, every conflict to the other
    winning_group = next(g for g, rs in by_payload.items() if winner in rs)
    assert all(not isinstance(r, Exception) for r in by_payload[winning_group])
    assert all(isinstance(r, IdempotencyConflict) for r in by_payload[1 - winning_group])
    assert h.counts() == (1, 1)


def test_concurrent_different_keys_all_created(h):
    results = _race(h, [(lambda i=i: create(h.repo, key="key-par-%04d" % i)) for i in range(20)])
    assert all(not isinstance(r, Exception) and not r.replayed for r in results), results
    assert len({r.ticket_id for r in results}) == 20
    assert h.counts() == (20, 20)


def test_concurrent_cross_tenant_same_key(h):
    calls = [(lambda i=i: create(h.repo, tenant=(A if i % 2 else B))) for i in range(20)]
    results = _race(h, calls)
    assert all(not isinstance(r, Exception) for r in results), results
    assert len({r.ticket_id for r in results}) == 2
    assert h.counts() == (2, 2)


def test_implementations_satisfy_protocol_shape():
    for cls in (PgTicketRepository, InMemoryTicketRepository):
        assert callable(cls.create_ticket) and callable(cls.get_ticket)
