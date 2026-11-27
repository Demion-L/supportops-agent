"""CommonsService: реализация Ask/Share/Validate/Feedback поверх Ledger.

Сервис НИЧЕГО не исполняет и не выдаёт полномочий (S1, S4). Feedback от агента
не меняет оценку (S7): оценку меняют только записи через EvaluationSink.
"""
from __future__ import annotations

from typing import Optional, Sequence

from .dto import (ApplicationEvidence, AskRequest, AskResponse, Candidate, FeedbackRequest,
                  FeedbackResponse, FeedbackStage, Recommendation, ShareRequest,
                  ShareResponse, ValidateRequest, ValidateResponse, Verdict,
                  context_signature, matches)
from .errors import ForbiddenError, UnknownEntityError
from .eventlog import EventLog
from .ledger import Ledger
from .lifecycle import AppState, RecState
from .scoring import score


class CommonsService:
    def __init__(self, log: Optional[EventLog] = None, *, scoring_mode: str = "context",
                 apply_applicability_filter: bool = True) -> None:
        self.ledger = Ledger(log or EventLog())
        self.scoring_mode = scoring_mode
        self.apply_applicability_filter = apply_applicability_filter  # False = fault injection

    # ---- порт для агентов ---------------------------------------------
    def port(self) -> "AgentPort":
        return AgentPort(self)

    def evaluation_sink(self) -> "Sink":
        return Sink(self)

    # ---- Share ---------------------------------------------------------
    def _share(self, req: ShareRequest) -> ShareResponse:
        rec = req.recommendation
        self.ledger.emit("rec.proposed", {"rec": rec.to_dict()})
        # Структурная валидация уже прошла в DTO (закрытые словари). VALIDATED здесь
        # означает «корректно оформлено и безопасно по форме», а НЕ «верно по существу».
        self.ledger.emit("rec.validated", {"recommendation_id": rec.recommendation_id, "version": rec.version})
        return ShareResponse(rec.recommendation_id, rec.version, RecState.VALIDATED.value)

    def revoke(self, recommendation_id: str, reason: str = "revoked") -> None:
        """Операторская функция Commons (не входит в порт агентов)."""
        found = False
        for (rid, ver), r in list(self.ledger.recs.items()):
            if rid == recommendation_id and r.state is RecState.VALIDATED:
                self.ledger.emit("rec.revoked", {"recommendation_id": rid, "version": ver, "reason": reason})
                found = True
        if not found:
            raise UnknownEntityError("no validated version to revoke")

    # ---- Ask -----------------------------------------------------------
    def _current(self, rid: str):
        """Актуальная версия = наибольшая VALIDATED; если она отозвана/нет — None."""
        best = None
        for (r, v), rec in self.ledger.recs.items():
            if r == rid and rec.state is RecState.VALIDATED and (best is None or v > best.rec.version):
                best = rec
        return best

    def _ask(self, req: AskRequest) -> AskResponse:
        sig = context_signature(req.context)
        out: list[Candidate] = []
        for rid in sorted({r for (r, _v) in self.ledger.recs}):
            cur = self._current(rid)
            if cur is None or cur.rec.problem_type != req.problem_type:
                continue
            if self.apply_applicability_filter and not matches(cur.rec.applicability, req.context):
                continue
            sc, nc, na = score(self.ledger.stats.get((rid, cur.rec.version)), sig, self.scoring_mode)
            out.append(Candidate(cur.rec, sc, nc, na))
        out.sort(key=lambda c: (-round(c.score, 9), -c.recommendation.confidence, c.recommendation.recommendation_id))
        return AskResponse(tuple(out[: req.limit]))

    # ---- Validate ------------------------------------------------------
    def _validate(self, req: ValidateRequest) -> ValidateResponse:
        rec_rec = self.ledger.recs.get((req.recommendation_id, req.version))
        if rec_rec is None:
            raise UnknownEntityError("recommendation")
        self.ledger.emit("app.proposed", {
            "application_id": req.application_id, "recommendation_id": req.recommendation_id,
            "version": req.version, "agent_id": req.agent_id,
            "context": dict(req.context), "case_ref": req.case_ref})
        cur = self._current(req.recommendation_id)
        is_current = cur is not None and cur.rec.version == req.version
        applicable = matches(rec_rec.rec.applicability, req.context)
        reasons = []
        if rec_rec.state is RecState.REVOKED or cur is None:
            reasons.append("revoked")
        elif not is_current:
            reasons.append("stale_version")
        if not applicable:
            reasons.append("not_applicable")
        ok = not reasons
        if ok:
            self.ledger.emit("app.validated", {"application_id": req.application_id,
                                               "validation": {"applicable": True, "is_current_version": True}})
        else:
            reason = reasons[0] if reasons[0] in ("revoked", "stale_version") else "not_applicable"
            self.ledger.emit("app.rejected", {"application_id": req.application_id, "reason": reason, "by": "commons"})
        return ValidateResponse(req.application_id, ok, applicable, is_current, tuple(reasons))

    # ---- Feedback ------------------------------------------------------
    def _feedback(self, req: FeedbackRequest) -> FeedbackResponse:
        a = self.ledger.apps.get(req.application_id)
        if a is None:
            raise UnknownEntityError("application")
        if a.agent_id != req.agent_id:
            raise ForbiddenError("feedback only from the applying agent")
        if req.stage is FeedbackStage.APPLIED:
            self.ledger.emit("app.applied", {"application_id": a.application_id})
        elif req.stage is FeedbackStage.DENIED_BY_LOCAL_POLICY:
            self.ledger.emit("app.rejected", {"application_id": a.application_id,
                                              "reason": "denied_by_local_policy", "by": a.agent_id})
        if req.self_reported_outcome is not None:
            self.ledger.emit("app.self_report", {"application_id": a.application_id,
                                                 "outcome": req.self_reported_outcome})
        return FeedbackResponse(a.application_id, True, a.state.value)


