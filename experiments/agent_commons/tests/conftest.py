import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import pytest  # noqa: E402

from agent_commons.agents import AgentConfig, KnowledgeAgent, SupportAgent  # noqa: E402
from agent_commons.dto import Recommendation  # noqa: E402
from agent_commons.evaluator import IndependentEvaluator  # noqa: E402
from agent_commons.eventlog import EventLog  # noqa: E402
from agent_commons.policy import LocalPolicy  # noqa: E402
from agent_commons.service import CommonsService  # noqa: E402
from agent_commons.simulator import World  # noqa: E402
from agent_commons.tasks import Task  # noqa: E402
from agent_commons.vocab import ActionId, ProblemType  # noqa: E402


def make_rec(rid="rec_t1", version=1, pt=ProblemType.DELIVERY_DELAY, action=ActionId.REQUEST_CARRIER_STATUS,
             applicability=None, confidence=0.5, author="agent_b"):
    return Recommendation(rid, version, pt, action, applicability or {}, confidence, author)


def delivery(i, status="shipped"):
    return Task(f"case_{i:04d}", ProblemType.DELIVERY_DELAY, {"order_status": status})


class Rig:
    """Мини-стенд: world + commons + A + Evaluator."""

    def __init__(self, tasks, *, log_path=None, policy=None, publish=True, recs=None, svc_kwargs=None,
                 config=None, evaluator=True):
        self.world = World(tasks)
        self.svc = CommonsService(EventLog(log_path), **(svc_kwargs or {}))
        if publish:
            if recs is None:
                KnowledgeAgent().publish(self.svc.port())
            else:
                from agent_commons.dto import ShareRequest
                for r in recs:
                    self.svc.port().share(ShareRequest(r))
        self.policy = policy or LocalPolicy()
        self.agent = SupportAgent("agent_a", config or AgentConfig("t"), self.svc.port(), self.policy, self.world.execute)
        self.evaluator = IndependentEvaluator(self.svc.evaluation_sink(), self.world.oracle()) if evaluator else None

    def run(self, task):
        tr = self.agent.solve(task)
        if self.evaluator:
            self.evaluator.process_pending()
        return tr


@pytest.fixture
def rig_factory():
    return Rig
