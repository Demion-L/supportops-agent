"""T12 and SQL-construction boundaries, checked statically and in a clean interpreter."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "hermes_service"
FILES = [SRC / "ids.py", *sorted((SRC / "repositories").glob("*.py"))]
FORBIDDEN_IMPORTS = {"supportops_agent", "openai", "httpx", "requests", "urllib", "socket", "subprocess", "http"}


def _tree(p):
    return ast.parse(p.read_text())


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_agent_llm_or_network_imports(path):  # T12
    for node in ast.walk(_tree(path)):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for m in mods:
            assert m.split(".")[0] not in FORBIDDEN_IMPORTS, (path.name, m)


def test_importing_repositories_loads_no_agent_or_llm_client():  # T12 (runtime)
    code = (
        "import sys, hermes_service.repositories.tickets, hermes_service.repositories.fakes;"
        "bad=[m for m in ('supportops_agent','openai') if m in sys.modules];"
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-I", "-c", code], check=True, cwd=SRC.parents[1])


def _code_strings_and_names(tree):
    """String literals (docstrings excluded) and identifiers of a module."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and ast.get_docstring(node, clean=False):
            docstrings.add(id(node.body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            out.append(node.value)
        elif isinstance(node, ast.Name):
            out.append(node.id)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append(ast.unparse(node))
    return out


def test_repositories_never_reference_credentials_or_runs_queue():
    for p in FILES:
        for text in _code_strings_and_names(_tree(p)):
            # api_credentials belongs to the Alpha-owned credentials repository only (API-004
            # integration); every ticket-side module must still never reference it.
            if p.name != "credentials.py":
                assert "api_credentials" not in text.lower(), p.name
            assert "runs_queue" not in text.lower(), p.name


def test_execute_arguments_are_module_constants_not_built_strings():
    tree = _tree(SRC / "repositories" / "tickets.py")
    consts = {
        n.target.id: n.value
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "execute"]
    assert len(calls) == 4  # insert ticket, insert run, select by key, select ticket
    for call in calls:
        arg = call.args[0]
        assert isinstance(arg, ast.Name) and arg.id in consts
        assert not any(isinstance(n, (ast.JoinedStr, ast.Mod, ast.Call)) for n in ast.walk(consts[arg.id]))
    assert not any(isinstance(n, ast.JoinedStr) for n in ast.walk(tree))
    assert not any(isinstance(n, ast.Attribute) and n.attr == "format" for n in ast.walk(tree))


def test_no_global_tenant_state():
    for p in FILES:
        for node in _tree(p).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                name = ast.unparse(node.targets[0] if isinstance(node, ast.Assign) else node.target)
                assert "tenant" not in name.lower() or name.endswith("_RE"), (p.name, name)
