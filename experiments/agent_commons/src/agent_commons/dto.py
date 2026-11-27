"""Явные DTO коммуникационного протокола Commons и строгая валидация.

Принцип: неизвестные поля — ошибка; свободного текста нет; значения — из закрытых
словарей. `confidence` — заявленная автором величина, доказательством не является.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional

from .errors import ValidationError
from .vocab import CONTEXT_VOCAB, ActionId, ProblemType

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
_REC_ID_RE = re.compile(r"^rec_[a-z0-9_]{1,40}$")
_APP_ID_RE = re.compile(r"^app_[a-z0-9_]{1,60}$")


def _check_id(value: Any, regex: re.Pattern, what: str) -> str:
    if not isinstance(value, str) or not regex.match(value):
        raise ValidationError(f"invalid {what}")
    return value


def validate_context(ctx: Any) -> dict[str, str]:
    """Контекст — только ключи/значения из закрытого словаря (S2, S3)."""
    if not isinstance(ctx, Mapping):
        raise ValidationError("context must be a mapping")
    out: dict[str, str] = {}
    for k, v in ctx.items():
        if k not in CONTEXT_VOCAB:
            raise ValidationError("unknown context key")
        if not isinstance(v, str) or v not in CONTEXT_VOCAB[k]:
            raise ValidationError("value outside closed vocabulary")
        out[k] = v
    return out


def context_signature(ctx: Mapping[str, str]) -> str:
    return "|".join(f"{k}={ctx[k]}" for k in sorted(ctx))


def matches(applicability: Mapping[str, str], ctx: Mapping[str, str]) -> bool:
    return all(ctx.get(k) == v for k, v in applicability.items())


def _strict_keys(d: Any, required: set[str], what: str) -> None:
    if not isinstance(d, Mapping):
        raise ValidationError(f"{what} must be a mapping")
    keys = set(d.keys())
    if keys != required:
        raise ValidationError(f"{what}: unexpected or missing fields")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class Verdict(str, Enum):
    SOLVED = "SOLVED"
    NOT_SOLVED = "NOT_SOLVED"
    HARMFUL = "HARMFUL"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNVERIFIED = "UNVERIFIED"


class FeedbackStage(str, Enum):
    APPLIED = "applied"
    DENIED_BY_LOCAL_POLICY = "denied_by_local_policy"
    SELF_REPORT = "self_report"


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    version: int
    problem_type: ProblemType
    proposed_action: ActionId
    applicability: Mapping[str, str]
    confidence: float
    author_id: str

    def __post_init__(self) -> None:
        _check_id(self.recommendation_id, _REC_ID_RE, "recommendation_id")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValidationError("version must be int >= 1")
        try:
            object.__setattr__(self, "problem_type", ProblemType(self.problem_type))
            object.__setattr__(self, "proposed_action", ActionId(self.proposed_action))
        except ValueError:
            raise ValidationError("problem_type/proposed_action outside closed vocabulary") from None
        object.__setattr__(self, "applicability", MappingProxyType(validate_context(self.applicability)))
        c = self.confidence
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or not 0.0 <= c <= 1.0:
            raise ValidationError("confidence must be a finite number in [0,1]")
        object.__setattr__(self, "confidence", float(c))
        _check_id(self.author_id, _ID_RE, "author_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "version": self.version,
            "problem_type": self.problem_type.value,
            "proposed_action": self.proposed_action.value,
            "applicability": dict(self.applicability),
            "confidence": self.confidence,
            "author_id": self.author_id,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "Recommendation":
        _strict_keys(
            d,
            {"recommendation_id", "version", "problem_type", "proposed_action",
             "applicability", "confidence", "author_id"},
            "recommendation",
        )
        return cls(**dict(d))

    def fingerprint(self) -> str:
        d = self.to_dict()
        return hashlib.sha256(canonical(d).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class AskRequest:
    """Только публичные признаки задачи. Никакого внутреннего состояния A (S2)."""

    requester_id: str
    problem_type: ProblemType
    context: Mapping[str, str]
    limit: int = 5

    def __post_init__(self) -> None:
        _check_id(self.requester_id, _ID_RE, "requester_id")
        try:
            object.__setattr__(self, "problem_type", ProblemType(self.problem_type))
        except ValueError:
            raise ValidationError("problem_type outside closed vocabulary") from None
        object.__setattr__(self, "context", validate_context(self.context))
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 20:
            raise ValidationError("limit must be int in [1,20]")

    def to_dict(self) -> dict[str, Any]:
        return {"requester_id": self.requester_id, "problem_type": self.problem_type.value,
                "context": dict(self.context), "limit": self.limit}

    @classmethod
    def from_dict(cls, d: Any) -> "AskRequest":
        _strict_keys(d, {"requester_id", "problem_type", "context", "limit"}, "ask")
        return cls(**dict(d))


@dataclass(frozen=True)
class Candidate:
    recommendation: Recommendation
    score: float  # контекстная оценка на основе независимых evaluations, не confidence
    context_trials: float
    global_trials: float

    def to_dict(self) -> dict[str, Any]:
        return {"recommendation": self.recommendation.to_dict(), "score": round(self.score, 6),
                "context_trials": self.context_trials, "global_trials": self.global_trials}


@dataclass(frozen=True)
class AskResponse:
    candidates: tuple[Candidate, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"candidates": [c.to_dict() for c in self.candidates]}


@dataclass(frozen=True)
class ShareRequest:
    recommendation: Recommendation

    def __post_init__(self) -> None:
        if not isinstance(self.recommendation, Recommendation):
            raise ValidationError("share requires a Recommendation")

    def to_dict(self) -> dict[str, Any]:
        return {"recommendation": self.recommendation.to_dict()}


@dataclass(frozen=True)
class ShareResponse:
    recommendation_id: str
    version: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {"recommendation_id": self.recommendation_id, "version": self.version, "status": self.status}


@dataclass(frozen=True)
class ValidateRequest:
    application_id: str
    agent_id: str
    recommendation_id: str
    version: int
    context: Mapping[str, str]
    case_ref: str  # непрозрачная ссылка для независимого Evaluator; наружу не отдаётся

    def __post_init__(self) -> None:
        _check_id(self.application_id, _APP_ID_RE, "application_id")
        _check_id(self.agent_id, _ID_RE, "agent_id")
        _check_id(self.recommendation_id, _REC_ID_RE, "recommendation_id")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValidationError("version must be int >= 1")
        object.__setattr__(self, "context", validate_context(self.context))
        _check_id(self.case_ref, _ID_RE, "case_ref")

    def to_dict(self) -> dict[str, Any]:
        return {"application_id": self.application_id, "agent_id": self.agent_id,
                "recommendation_id": self.recommendation_id, "version": self.version,
                "context": dict(self.context), "case_ref": self.case_ref}


@dataclass(frozen=True)
class ValidateResponse:
    application_id: str
    ok: bool
    applicable: bool
    is_current_version: bool
    reasons: tuple[str, ...] = ()
    # ok=True НЕ даёт полномочий (S1): это лишь проверка целостности/актуальности знания.

    def to_dict(self) -> dict[str, Any]:
        return {"application_id": self.application_id, "ok": self.ok, "applicable": self.applicable,
                "is_current_version": self.is_current_version, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class FeedbackRequest:
    application_id: str
    agent_id: str
    stage: FeedbackStage
    self_reported_outcome: Optional[str] = None  # информативно; в оценку НЕ входит (S7)

    def __post_init__(self) -> None:
        _check_id(self.application_id, _APP_ID_RE, "application_id")
        _check_id(self.agent_id, _ID_RE, "agent_id")
        try:
            object.__setattr__(self, "stage", FeedbackStage(self.stage))
        except ValueError:
            raise ValidationError("unknown feedback stage") from None
        if self.self_reported_outcome not in (None, "success", "failure"):
            raise ValidationError("self_reported_outcome must be success|failure|None")

    def to_dict(self) -> dict[str, Any]:
        return {"application_id": self.application_id, "agent_id": self.agent_id,
                "stage": self.stage.value, "self_reported_outcome": self.self_reported_outcome}


@dataclass(frozen=True)
class FeedbackResponse:
    application_id: str
    accepted: bool
    state: str

    def to_dict(self) -> dict[str, Any]:
        return {"application_id": self.application_id, "accepted": self.accepted, "state": self.state}


@dataclass(frozen=True)
class ApplicationEvidence:
    """Что Evaluator получает от ledger: заявленные факты, подлежащие проверке по миру."""

    application_id: str
    recommendation_id: str
    version: int
    proposed_action: ActionId
    rec_applicability: Mapping[str, str]
    claimed_context: Mapping[str, str]
    case_ref: str
    state: str
    reject_reason: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)
