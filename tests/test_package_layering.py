"""Архитектурные границы пакета supportops_agent (статическая проверка импортов).

Слои (зависимость только вниз): models <- classification <- business <- tools <- runtime.
Циклы между подпакетами и импорт service-слоя из агентного ядра запрещены.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "supportops_agent"
LAYERS = ["models", "classification", "business", "tools", "runtime"]  # снизу вверх


def _layer(module: str) -> str | None:
    parts = module.split(".")
    return parts[1] if parts[0] == "supportops_agent" and len(parts) > 1 else None


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.ImportFrom):
            assert n.level == 0, f"{path}: относительные импорты запрещены, используйте абсолютные"
            if n.module:
                out.add(n.module)
                out.update(f"{n.module}.{a.name}" for a in n.names)
        elif isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
    return out


def _files() -> list[Path]:
    files = sorted(p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts)
    assert len(files) > 10
    return files


def test_every_top_level_module_and_package_has_a_declared_layer():
    names = {p.stem if p.is_file() else p.name for p in ROOT.iterdir() if p.name not in {"__pycache__", "__init__.py"}}
    assert names == set(LAYERS)


def test_dependencies_point_only_downwards():
    rank = {name: i for i, name in enumerate(LAYERS)}
    for path in _files():
        own = path.relative_to(ROOT).parts[0].removesuffix(".py")
        if own == "__init__":
            continue
        for mod in _imports(path):
            dep = _layer(mod)
            if dep in rank and dep != own:
                assert rank[dep] < rank[own], f"{path.relative_to(ROOT)} ({own}) импортирует {mod} ({dep})"


def test_agent_core_does_not_import_service_layer():
    for path in _files():
        assert not any(m.split(".")[0] == "hermes_service" for m in _imports(path)), path
