"""Ledger: состояние Recommendation/Application как чистая свёртка event log.

Каждая команда: проверка -> append в лог -> применение. При открытии существующего
лога состояние перестраивается теми же обработчиками (восстановление после рестарта).
Любое нарушение инвариантов при replay => LogCorruptError.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .dto import Recommendation, Verdict, context_signature
from .errors import (CommonsError, ConflictError, DuplicateError, LogCorruptError,
                     UnknownEntityError, ValidationError)
from .eventlog import EventLog
from .lifecycle import (APP_TRANSITIONS, REC_TRANSITIONS, AppState, RecState,
                        check_transition)
from .vocab import REJECT_REASONS

# Вес в статистике: HARMFUL весит как 3 неудачи (асимметрия: вред хуже промаха).
_WEIGHTS = {Verdict.SOLVED: (1, 1), Verdict.NOT_SOLVED: (0, 1),
            Verdict.HARMFUL: (0, 3), Verdict.BLOCKED_BY_POLICY: (0, 1)}


@dataclass
class RecRecord:
    rec: Recommendation
    state: RecState = RecState.PROPOSED
    reason: Optional[str] = None


@dataclass
class AppRecord:
    application_id: str
    recommendation_id: str
    version: int
    agent_id: str
    context: dict[str, str]
    case_ref: str
    state: AppState = AppState.PROPOSED
    validation: Optional[dict[str, Any]] = None
    reject_reason: Optional[str] = None
    observation: Optional[dict[str, Any]] = None
    verdict: Optional[Verdict] = None
    quality: Optional[int] = None
    rejection_verdict: Optional[Verdict] = None
    self_reports: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=lambda: ["PROPOSED"])


class Ledger:
    def __init__(self, log: EventLog) -> None:
        self.log = log
        self.recs: dict[tuple[str, int], RecRecord] = {}
        self.latest: dict[str, int] = {}
        self.apps: dict[str, AppRecord] = {}
        # (rec_id, version) -> {"all": [s, n], "<ctx_sig>": [s, n]}; только из независимых evaluations
        self.stats: dict[tuple[str, int], dict[str, list[float]]] = {}
        for ev in log.events:
            try:
                self._handle(ev["type"], ev["data"], commit=True)
            except LogCorruptError:
                raise
            except Exception as e:  # любое нарушение при replay = повреждённый лог
                raise LogCorruptError(f"replay invariant violated: {type(e).__name__}") from e

    # ---- команды -------------------------------------------------------
    def emit(self, etype: str, data: dict[str, Any]) -> None:
        self._handle(etype, data, commit=False)  # проверка до записи в лог
        self.log.append(etype, data)
        self._handle(etype, data, commit=True)

    # ---- обработчики ---------------------------------------------------
    def _rec(self, rid: str, ver: int) -> RecRecord:
        r = self.recs.get((rid, ver))
        if r is None:
            raise UnknownEntityError("recommendation")
        return r

    def _app(self, aid: str) -> AppRecord:
        a = self.apps.get(aid)
        if a is None:
            raise UnknownEntityError("application")
        return a

    def _handle(self, t: str, d: dict[str, Any], commit: bool) -> None:
        if t == "rec.proposed":
            rec = Recommendation.from_dict(d["rec"])
            key = (rec.recommendation_id, rec.version)
            if key in self.recs:
                raise DuplicateError("recommendation version exists")
            if rec.version != self.latest.get(rec.recommendation_id, 0) + 1:
                raise ConflictError("version must be latest+1")
            if commit:
                self.recs[key] = RecRecord(rec)
                self.latest[rec.recommendation_id] = rec.version
        elif t in ("rec.validated", "rec.rejected", "rec.revoked"):
            r = self._rec(d["recommendation_id"], d["version"])
            target = {"rec.validated": RecState.VALIDATED, "rec.rejected": RecState.REJECTED,
                      "rec.revoked": RecState.REVOKED}[t]
            check_transition(REC_TRANSITIONS, r.state, target)
            if commit:
                r.state, r.reason = target, d.get("reason")
        elif t == "app.proposed":
            if d["application_id"] in self.apps:
                raise DuplicateError("application exists")
            self._rec(d["recommendation_id"], d["version"])
            if commit:
                self.apps[d["application_id"]] = AppRecord(
                    d["application_id"], d["recommendation_id"], d["version"],
                    d["agent_id"], dict(d["context"]), d["case_ref"])
        elif t in ("app.validated", "app.rejected", "app.applied", "app.observed", "app.evaluated"):
            a = self._app(d["application_id"])
            target = {"app.validated": AppState.VALIDATED, "app.rejected": AppState.REJECTED,
                      "app.applied": AppState.APPLIED, "app.observed": AppState.OBSERVED,
                      "app.evaluated": AppState.EVALUATED}[t]
            check_transition(APP_TRANSITIONS, a.state, target)
            if t == "app.rejected" and d["reason"] not in REJECT_REASONS:
                raise ValidationError("unknown reject reason")
            if t == "app.evaluated":
                Verdict(d["verdict"])
                if not isinstance(d.get("quality"), int):
                    raise ValidationError("quality must be int")
            if t == "app.observed" and not isinstance(d.get("observation"), dict):
                raise ValidationError("observation must be a dict")
            if t == "app.validated" and not isinstance(d.get("validation"), dict):
                raise ValidationError("validation must be a dict")
            if t == "app.applied":
                # одно исполнение в мире = одно доказательство: (case_ref, действие) засчитывается один раз
                act = self.recs[(a.recommendation_id, a.version)].rec.proposed_action
                for o in self.apps.values():
                    if (o is not a and o.case_ref == a.case_ref and o.state in
                            (AppState.APPLIED, AppState.OBSERVED, AppState.EVALUATED)
                            and self.recs[(o.recommendation_id, o.version)].rec.proposed_action == act):
                        raise DuplicateError("execution already claimed by another application")
            if commit:
                a.state = target
                a.history.append(target.value)
                if t == "app.validated":
                    a.validation = dict(d["validation"])
                elif t == "app.rejected":
                    a.reject_reason = d["reason"]
                elif t == "app.observed":
                    a.observation = dict(d["observation"])
                elif t == "app.evaluated":
                    a.verdict, a.quality = Verdict(d["verdict"]), d["quality"]
                    self._count(a, a.verdict)
        elif t == "app.rejection_assessed":
            a = self._app(d["application_id"])
            if a.state is not AppState.REJECTED or a.reject_reason != "denied_by_local_policy":
                raise ValidationError("only denied_by_local_policy rejections are assessable")
            if a.rejection_verdict is not None:
                raise DuplicateError("rejection already assessed")
            v = Verdict(d["verdict"])
            if commit:
                a.rejection_verdict = v
                self._count(a, v)
        elif t == "app.self_report":
            a = self._app(d["application_id"])
            if commit:
                a.self_reports.append(d["outcome"])  # хранится, но в оценку не входит
        else:
            raise ValidationError("unknown event type")

    def _count(self, a: AppRecord, v: Verdict) -> None:
        w = _WEIGHTS.get(v)
        if w is None:  # NOT_APPLICABLE / UNVERIFIED не учитываются
            return
        entry = self.stats.setdefault((a.recommendation_id, a.version), {"all": [0.0, 0.0]})
        sig = context_signature(a.context)
        for k in ("all", sig):
            s = entry.setdefault(k, [0.0, 0.0])
            s[0] += w[0]
            s[1] += w[1]
