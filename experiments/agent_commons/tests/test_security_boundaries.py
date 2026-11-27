import ast
import re
from pathlib import Path

from agent_commons.agents import AgentConfig, KnowledgeAgent, SupportAgent
from agent_commons.dto import canonical
from agent_commons.experiment import CountingPort
from agent_commons.policy import LocalPolicy
from agent_commons.service import AgentPort
from conftest import Rig, delivery, make_rec

SRC = Path(__file__).resolve().parents[1] / "src" / "agent_commons"


class Recorder:
    def __init__(self, inner): self.inner, self.wire = inner, []
    def _rec(self, name, fn, req):
        self.wire.append(canonical(req.to_dict())); resp = fn(req); self.wire.append(canonical(resp.to_dict())); return resp
    def ask(self, r): return self._rec("ask", self.inner.ask, r)
    def share(self, r): return self._rec("share", self.inner.share, r)
    def validate(self, r): return self._rec("validate", self.inner.validate, r)
    def feedback(self, r): return self._rec("feedback", self.inner.feedback, r)


def test_only_whitelisted_data_crosses_the_boundary():  # S2, S3
    t = delivery(1)
    rig = Rig([t], recs=[make_rec(applicability={"order_status": "shipped"})])
    rec = Recorder(rig.svc.port())
    rig.agent._port = rec
    rig.agent.solve(t)
    wire = " ".join(rec.wire)
    assert rec.wire
    for forbidden in ("customer", "email", "api_key", "sk-", "password", "notes", "@"):
        assert forbidden not in wire
    keys = set(re.findall(r'"([a-z_]+)":', wire))
    assert keys <= {"requester_id", "problem_type", "context", "limit", "candidates", "recommendation", "score",
                    "context_trials", "global_trials", "recommendation_id", "version", "proposed_action",
                    "applicability", "confidence", "author_id", "application_id", "agent_id", "case_ref",
                    "ok", "applicable", "is_current_version", "reasons", "stage", "self_reported_outcome",
                    "accepted", "state", "order_status"}


def test_ask_and_validate_responses_do_not_expose_case_ref_or_other_agents_applications():
    t = delivery(1)
    rig = Rig([t], recs=[make_rec(applicability={"order_status": "shipped"})])
    rec = Recorder(rig.svc.port()); rig.agent._port = rec
    rig.agent.solve(t)
    responses = rec.wire[1::2]
    # поле case_ref не возвращается из Commons (application_id — эхо идентификатора, выбранного самим A)
    assert responses and not any('"case_ref"' in r for r in responses)


def test_knowledge_agent_has_no_handle_to_agent_a_state():  # S2
    b = KnowledgeAgent()
    assert not any(isinstance(v, (SupportAgent, LocalPolicy)) for v in vars(b).values())
    assert not hasattr(b, "execute") and not hasattr(b, "solve")


def test_agent_port_exposes_no_evaluation_or_admin_capability():
    names = {n for n in dir(AgentPort) if not n.startswith("_")}
    assert names == {"ask", "share", "validate", "feedback"}
    assert not hasattr(AgentPort, "revoke") and not hasattr(AgentPort, "record_evaluation")


def test_no_code_execution_network_or_monolith_runtime_dependency():  # S4 + изоляция
    banned_calls = {"eval", "exec", "compile", "__import__"}
    banned_modules = {"subprocess", "socket", "http", "urllib", "requests", "os.system", "pickle", "importlib", "ctypes"}
    for f in SRC.glob("*.py"):
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                assert n.func.id not in banned_calls, (f.name, n.func.id)
            if isinstance(n, ast.Import):
                assert not ({a.name.split(".")[0] for a in n.names} & banned_modules), f.name
            if isinstance(n, ast.ImportFrom):
                assert (n.module or "").split(".")[0] not in banned_modules, f.name
        text = f.read_text().lower()
        assert not re.search(r"^\s*(from|import)\s+.*(monolith|supportops)", text, re.M), f.name


def test_recommendation_is_inert_data_never_executed():
    # «Вредная» метка — лишь enum; Commons сам по себе ничего не исполняет
    rig = Rig([delivery(1)], recs=KnowledgeAgent().corpus())
    assert rig.world.oracle().case_state("case_0001").executions == ()


def test_no_production_supportops_import():
    import sys
    assert not any(m == "supportops_agent" or m.startswith("supportops_agent.") for m in sys.modules)
