"""No secrets in responses, logs, reprs or exceptions."""

from __future__ import annotations

import pytest
from conftest import PEPPER, bearer

from hermes_service.security.credentials import (
    PepperSet,
    compute_key_hash,
    generate_api_key,
)
from hermes_service.security.errors import Unauthenticated


def _secrets_of(issued, env):
    prefix = issued.record.key_prefix
    secret = issued.key.split(".")[2]
    return [issued.key, prefix, secret, issued.record.key_hash.hex(), PEPPER.decode()]


@pytest.mark.parametrize("mode", ["good", "wrong_secret", "revoked", "unknown_prefix", "garbage"])
def test_no_secret_material_in_responses_or_logs(client, env, security_logs, mode):
    from datetime import timedelta

    from conftest import NOW

    issued = env.issue(revoked_at=NOW - timedelta(days=1) if mode == "revoked" else None)
    key = {
        "good": issued.key,
        "wrong_secret": f"sok1.{issued.record.key_prefix}.{generate_api_key().secret}",
        "revoked": issued.key,
        "unknown_prefix": generate_api_key().render(),
        "garbage": "SECRET-MARKER-GARBAGE-xyz",
    }[mode]
    r = client.get("/v1/whoami", headers={**bearer(key), "X-Request-ID": "rid-leak-test"})
    blob = r.text + "\n".join(f"{k}:{v}" for k, v in r.headers.items()) + security_logs.text
    for needle in [*_secrets_of(issued, env), key, key.split(".")[-1]]:
        assert needle not in blob, needle
    assert "Authorization" not in security_logs.text


def test_unauthenticated_prefix_is_never_logged(client, env, security_logs):
    attacker = generate_api_key()
    client.get("/v1/whoami", headers=bearer(attacker.render()))
    assert attacker.prefix not in security_logs.text
    assert attacker.secret not in security_logs.text


def test_log_line_carries_category_and_ids_only_when_record_found(client, env, security_logs):
    issued = env.issue(tenant_id="tenant-a", credential_id="cred-a1")
    bad = f"sok1.{issued.record.key_prefix}.{generate_api_key().secret}"
    client.get("/v1/whoami", headers=bearer(bad))
    assert "reason=bad_secret" in security_logs.text
    assert "credential_id=cred-a1" in security_logs.text and "tenant_id=tenant-a" in security_logs.text
    security_logs.clear()
    client.get("/v1/whoami", headers=bearer(generate_api_key().render()))
    assert "reason=unknown_prefix" in security_logs.text
    assert "credential_id=-" in security_logs.text and "tenant_id=-" in security_logs.text


def test_log_injection_via_stored_credential_id_is_neutralised(client, env, security_logs):
    issued = env.issue(credential_id="evil\nFAKE LOG LINE reason=ok")
    client.get("/v1/whoami", headers=bearer(issued.key))
    assert "FAKE LOG LINE" not in security_logs.text


def test_reprs_do_not_expose_secrets(env):
    issued = env.issue()
    k = generate_api_key()
    parsed_repr = repr(k)
    assert k.secret not in parsed_repr and k.secret not in str(k)
    assert issued.record.key_hash.hex() not in repr(issued.record)
    assert "synthetic-pepper" not in repr(env.peppers) and "synthetic-pepper" not in str(env.peppers)
    assert "synthetic-pepper" not in repr(env.verifier.__dict__.get("_peppers"))
    with pytest.raises(TypeError):
        import pickle

        pickle.dumps(env.peppers)


def test_security_exceptions_expose_only_the_fixed_code(env):
    with pytest.raises(Unauthenticated) as info:
        env.verifier.authenticate([f"Bearer {generate_api_key().render()}"])
    assert str(info.value) == "unauthenticated"
    assert "unknown_prefix" not in str(info.value) and "unknown_prefix" not in repr(info.value.args)


def test_unhandled_error_in_handler_does_not_leak(client, env, app):
    marker = "SECRET-MARKER-HANDLER-77"
    from fastapi import Depends

    from hermes_service.security.dependencies import authenticate

    @app.get("/v1/boom")
    def boom(ctx=Depends(authenticate)):
        raise RuntimeError(marker)

    issued = env.issue()
    r = client.get("/v1/boom", headers=bearer(issued.key))
    assert r.status_code == 500 and marker not in r.text


def test_compute_key_hash_is_not_the_key():
    h = compute_key_hash(PEPPER, "aaaaaaaaaaaa", "s" * 43)
    assert "s" * 43 not in h.hex() and PepperSet({"p1": PEPPER}).get("p1") == PEPPER
