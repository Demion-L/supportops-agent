import json
import pytest

from agent_commons.errors import LogCorruptError
from agent_commons.eventlog import EventLog
from agent_commons.experiment import ARMS, run_arm
from agent_commons.service import CommonsService
from agent_commons.simulator import generate_tasks
from conftest import Rig


def stats_of(rig):
    return {k: v for k, v in rig.svc.ledger.stats.items()}


def test_state_recovered_after_restart(tmp_path):
    p = tmp_path / "log.jsonl"
    tasks = generate_tasks(3, 40)
    rig = Rig(tasks, log_path=p)
    for t in tasks[:20]:
        rig.run(t)
    before = (stats_of(rig), {k: v.state for k, v in rig.svc.ledger.recs.items()},
              {k: (v.state, v.verdict) for k, v in rig.svc.ledger.apps.items()})
    svc2 = CommonsService(EventLog(p))  # «перезапуск симулятора»
    after = (dict(svc2.ledger.stats), {k: v.state for k, v in svc2.ledger.recs.items()},
             {k: (v.state, v.verdict) for k, v in svc2.ledger.apps.items()})
    assert before == after


def test_run_continued_after_restart_equals_uninterrupted(tmp_path):
    tasks = generate_tasks(5, 40)
    ref = Rig(tasks)
    for t in tasks:
        ref.run(t)
    rig = Rig(tasks, log_path=tmp_path / "l.jsonl")
    for t in tasks[:17]:
        rig.run(t)
    # рестарт Commons: пересоздаём сервис и всё, что на нём держится; world (состояние мира) сохраняется
    rig.svc = CommonsService(EventLog(tmp_path / "l.jsonl"))
    rig.agent._port = rig.svc.port()
    from agent_commons.evaluator import IndependentEvaluator
    rig.evaluator = IndependentEvaluator(rig.svc.evaluation_sink(), rig.world.oracle())
    for t in tasks[17:]:
        rig.run(t)
    assert stats_of(rig) == stats_of(ref)
    assert len(rig.svc.log_events if hasattr(rig.svc, "log_events") else rig.svc.ledger.log.events) == len(ref.svc.ledger.log.events)


def test_torn_tail_is_recovered_and_midfile_corruption_detected(tmp_path):
    p = tmp_path / "l.jsonl"
    rig = Rig(generate_tasks(1, 5), log_path=p)
    n = len(rig.svc.ledger.log.events)
    with open(p, "ab") as f:
        f.write(b'{"seq": 99, "type": "rec.prop')  # оборванная запись
    log = EventLog(p)
    assert log.recovered_torn_tail and len(log.events) == n
    lines = p.read_text().splitlines()
    d = json.loads(lines[3]); d["data"] = {"tampered": True}
    lines[3] = json.dumps(d)
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(LogCorruptError):
        EventLog(p)


def test_semantically_invalid_log_is_rejected_on_replay(tmp_path):
    log = EventLog(tmp_path / "l.jsonl")
    log.append("app.evaluated", {"application_id": "app_x", "verdict": "SOLVED", "quality": 1})  # без предшествующих событий
    with pytest.raises(LogCorruptError):
        CommonsService(EventLog(tmp_path / "l.jsonl"))


def test_same_seed_is_reproducible_and_same_stream_across_arms():
    a = run_arm(ARMS["commons"], 7, 60); b = run_arm(ARMS["commons"], 7, 60)
    assert a == b
    assert generate_tasks(7, 60) == generate_tasks(7, 60)
    assert generate_tasks(7, 60) != generate_tasks(8, 60)
