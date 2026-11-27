"""Модели жизненного цикла Recommendation и Application.

Recommendation = версионированное знание. Application = конкретное применение.
Одна Recommendation (версия) -> много Application.

Application (основная ветка):
    PROPOSED -> VALIDATED -> APPLIED -> OBSERVED -> EVALUATED
Альтернативные переходы, которые потребовала реальность эксперимента:
    PROPOSED|VALIDATED -> REJECTED   (неприменимо / устарело / отказ локальной политики)
REJECTED и EVALUATED — терминальные.

Recommendation:
    PROPOSED -> VALIDATED -> REVOKED ;  PROPOSED -> REJECTED
Новая версия = новая запись (id, version+1), статистика не наследуется.
"""
from __future__ import annotations

from enum import Enum

from .errors import TransitionError


class RecState(str, Enum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


class AppState(str, Enum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    APPLIED = "APPLIED"
    OBSERVED = "OBSERVED"
    EVALUATED = "EVALUATED"
    REJECTED = "REJECTED"


REC_TRANSITIONS: dict[RecState, frozenset[RecState]] = {
    RecState.PROPOSED: frozenset({RecState.VALIDATED, RecState.REJECTED}),
    RecState.VALIDATED: frozenset({RecState.REVOKED}),
    RecState.REJECTED: frozenset(),
    RecState.REVOKED: frozenset(),
}

APP_TRANSITIONS: dict[AppState, frozenset[AppState]] = {
    AppState.PROPOSED: frozenset({AppState.VALIDATED, AppState.REJECTED}),
    AppState.VALIDATED: frozenset({AppState.APPLIED, AppState.REJECTED}),
    AppState.APPLIED: frozenset({AppState.OBSERVED}),
    AppState.OBSERVED: frozenset({AppState.EVALUATED}),
    AppState.EVALUATED: frozenset(),
    AppState.REJECTED: frozenset(),
}


def check_transition(table: dict, current: Enum, target: Enum) -> None:
    if target not in table[current]:
        raise TransitionError(f"illegal transition {current.value}->{target.value}")
