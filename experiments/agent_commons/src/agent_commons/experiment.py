"""Runner эксперимента AC-EXP-001: режимы (arms), метрики, агрегирование.

Метрики исхода берутся из состояния мира (OracleView), а не из ledger и не из
отчётов агентов. Детерминированно: без сети, времени и случайности вне seed.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

from .agents import AgentConfig, KnowledgeAgent, SupportAgent, TaskTrace
from .dto import (AskRequest, AskResponse, FeedbackRequest, FeedbackResponse, ShareRequest,
                  ShareResponse, ValidateRequest, ValidateResponse, canonical, matches)
from .errors import CommonsUnavailable
from .evaluator import IndependentEvaluator
from .eventlog import EventLog
from .policy import LocalPolicy
from .protocol import CommonsPort
from .service import CommonsService
from .simulator import HARMFUL_ACTIONS, World, generate_tasks
from .vocab import ActionId

COST_UNITS = {"ask": 4, "validate": 2, "feedback": 1, "share": 2}
EVAL_COST = 3  # observe + evaluate


class OutagePort:
    """Commons недоступен (S6)."""

    def _down(self, *_a, **_k):
        raise CommonsUnavailable("commons down")

    ask = share = validate = feedback = _down


class CountingPort:
    """Считает обращения, байты и детерминированную стоимость."""

    def __init__(self, inner: CommonsPort) -> None:
        self._inner = inner
        self.calls = {k: 0 for k in COST_UNITS}
        self.bytes = 0

    def _wrap(self, op: str, req, fn):
        self.calls[op] += 1
        self.bytes += len(canonical(req.to_dict()))
        resp = fn(req)
        self.bytes += len(canonical(resp.to_dict()))
        return resp

    def ask(self, req: AskRequest) -> AskResponse:
        return self._wrap("ask", req, self._inner.ask)

    def share(self, req: ShareRequest) -> ShareResponse:
        return self._wrap("share", req, self._inner.share)

    def validate(self, req: ValidateRequest) -> ValidateResponse:
        return self._wrap("validate", req, self._inner.validate)

    def feedback(self, req: FeedbackRequest) -> FeedbackResponse:
        return self._wrap("feedback", req, self._inner.feedback)

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    @property
    def cost(self) -> int:
        return sum(COST_UNITS[k] * v for k, v in self.calls.items())


@dataclass(frozen=True)
class Arm:
    name: str
    description: str
    agent: AgentConfig
    evaluator: bool = False
    outage: bool = False
    scoring_mode: str = "context"
    faulty_filter: bool = False
    policy_gap: bool = False   # локальная политика ничего не запрещает (проверка 2-й линии защиты)
    n_agents: int = 1
    shared: bool = True        # n_agents>1: общий Commons (передача опыта) или приватные


ARMS: dict[str, Arm] = {a.name: a for a in [
    Arm("baseline", "A без Commons: локальный playbook + локальная политика",
        AgentConfig("baseline", use_commons=False)),
    Arm("commons", "A + Commons + независимый Evaluator, контекстная оценка",
        AgentConfig("commons"), evaluator=True),
    Arm("commons_no_evaluator", "Commons без Evaluator: ранжирование только по confidence (ablation Q2)",
        AgentConfig("commons_no_evaluator"), evaluator=False),
    Arm("commons_global_score", "Commons + Evaluator, но оценка глобальная без контекста (ablation S9)",
        AgentConfig("commons_global_score"), evaluator=True, scoring_mode="global"),
    Arm("commons_outage", "Commons полностью недоступен (S6)",
        AgentConfig("commons_outage"), outage=True),
    Arm("commons_faulty_filter", "Сервер Commons не фильтрует по применимости; A проверяет локально",
        AgentConfig("commons_faulty_filter"), evaluator=True, faulty_filter=True),
    Arm("local_expert_playbook", "ПОТОЛОК: локальный контекстный playbook (экспертное знание без Commons)",
        AgentConfig("local_expert_playbook", use_commons=False, contextual_playbook=True)),
    Arm("commons_policy_gap", "Commons + Evaluator, но локальная политика пуста: вред ловится только обучением",
        AgentConfig("commons_policy_gap"), evaluator=True, policy_gap=True),
    Arm("commons_2agents_shared", "2 агента A делят один Commons (передача опыта между агентами)",
        AgentConfig("commons_2agents_shared"), evaluator=True, n_agents=2, shared=True),
    Arm("commons_2agents_private", "2 агента A, у каждого свой приватный Commons (нет передачи опыта)",
        AgentConfig("commons_2agents_private"), evaluator=True, n_agents=2, shared=False),
    Arm("naive_trust", "ДЕМО УГРОЗ: A слепо доверяет топ-рекомендации (без валидации/политики/оценки)",
        AgentConfig("naive_trust", local_validation=False, enforce_policy=False, send_feedback=False),
        faulty_filter=True),
]}


def run_arm(arm: Arm, seed: int, n_tasks: int = 100, log_path: Optional[Path] = None) -> dict[str, Any]:
    tasks = generate_tasks(seed, n_tasks)
    world = World(tasks)
    oracle = world.oracle()
    policy = LocalPolicy(denied=frozenset()) if arm.policy_gap else LocalPolicy()

    def new_unit(agent_id: str, svc=None):
        svc = svc or CommonsService(EventLog(log_path), scoring_mode=arm.scoring_mode,
                                    apply_applicability_filter=not arm.faulty_filter)
        if arm.agent.use_commons and not arm.outage and svc.ledger.recs == {}:
            KnowledgeAgent().publish(svc.port())  # публикация B вне агентского счётчика
        port = CountingPort(OutagePort() if arm.outage else svc.port())
        agent = SupportAgent(agent_id, arm.agent, port if arm.agent.use_commons else None, policy, world.execute)
        ev = IndependentEvaluator(svc.evaluation_sink(), oracle) if arm.evaluator else None
        return svc, port, agent, ev

    ids = ["agent_a", "agent_a2", "agent_a3"][: arm.n_agents]
    units = []
    for i, aid in enumerate(ids):
        # shared: все агенты на одном Commons; private: у каждого свой Commons и свой Evaluator
        units.append(new_unit(aid, units[0][0] if (arm.shared and i > 0) else None))
    traces: list[TaskTrace] = []
    for i, t in enumerate(tasks):
        svc, port, agent, ev = units[i % len(units)]
        traces.append(agent.solve(t))
        for u in {id(u[0]): u for u in units}.values():  # Evaluator каждого уникального Commons
            if u[3] is not None:
                u[3].process_pending()

    n = len(tasks)
    resolved = damaged = executed = wrong = invalid = 0
    flags: list[int] = []
    considered = accepted = 0
    hv = {h.value for h in HARMFUL_ACTIONS}
    harmful_surfaced = harmful_considered = harmful_considered_executed = 0
    sources: dict[str, int] = {}
    for t, tr in zip(tasks, traces):
        st = oracle.case_state(t.task_id)
        resolved += st.resolved
        flags.append(int(st.resolved))
        damaged += st.damaged
        if tr.executed_action is not None:
            executed += 1
            wrong += not st.resolved
            invalid += st.damaged or tr.executed_rec_applicable is False
        considered += len(tr.considered)
        accepted += sum(c["decision"] == "accepted" for c in tr.considered)
        harmful_surfaced += sum(a in hv for a in tr.surfaced_actions)
        for c in tr.considered:
            if c["action"] in hv:
                harmful_considered += 1
                harmful_considered_executed += c["decision"] == "accepted"
        sources[tr.source] = sources.get(tr.source, 0) + 1
    uniq = list({id(u[0]): u for u in units}.values())
    calls = sum(u[1].total_calls for u in units)
    cost = sum(u[1].cost for u in units)
    nbytes = sum(u[1].bytes for u in units)
    evals = sum(u[3].processed for u in uniq if u[3] is not None)
    q = [(i * n // 4, (i + 1) * n // 4) for i in range(4)]
    stats = {}
    for u in uniq:
        for k, v in u[0].ledger.stats.items():
            stats.setdefault(f"{k[0]}@{k[1]}", v)
    return {
        "arm": arm.name, "seed": seed, "n_tasks": n,
        "task_success_rate": resolved / n if n else 0.0,
        "wrong_action_rate": wrong / n if n else 0.0,
        "invalid_action_rate": invalid / n if n else 0.0,
        "harmful_executed_rate": damaged / n if n else 0.0,
        "outcome_quality": (resolved - damaged) / n if n else 0.0,
        "success_by_quarter": [(sum(flags[a:b]) / (b - a)) if b > a else None for a, b in q],
        "recommendation_acceptance_rate": (accepted / considered) if considered else None,
        "candidates_considered": considered,
        "harmful_surfaced": harmful_surfaced,
        "harmful_considered": harmful_considered,
        # доля РАССМОТРЕННЫХ вредных кандидатов, которые не были исполнены
        "harmful_rejection_rate": (1 - harmful_considered_executed / harmful_considered) if harmful_considered else None,
        "commons_calls": calls,
        "commons_calls_by_op": {k: sum(u[1].calls[k] for u in units) for k in COST_UNITS},
        "commons_cost_units": cost + EVAL_COST * evals,
        "commons_bytes": nbytes,
        "evaluations": evals,
        "unavailable_tasks": sum(tr.unavailable for tr in traces),
        "sources": sources,
        "ledger_stats": stats,
    }


_METRICS = ["task_success_rate", "wrong_action_rate", "invalid_action_rate", "harmful_executed_rate",
            "outcome_quality", "recommendation_acceptance_rate", "harmful_rejection_rate",
            "commons_calls", "commons_cost_units", "commons_bytes"]


def aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in _METRICS:
        vals = [r[m] for r in runs if r[m] is not None]
        out[m] = ({"mean": statistics.fmean(vals), "min": min(vals), "max": max(vals),
                   "stdev": statistics.pstdev(vals)} if vals else None)
    return out


def run_all(seeds: list[int], n_tasks: int = 100, arms: Optional[list[str]] = None) -> dict[str, Any]:
    result: dict[str, Any] = {"seeds": seeds, "n_tasks": n_tasks, "arms": {}}
    for name in arms or list(ARMS):
        runs = [run_arm(ARMS[name], s, n_tasks) for s in seeds]
        result["arms"][name] = {"description": ARMS[name].description, "aggregate": aggregate(runs), "runs": runs}
    return result


def paired_delta(result: dict[str, Any], arm: str, base: str = "baseline", metric: str = "task_success_rate") -> dict[str, float]:
    a = [r[metric] for r in result["arms"][arm]["runs"]]
    b = [r[metric] for r in result["arms"][base]["runs"]]
    d = [x - y for x, y in zip(a, b)]
    return {"mean_delta": statistics.fmean(d), "min_delta": min(d), "max_delta": max(d),
            "seeds_improved": sum(x > 0 for x in d), "seeds_worse": sum(x < 0 for x in d), "n": len(d)}
