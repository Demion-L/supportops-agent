"""Локальная политика авторизации агента A. Независима от Commons (S1, S5).

Не принимает confidence/score и не знает, откуда пришло действие.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .vocab import ActionId


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


DEFAULT_DENIED = frozenset({ActionId.REFUND_WITHOUT_VERIFICATION, ActionId.GRANT_ADMIN_OVERRIDE})


class LocalPolicy:
    def __init__(self, denied: frozenset[ActionId] = DEFAULT_DENIED) -> None:
        self._denied = frozenset(denied)

    def authorize(self, action: ActionId, context: Mapping[str, str]) -> PolicyDecision:
        if action in self._denied:
            return PolicyDecision(False, "action_denied_by_local_policy")
        return PolicyDecision(True, "allowed")
