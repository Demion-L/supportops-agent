import hashlib
import json

import pytest
from supportops_service import ids
from supportops_service.repositories import _inputs


def test_ids_match_db_check_format_and_are_unique():
    t = {ids.new_ticket_id() for _ in range(500)}
    r = {ids.new_run_id() for _ in range(500)}
    assert len(t) == len(r) == 500
    assert all(ids.is_ticket_id(x) and x.startswith("tkt_") and len(x) == 36 for x in t)
    assert all(ids.is_run_id(x) and x.startswith("run_") for x in r)


@pytest.mark.parametrize("bad", ["", "tkt_", "tkt_" + "a" * 31, "tkt_" + "A" * 32, "tkt_" + "a" * 32 + "\n", "run_" + "a" * 32, None, 1, b"tkt_"])
def test_is_ticket_id_rejects(bad):
    assert not ids.is_ticket_id(bad)


def test_ids_use_secrets(monkeypatch):
    monkeypatch.setattr(ids.secrets, "token_hex", lambda n: "ab" * n)
    assert ids.new_ticket_id() == "tkt_" + "ab" * 16


def test_request_hash_is_canonical():
    expected = hashlib.sha256(
        json.dumps({"customer_id": "c", "message": "ж", "order_id": None}, sort_keys=True,
                   separators=(",", ":"), ensure_ascii=False).encode()
    ).digest()
    assert _inputs.compute_request_hash("c", "ж", None) == expected
    assert _inputs.compute_request_hash("c", "m", None) != _inputs.compute_request_hash("c", "m", "")
    assert _inputs.compute_request_hash("a", "bc", None) != _inputs.compute_request_hash("ab", "c", None)
    assert len(expected) == 32
