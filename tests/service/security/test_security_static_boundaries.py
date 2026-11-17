"""Static guards: single TenantContext, constant-time compare, DI boundaries, no worker authority."""

from __future__ import annotations

import ast
from pathlib import Path

import supportops_service
from supportops_service import interfaces
from supportops_service.security import tenant_context

SRC = Path(supportops_service.__file__).parent
SECURITY = SRC / "security"
SECRET_NAMES = ("secret", "key_hash", "digest", "computed", "pepper", "token")


def _py(path: Path):
    return [p for p in path.rglob("*.py")]


def test_tenant_context_has_exactly_one_definition():
    defs = []
    for p in SRC.rglob("*.py"):
        tree = ast.parse(p.read_text())
        defs += [p for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "TenantContext"]
    assert defs == [SRC / "interfaces.py"]
    assert tenant_context.TenantContext is interfaces.TenantContext


def test_tenant_context_is_constructed_only_by_the_factory():
    offenders = []
    for p in SRC.rglob("*.py"):
        if p.name in {"interfaces.py", "tenant_context.py"}:
            continue
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "TenantContext":
                offenders.append(p.name)
    assert offenders == []


def test_no_equality_operator_on_secret_material():
    offenders = []
    for p in _py(SECURITY):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Compare) and any(isinstance(o, (ast.Eq, ast.NotEq)) for o in n.ops):
                text = ast.unparse(n).lower()
                if text.startswith("len("):  # length checks are not secret comparisons
                    continue
                if any(name in text for name in SECRET_NAMES):
                    offenders.append(f"{p.name}: {ast.unparse(n)}")
    assert offenders == []


def test_security_modules_read_no_environment_and_no_network():
    banned = {"os", "socket", "requests", "httpx", "urllib", "subprocess"}
    for p in _py(SECURITY):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Import):
                assert not {a.name.split(".")[0] for a in n.names} & banned, p.name
            if isinstance(n, ast.ImportFrom) and n.module:
                assert n.module.split(".")[0] not in banned, p.name


def test_only_the_authorization_header_is_read():
    for p in _py(SECURITY):
        text = p.read_text().lower()
        for name in ("x-tenant", "tenant-id", "x-scope", "query_params", "cookies", ".json()", "request.body"):
            assert name not in text, (p.name, name)


def test_agent_package_does_not_import_service_security():
    agent_src = SRC.parent / "supportops_agent"
    for p in _py(agent_src):
        assert "supportops_service" not in p.read_text(), p


def test_credential_repository_is_confined_to_authentication_code():
    """Worker-side code must not be able to reach credentials (contract AC-11)."""
    # ``app.py`` is the API composition root (wires the repository into the verifier); it is the
    # only module outside security/ and the credentials repository allowed to name these types.
    for p in SRC.rglob("*.py"):
        rel = p.relative_to(SRC)
        if rel.parts[0] == "security" or rel.parts in {("interfaces.py",), ("app.py",)}:
            continue
        if rel.parts == ("repositories", "credentials.py"):
            continue
        assert "CredentialRepository" not in p.read_text() and "CredentialRecord" not in p.read_text(), rel
    worker = SRC / "worker"
    if worker.exists():
        for p in _py(worker):
            assert "security" not in p.read_text().lower().replace("security_", ""), p


def test_no_global_mutable_authentication_cache():
    """No TTL/lru caches in authentication (contract I-10)."""
    for p in _py(SECURITY):
        text = p.read_text()
        assert "lru_cache" not in text and "cachetools" not in text and "@cache" not in text, p.name


def test_temporary_contracts_module_is_gone_and_types_come_from_interfaces():
    """After interface alignment there is one definition of each shared type (interfaces.py)."""
    assert not (SECURITY / "contracts.py").exists()
    for p in _py(SECURITY):
        assert "security.contracts" not in p.read_text(), p.name
    shared = {"Scope", "TenantContext", "CredentialRecord", "CredentialRepository", "Clock"}
    defined: dict[str, list[str]] = {name: [] for name in shared}
    for p in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.ClassDef) and node.name in shared:
                defined[node.name].append(p.name)
    assert defined == {name: ["interfaces.py"] for name in shared}
