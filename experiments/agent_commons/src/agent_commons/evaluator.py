"""Агент C: независимый Evaluator.

Оценивает фактический исход по состоянию мира (OracleView), а не по тексту и не по
самоотчётам. Намеренно НЕ импортирует agents/service: знает только protocol, dto и
read-only oracle. Заявленные агентом факты (контекст, применённое действие) он
перепроверяет по миру; расхождение => UNVERIFIED (в статистику не попадает).
"""
from __future__ import annotations

from .dto import ApplicationEvidence, Verdict, context_signature, matches
from .protocol import EvaluationSink
from .simulator import OracleView

_QUALITY = {Verdict.SOLVED: 1, Verdict.NOT_SOLVED: 0, Verdict.HARMFUL: -1}


class IndependentEvaluator:
    def __init__(self, sink: EvaluationSink, oracle: OracleView, evaluator_id: str = "evaluator_c") -> None:
        self._sink, self._oracle, self.evaluator_id = sink, oracle, evaluator_id
        self.processed = 0

    def process_pending(self) -> int:
        n = 0
        for ev in list(self._sink.pending()):
            if ev.state == "APPLIED":
                self._evaluate_applied(ev)
            else:
                self._sink.assess_rejection(ev.application_id, self._assess_rejection(ev))
            n += 1
        self.processed += n
        return n

    def _state(self, ev: ApplicationEvidence):
        try:
            return self._oracle.case_state(ev.case_ref)
        except KeyError:
            return None

    def _evaluate_applied(self, ev: ApplicationEvidence) -> None:
        st = self._state(ev)
        obs = {"known_case": st is not None}
        if st is not None:
            obs.update({"executions": [(e.action.value, e.effect) for e in st.executions],
                        "context_sig": context_signature(st.context)})
        self._sink.record_observation(ev.application_id, obs)
        verdict = self._verdict_applied(ev, st)
        self._sink.record_evaluation(ev.application_id, verdict, _QUALITY.get(verdict, 0), self.evaluator_id)

    def _verdict_applied(self, ev, st) -> Verdict:
        if st is None or dict(st.context) != dict(ev.claimed_context):
            return Verdict.UNVERIFIED  # агент неверно указал контекст/кейс
        mine = [e for e in st.executions if e.action == ev.proposed_action]
        if not mine:
            return Verdict.UNVERIFIED  # заявленное применение не подтверждено миром
        if not matches(ev.rec_applicability, st.context):
            return Verdict.NOT_APPLICABLE  # не вина рекомендации
        effect = mine[0].effect
        return Verdict.HARMFUL if effect == "damage" else Verdict.SOLVED if effect == "resolved" else Verdict.NOT_SOLVED

    def _assess_rejection(self, ev: ApplicationEvidence) -> Verdict:
        st = self._state(ev)
        if st is None or dict(st.context) != dict(ev.claimed_context):
            return Verdict.UNVERIFIED
        if any(e.action == ev.proposed_action for e in st.executions):
            return Verdict.UNVERIFIED  # заявил отказ, но действие исполнено
        return Verdict.BLOCKED_BY_POLICY if self._oracle.is_forbidden(ev.proposed_action) else Verdict.UNVERIFIED
