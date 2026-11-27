"""Простые типы, видимые агентам. Никакой истины о мире здесь нет."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .vocab import ProblemType


@dataclass(frozen=True)
class Task:
    task_id: str  # синтетический идентификатор, одновременно непрозрачный case_ref
    problem_type: ProblemType
    context: Mapping[str, str]


@dataclass(frozen=True)
class Receipt:
    executed: bool  # агент видит только факт исполнения, но не исход
