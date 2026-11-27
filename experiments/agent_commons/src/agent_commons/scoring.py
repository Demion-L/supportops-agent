"""Минимальная контекстно-зависимая оценка рекомендации (S8, S9).

Не сумма лайков и не `confidence`. Источник — только независимые evaluations.

    prior_rec = (S_all + 1) / (N_all + 2)                    # сглаженная по всем контекстам
    score_ctx = (S_ctx + K * prior_rec) / (N_ctx + K)        # контекст сжимается к prior_rec

В режиме "global" используется только prior_rec (ablation для проверки S9).
Нулевая история => 0.5 (нейтрально). Вредный исход весит как 3 неудачи.
"""
from __future__ import annotations

from typing import Optional

PRIOR_STRENGTH = 2.0


def score(entry: Optional[dict[str, list[float]]], ctx_sig: str, mode: str = "context") -> tuple[float, float, float]:
    """-> (score, context_trials, global_trials)"""
    if entry is None:
        return 0.5, 0.0, 0.0
    s_all, n_all = entry["all"]
    prior = (s_all + 1.0) / (n_all + 2.0)
    s_c, n_c = entry.get(ctx_sig, [0.0, 0.0])
    if mode == "global":
        return prior, n_c, n_all
    return (s_c + PRIOR_STRENGTH * prior) / (n_c + PRIOR_STRENGTH), n_c, n_all
