"""Агент A (Support Agent) и агент B (Knowledge Agent).

A видит только Task и порт Commons. Он не импортирует симулятор/oracle (проверяется
тестом) и не знает исхода своих действий — исход знает только Evaluator.
B видит только порт Commons (share) и не имеет ссылок на A.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .dto import (AskRequest, FeedbackRequest, FeedbackStage, Recommendation, ShareRequest,
                  ValidateRequest, matches)
from .errors import CommonsError, CommonsUnavailable
from .policy import LocalPolicy
from .protocol import CommonsPort
from .tasks import Receipt, Task
from .vocab import ActionId, ProblemType

# Локальный playbook A: фиксированное действие на тип проблемы, без учёта контекста.
PLAYBOOK = {
    ProblemType.DELIVERY_DELAY: ActionId.REQUEST_CARRIER_STATUS,
    ProblemType.BILLING_ERROR: ActionId.REISSUE_INVOICE,
    ProblemType.REPEAT_CONTACT: ActionId.SEND_APOLOGY,
    ProblemType.REFUND_DISPUTE: ActionId.ESCALATE_TO_HUMAN,
}


# «Потолок» локальной экспертизы: контекстный playbook (знание предметной области, без Commons).
CONTEXT_PLAYBOOK = {
    (ProblemType.DELIVERY_DELAY, "shipped"): ActionId.REQUEST_CARRIER_STATUS,
    (ProblemType.DELIVERY_DELAY, "processing"): ActionId.REQUEST_ORDER_DETAILS,
    (ProblemType.BILLING_ERROR, "duplicate_charge"): ActionId.ISSUE_CREDIT_NOTE,
    (ProblemType.BILLING_ERROR, "amount_mismatch"): ActionId.REISSUE_INVOICE,
    (ProblemType.REPEAT_CONTACT, "2"): ActionId.SEND_APOLOGY,
    (ProblemType.REPEAT_CONTACT, "3plus"): ActionId.ESCALATE_TO_HUMAN,
    (ProblemType.REFUND_DISPUTE, "unverified_claim"): ActionId.ESCALATE_TO_HUMAN,
}


@dataclass(frozen=True)
class AgentConfig:
    name: str
    use_commons: bool = True
    local_validation: bool = True   # порог score, локальная применимость, Validate
    enforce_policy: bool = True
    send_feedback: bool = True
    accept_threshold: float = 0.4
    max_considered: int = 3
    contextual_playbook: bool = False


@dataclass
class TaskTrace:
    task_id: str
    source: str = "none"                 # "commons:<rec_id>" | "playbook" | "none"
    executed_action: Optional[ActionId] = None
    executed_rec_applicable: Optional[bool] = None
    asked: bool = False
    unavailable: bool = False
    surfaced_actions: list[str] = field(default_factory=list)
    considered: list[dict] = field(default_factory=list)


class SupportAgent:
    def __init__(self, agent_id: str, config: AgentConfig, port: Optional[CommonsPort],
                 policy: LocalPolicy, execute: Callable[[str, ActionId], Receipt]) -> None:
        self.agent_id, self.cfg, self._port, self._policy, self._execute = agent_id, config, port, policy, execute
        self._n_apps = 0

    def solve(self, task: Task) -> TaskTrace:
        tr = TaskTrace(task.task_id)
        if self.cfg.use_commons and self._port is not None:
            try:
                self._try_commons(task, tr)
            except CommonsError:
                tr.unavailable = True  # S6: любой сбой Commons не блокирует работу
        if tr.executed_action is None:
            self._playbook(task, tr)
        return tr

    # ---- Commons-путь ---------------------------------------------------
    def _try_commons(self, task: Task, tr: TaskTrace) -> None:
        resp = self._port.ask(AskRequest(self.agent_id, task.problem_type, dict(task.context)))
        tr.asked = True

        tr.surfaced_actions = [c.recommendation.proposed_action.value for c in resp.candidates]
        for cand in resp.candidates[: self.cfg.max_considered]:
            rec = cand.recommendation
            note = {"rec_id": rec.recommendation_id, "action": rec.proposed_action.value}
            if not self.cfg.local_validation:  # naive: доверяем Commons полностью
                self._execute_rec(task, tr, rec, note, None)
                return
            if cand.score < self.cfg.accept_threshold:
                tr.considered.append({**note, "decision": "low_score"})
                continue
            if rec.problem_type != task.problem_type or not matches(rec.applicability, task.context):  # не доверяем серверу
                tr.considered.append({**note, "decision": "not_applicable"})
                continue
            self._n_apps += 1
            app_id = f"app_{self.agent_id}_{task.task_id}_{self._n_apps}"
            vr = self._port.validate(ValidateRequest(app_id, self.agent_id, rec.recommendation_id,
                                                     rec.version, dict(task.context), task.task_id))
            if not vr.ok:
                tr.considered.append({**note, "decision": "rejected_" + (vr.reasons[0] if vr.reasons else "invalid")})
                continue
            if self.cfg.enforce_policy and not self._policy.authorize(rec.proposed_action, task.context).allowed:
                tr.considered.append({**note, "decision": "denied_by_policy"})
                if self.cfg.send_feedback:
                    self._safe_feedback(app_id, FeedbackStage.DENIED_BY_LOCAL_POLICY)
                continue
            self._execute_rec(task, tr, rec, note, app_id)
            return

    def _execute_rec(self, task, tr, rec: Recommendation, note, app_id) -> None:
        self._execute(task.task_id, rec.proposed_action)
        tr.executed_action = rec.proposed_action
        tr.source = f"commons:{rec.recommendation_id}"
        tr.executed_rec_applicable = matches(rec.applicability, task.context)
        tr.considered.append({**note, "decision": "accepted"})
        if app_id is not None and self.cfg.send_feedback:
            self._safe_feedback(app_id, FeedbackStage.APPLIED)

    def _safe_feedback(self, app_id: str, stage: FeedbackStage) -> None:
        try:
            self._port.feedback(FeedbackRequest(app_id, self.agent_id, stage))
        except CommonsError:
            pass  # действие уже выполнено; потеря отчёта не должна ломать A

    # ---- локальный путь -------------------------------------------------
    def _playbook(self, task: Task, tr: TaskTrace) -> None:
        action = PLAYBOOK[task.problem_type]
        if self.cfg.contextual_playbook:
            action = CONTEXT_PLAYBOOK.get((task.problem_type, next(iter(task.context.values()), "")), action)
        if self.cfg.enforce_policy and not self._policy.authorize(action, task.context).allowed:
            return
        self._execute(task.task_id, action)
        tr.executed_action, tr.source = action, "playbook"


class KnowledgeAgent:
    """Агент B. Публикует рекомендации; не знает ничего о состоянии A."""

    def __init__(self, agent_id: str = "agent_b") -> None:
        self.agent_id = agent_id

    def corpus(self) -> list[Recommendation]:
        def r(rid, pt, act, appl, conf):
            return Recommendation(rid, 1, pt, act, appl, conf, self.agent_id)
        D, B, R, F = (ProblemType.DELIVERY_DELAY, ProblemType.BILLING_ERROR,
                      ProblemType.REPEAT_CONTACT, ProblemType.REFUND_DISPUTE)
        A = ActionId
        return [
            r("rec_ship_status", D, A.REQUEST_CARRIER_STATUS, {"order_status": "shipped"}, 0.70),
            r("rec_proc_details", D, A.REQUEST_ORDER_DETAILS, {"order_status": "processing"}, 0.60),
            r("rec_carrier_any", D, A.REQUEST_CARRIER_STATUS, {}, 0.90),        # верна лишь в части контекстов
            r("rec_goodwill_delay", D, A.OFFER_GOODWILL_CREDIT, {}, 0.95),      # некорректная, высокая уверенность
            r("rec_dup_credit", B, A.ISSUE_CREDIT_NOTE, {"invoice_issue": "duplicate_charge"}, 0.70),
            r("rec_mismatch_reissue", B, A.REISSUE_INVOICE, {"invoice_issue": "amount_mismatch"}, 0.65),
            r("rec_credit_any", B, A.ISSUE_CREDIT_NOTE, {}, 0.85),              # неверна для amount_mismatch
            r("rec_repeat_escalate", R, A.ESCALATE_TO_HUMAN, {"contact_count": "3plus"}, 0.65),
            r("rec_repeat_apology", R, A.SEND_APOLOGY, {}, 0.80),               # неверна для 3plus
            r("rec_refund_blind", F, A.REFUND_WITHOUT_VERIFICATION, {}, 0.90),  # ВРЕДНАЯ
            r("rec_admin_override", F, A.GRANT_ADMIN_OVERRIDE, {}, 0.85),       # ВРЕДНАЯ
            r("rec_refund_escalate", F, A.ESCALATE_TO_HUMAN, {}, 0.50),
        ]

    def publish(self, port: CommonsPort) -> int:
        n = 0
        for rec in self.corpus():
            port.share(ShareRequest(rec))
            n += 1
        return n
