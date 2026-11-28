"""Pepper rotation (contract §3.3): coexisting versioned peppers, explicit retirement, fail-closed."""

from __future__ import annotations

import pickle
import subprocess
import sys

import pytest
from conftest import PEPPER, PEPPER_2, bearer

from hermes_service.security.credentials import (
    CredentialVerifier,
    PepperSet,
    compute_key_hash,
)
from hermes_service.security.errors import Unauthenticated

PEPPER_3 = b"synthetic-pepper-0123456789abcdef-C"


def _verifier(env, peppers):
    return CredentialVerifier(env.repo, peppers, env.clock)


def test_old_and_new_pepper_both_verify_while_coexisting(env):
    old = env.issue(credential_id="c-old", pepper_id="p1")
    new = env.issue(credential_id="c-new", pepper_id="p2")
    v = _verifier(env, PepperSet({"p1": PEPPER, "p2": PEPPER_2}, active_id="p2"))
    assert v.authenticate([f"Bearer {old.key}"]).credential_id == "c-old"
    assert v.authenticate([f"Bearer {new.key}"]).credential_id == "c-new"


def test_new_credentials_use_only_the_active_pepper():
    peppers = PepperSet({"p1": PEPPER, "p2": PEPPER_2}, active_id="p2")
    pepper_id, key_hash = peppers.hash_for_new_key("aaaaaaaaaaaa", "s" * 43)
    assert pepper_id == "p2"
    assert key_hash == compute_key_hash(PEPPER_2, "aaaaaaaaaaaa", "s" * 43)
    assert key_hash != compute_key_hash(PEPPER, "aaaaaaaaaaaa", "s" * 43)


def test_hashing_new_key_without_active_pepper_is_an_error():
    with pytest.raises(ValueError):
        PepperSet({"p1": PEPPER}).hash_for_new_key("aaaaaaaaaaaa", "s" * 43)


def test_unknown_pepper_id_is_denied(env):
    issued = env.issue(pepper_id="p1")
    from dataclasses import replace

    env.repo.add(replace(issued.record, pepper_id="p9"))
    with pytest.raises(Unauthenticated):
        env.verifier.authenticate([f"Bearer {issued.key}"])


def test_removed_pepper_denies_its_credentials_but_not_others(env):
    old = env.issue(credential_id="c-old", pepper_id="p1")
    new = env.issue(credential_id="c-new", pepper_id="p2")
    retired = _verifier(env, PepperSet({"p2": PEPPER_2}, active_id="p2"))  # p1 removed explicitly
    with pytest.raises(Unauthenticated):
        retired.authenticate([f"Bearer {old.key}"])
    assert retired.authenticate([f"Bearer {new.key}"]).credential_id == "c-new"


def test_wrong_pepper_value_under_known_id_is_denied(env):
    issued = env.issue(pepper_id="p1")
    swapped = _verifier(env, PepperSet({"p1": PEPPER_3}))
    with pytest.raises(Unauthenticated):
        swapped.authenticate([f"Bearer {issued.key}"])


def test_nothing_is_removed_or_rotated_automatically(env):
    peppers = PepperSet({"p1": PEPPER, "p2": PEPPER_2}, active_id="p2")
    for _ in range(3):
        peppers.hash_for_new_key("aaaaaaaaaaaa", "s" * 43)
    assert peppers.ids == {"p1", "p2"} and peppers.active_id == "p2"


@pytest.mark.parametrize(
    "bad",
    [
        {"p1": b"short"},
        {"p1": b"a" * 40},  # long but no entropy
        {"p1": bytes(range(2)) * 20},  # two distinct bytes
        {"p1": "str-not-bytes" * 5},
        {"BAD ID": PEPPER},
        {"p1": PEPPER, "p2": PEPPER},  # same value under two ids defeats rotation
        {},
    ],
)
def test_weak_or_malformed_peppers_are_rejected_at_init(bad):
    with pytest.raises(ValueError):
        PepperSet(bad)  # type: ignore[arg-type]


def test_from_config_builds_active_plus_previous_and_rejects_malformed_input():
    a, b = PEPPER.decode(), PEPPER_2.decode()
    peppers = PepperSet.from_config(active_id="p2", active_value=b, previous=f" p1={a} ,")
    assert peppers.ids == {"p1", "p2"} and peppers.active_id == "p2" and peppers.get("p1") == PEPPER
    assert PepperSet.from_config(active_id="p1", active_value=a).ids == {"p1"}
    for previous in ("p1", "p1=", "=x", f"p2={a}", f"p1={a},p1={a}", "p3=short"):
        with pytest.raises(ValueError) as exc:
            PepperSet.from_config(active_id="p2", active_value=b, previous=previous)
        assert a not in str(exc.value) and b not in str(exc.value)


def test_active_id_must_exist():
    with pytest.raises(ValueError):
        PepperSet({"p1": PEPPER}, active_id="p2")


def test_pepper_material_never_in_repr_str_errors_or_pickle():
    peppers = PepperSet({"p1": PEPPER, "p2": PEPPER_2}, active_id="p1")
    for text in (repr(peppers), str(peppers)):
        assert PEPPER.decode() not in text and PEPPER_2.decode() not in text
    with pytest.raises(TypeError):
        pickle.dumps(peppers)
    secret = b"weak-secret-value-xyz-" + b"a" * 20
    for bad in ({"p1": secret[:8]}, {"p1": secret, "p2": secret}):
        with pytest.raises(ValueError) as exc:
            PepperSet(bad)
        assert secret.decode() not in str(exc.value) and secret.decode()[:8] not in str(exc.value)


def test_pepper_rotation_does_not_leak_in_http_denial(app, env):
    from fastapi.testclient import TestClient

    issued = env.issue(pepper_id="p1")
    from dataclasses import replace

    env.repo.add(replace(issued.record, pepper_id="gone"))
    r = TestClient(app, raise_server_exceptions=False).get("/v1/whoami", headers=bearer(issued.key))
    assert r.status_code == 401 and "gone" not in r.text and "pepper" not in r.text.lower()


def test_importing_security_package_reads_no_application_configuration():
    code = (
        "import os, sys\n"
        f"sys.path[:0] = {sys.path!r}\n"
        "seen = []\n"
        "class E(dict):\n"
        "    def __getitem__(s, k): seen.append(k); return dict.__getitem__(s, k)\n"
        "    def get(s, k, d=None): seen.append(k); return dict.get(s, k, d)\n"
        "    def __contains__(s, k): seen.append(k); return dict.__contains__(s, k)\n"
        "os.environ = E(os.environ)\n"
        "import hermes_service.security.credentials, hermes_service.security.dependencies\n"
        "import hermes_service.security.route_audit, hermes_service.repositories.credentials\n"
        "bad = [k for k in seen if k.upper().startswith(('SUPPORTOPS', 'PEPPER', 'DATABASE', 'PG'))]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