class AgentPort:
    """Ровно 4 операции. Нет ни revoke, ни записи оценок."""

    __slots__ = ("_svc",)

    def __init__(self, svc: CommonsService) -> None:
        self._svc = svc

    def ask(self, req: AskRequest) -> AskResponse:
        return self._svc._ask(req)

    def share(self, req: ShareRequest) -> ShareResponse:
        return self._svc._share(req)

    def validate(self, req: ValidateRequest) -> ValidateResponse:
        return self._svc._validate(req)

    def feedback(self, req: FeedbackRequest) -> FeedbackResponse:
        return self._svc._feedback(req)


class Sink:
    """Способность независимого Evaluator."""

    __slots__ = ("_svc",)

    def __init__(self, svc: CommonsService) -> None:
        self._svc = svc

    def pending(self) -> Sequence[ApplicationEvidence]:
        out = []
        for a in self._svc.ledger.apps.values():
            needs = (a.state is AppState.APPLIED or
                     (a.state is AppState.REJECTED and a.reject_reason == "denied_by_local_policy"
                      and a.rejection_verdict is None))
            if not needs:
                continue
            rec = self._svc.ledger.recs[(a.recommendation_id, a.version)].rec
            out.append(ApplicationEvidence(
                a.application_id, a.recommendation_id, a.version, rec.proposed_action,
                dict(rec.applicability), dict(a.context), a.case_ref, a.state.value, a.reject_reason))
        return out

    def record_observation(self, application_id: str, observation: dict) -> None:
        self._svc.ledger.emit("app.observed", {"application_id": application_id, "observation": observation})

    def record_evaluation(self, application_id: str, verdict: Verdict, quality: int, evaluator_id: str) -> None:
        self._svc.ledger.emit("app.evaluated", {"application_id": application_id, "verdict": verdict.value,
                                                "quality": quality, "evaluator_id": evaluator_id})

    def assess_rejection(self, application_id: str, verdict: Verdict) -> None:
        self._svc.ledger.emit("app.rejection_assessed", {"application_id": application_id, "verdict": verdict.value})
