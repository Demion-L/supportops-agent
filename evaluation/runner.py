"""Запуск сценариев, сравнение с ожидаемым исходом, отчёт.

Изоляция (всё внутри `_isolated`, восстанавливается в finally):
  * `mock_data.CASES` заменяется чистым словарём (побочные эффекты create_support_case);
  * обработчики TOOL_REGISTRY оборачиваются шпионом/инъекцией сбоев;
  * сетевые соединения блокируются и считаются нарушением инварианта.
Реальных LLM, credentials, PostgreSQL и refund-операций harness не использует
(refund-инструментов с побочным эффектом в агенте нет; check_refund_eligibility — read-only).
Запуск последовательный: подмена обработчиков глобальна и не потокобезопасна.

CLI:  python -m evaluation.runner [--dataset PATH] [--output PATH] [--fail-on-mismatch]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from supportops_agent.business import mock_data
from supportops_agent.runtime.agent import SupportAgent
from supportops_agent.tools.authorization import support_agent_context
from supportops_agent.models import AgentRunStatus, AgentStepType, SupportTicket
from supportops_agent.business.refund_evaluation import default_refund_evaluator
from supportops_agent.tools.definitions import TOOL_REGISTRY

from .fakes import FakeClassifier
from .invariants import (
    GLOBAL_CANARIES,
    CallRecord,
    HandlerCall,
    Observation,
    check_invariants,
)
from .metrics import METRIC_DEFINITIONS, MetricResult, compute_metrics
from .recording import load_recordings, recording_filename, save_recording
from .schemas import (
    SCHEMA_VERSION,
    Dataset,
    Mismatch,
    ObservedCall,
    Outcome,
    Scenario,
    ScenarioResult,
)
from .sources import FakeSource, LLMSource, Mode, RecordSource, ReplaySource

DEFAULT_DATASET = Path(__file__).parent / "datasets" / "support_cases.json"
DEFAULT_RECORDINGS = Path(__file__).parent / "recordings"
REDACTED = "[REDACTED]"
_FAULTS = {"RuntimeError": RuntimeError, "ValueError": ValueError}


class NetworkAccessBlocked(Exception):
    """Попытка сетевого соединения внутри сценария."""


class ReportLeakError(RuntimeError):
    """В готовом отчёте найден синтетический секрет (fail-closed: отчёт не пишется)."""


@dataclass
class _Env:
    phase: str = "run"
    handler_calls: list[HandlerCall] = field(default_factory=list)
    call_records: list[CallRecord] = field(default_factory=list)
    network_attempts: int = 0


# --- изоляция ------------------------------------------------------------------
@contextmanager
def _no_network(env: _Env) -> Iterator[None]:
    def blocked(*_a, **_k):
        env.network_attempts += 1
        raise NetworkAccessBlocked("network access is blocked in evaluation")

    saved = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.create_connection,
        socket.getaddrinfo,
    )
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    socket.getaddrinfo = blocked
    try:
        yield
    finally:
        (
            socket.socket.connect,
            socket.socket.connect_ex,
            socket.create_connection,
            socket.getaddrinfo,
        ) = saved


@contextmanager
def _isolated(scenario: Scenario, env: _Env) -> Iterator[None]:
    faults = {f.tool: f for f in scenario.tool_faults}
    originals = {name: d.handler for name, d in TOOL_REGISTRY.items()}
    saved_cases = mock_data.CASES

    def wrap(name, original):
        def handler(**kwargs):
            env.handler_calls.append(HandlerCall(name, dict(kwargs), env.phase))
            fault = faults.get(name)
            if fault is not None:
                raise _FAULTS[fault.exception](f"synthetic tool fault {fault.secret}")
            return original(**kwargs)

        return handler

    mock_data.CASES = {}
    for name, definition in TOOL_REGISTRY.items():
        definition.handler = wrap(name, originals[name])
    try:
        with _no_network(env):
            yield
    finally:
        for name, definition in TOOL_REGISTRY.items():
            definition.handler = originals[name]
        mock_data.CASES = saved_cases


def _spy_agent_calls(agent: SupportAgent, env: _Env) -> None:
    """Запись каждого вызова публичного execute_function_call (метод экземпляра)."""

    real = agent.execute_function_call

    def spy(item):
        before = len(env.handler_calls)
        result = real(item)
        executed = len(env.handler_calls) > before
        env.call_records.append(
            CallRecord(
                name=item.name,
                phase=env.phase,
                executed=executed,
                kwargs=dict(env.handler_calls[-1].kwargs) if executed else None,
                error_type=result.error_type,
                reason_code=result.reason_code,
                output=result.output,
            )
        )
        return result

    agent.execute_function_call = spy


def _probe(agent: SupportAgent, probe) -> dict | None:
    item = SimpleNamespace(
        call_id="probe_call", name=probe.tool, arguments=json.dumps(probe.arguments)
    )
    return agent.execute_function_call(item).output


# --- наблюдение и сравнение ----------------------------------------------------
def _observed_classification(run) -> dict | None:
    for step in run.steps:
        if step.step_type == AgentStepType.CLASSIFICATION and step.error is None:
            out = step.output or {}
            return {
                k: out.get(k)
                for k in ("primary_intent", "intents", "requires_clarification", "exception_requested")
            }
    return None


def _derive_outcome(run, exception_type: str | None) -> Outcome:
    if exception_type is not None or run is None or run.status != AgentRunStatus.COMPLETED:
        return Outcome.FAILED
    return Outcome.DEGRADED if run.summary()["degraded"] else Outcome.COMPLETED


def _compare(
    scenario: Scenario,
    result: ScenarioResult,
    answer: str | None,
    probe_outputs: list[dict | None],
    run_args: list[dict | None],
    expected_llm_calls: int,
) -> None:
    exp = scenario.expected

    def miss(dim: str, expected: object, observed: object) -> None:
        result.mismatches.append(
            Mismatch(dimension=dim, expected=str(expected), observed=str(observed))
        )

    # Число LLM-вызовов обязано совпасть со скриптом: лишний вызов (напр. LLM после
    # fail-closed классификации) или пропущенный — поведение агента, а не ошибка harness.
    llm_ok = result.llm_calls == expected_llm_calls
    if not llm_ok:
        miss("llm_calls", expected_llm_calls, result.llm_calls)

    # Outcome + проверки результата -> Task Success.
    if exp.outcome is not None:
        ok = llm_ok
        if result.outcome != exp.outcome:
            miss("outcome", exp.outcome.value, result.outcome.value)
            ok = False
        for needle in exp.answer_contains:
            if needle not in (answer or ""):
                miss("answer_contains", needle, "absent")
                ok = False
        for needle in exp.answer_not_contains:
            if needle in (answer or ""):
                miss("answer_not_contains", needle, "present")
                ok = False
        if exp.cases_created is not None and result.cases_created != exp.cases_created:
            miss("cases_created", exp.cases_created, result.cases_created)
            ok = False
        if exp.failed_tool_calls is not None and result.failed_tool_calls != exp.failed_tool_calls:
            miss("failed_tool_calls", exp.failed_tool_calls, result.failed_tool_calls)
            ok = False
        if exp.exception_type is not None and result.exception_type != exp.exception_type:
            miss("exception_type", exp.exception_type, result.exception_type)
            ok = False
        if exp.probe_outcomes is not None:
            observed = [(o or {}).get("outcome") for o in probe_outputs]
            if observed != exp.probe_outcomes:
                miss("probe_outcomes", exp.probe_outcomes, observed)
                ok = False
        result.task_success = ok

    # Classification: все заданные поля должны совпасть; отсутствие классификации — неверно.
    if exp.classification is not None and exp.classification.specified():
        observed = result.observed_classification
        ok = observed is not None
        if observed is None:
            miss("classification", "specified", "not_produced")
        else:
            for key, want in exp.classification.model_dump().items():
                if want is not None and observed.get(key) != want:
                    miss(f"classification.{key}", want, observed.get(key))
                    ok = False
        result.classification_correct = ok

    # Tools: имена по порядку + forbidden; Argument Validity — позиционно.
    if exp.tools is not None:
        want = exp.tools.calls
        got = result.observed_calls
        names_ok = [c.name for c in want] == [c.name for c in got]
        if not names_ok:
            miss("tool_selection", [c.name for c in want], [c.name for c in got])
        result.tool_selection_correct = names_ok

        matched = 0
        for index in range(max(len(want), len(got))):
            if index >= len(want) or index >= len(got):
                continue
            w, g = want[index], got[index]
            call_ok = (
                w.name == g.name
                and w.validation_status == g.validation_status
                and (w.executed is None or w.executed == g.executed)
                and (w.reason_code is None or w.reason_code == g.reason_code)
            )
            if call_ok and w.arguments is not None:
                call_ok = g.executed and run_args[index] == w.arguments
            if call_ok:
                matched += 1
            else:
                miss(f"tool_call[{index}]", w.model_dump(exclude_none=True), g.model_dump(exclude_none=True))
        result.argument_calls_total = max(len(want), len(got))
        result.argument_calls_matched = matched
        # Нарушения forbidden фиксируются инвариантами; здесь выбор считается неверным.
        executed_forbidden = {
            c.name for c in got if c.executed and c.name in exp.tools.forbidden
        }
        if executed_forbidden:
            miss("forbidden_tool", "never_executed", sorted(executed_forbidden))
            result.tool_selection_correct = False


def run_scenario(scenario: Scenario, source: LLMSource | None = None) -> ScenarioResult:
    """Один сценарий. `source` — fake (по умолчанию), replay или record (sources.py)."""

    source = source or FakeSource()
    result = ScenarioResult(scenario_id=scenario.id, category=scenario.category)
    canaries = (
        *GLOBAL_CANARIES,
        *scenario.secret_canaries,
        *(f.secret for f in scenario.tool_faults),
    )

    try:
        ticket = SupportTicket(**scenario.ticket.model_dump())
    except ValidationError:
        result.harness_error = "invalid_ticket"
        return result

    env = _Env()
    llm = source.open(scenario)
    client = llm.client
    classifier = FakeClassifier(scenario.classifier) if scenario.classifier else None
    exception_type: str | None = None
    answer: str | None = None
    probe_outputs: list[dict | None] = []

    with _isolated(scenario, env):
        agent = SupportAgent(
            client,
            classifier,
            context=support_agent_context() if scenario.trusted_context else None,
            refund_evaluator=default_refund_evaluator() if scenario.refund_evaluation else None,
        )
        _spy_agent_calls(agent, env)

        env.phase = "probe_before"
        for probe in (p for p in scenario.probes if p.when == "before_run"):
            probe_outputs.append(_probe(agent, probe))

        env.phase = "run"
        try:
            answer = agent.run(ticket)
        except Exception as exc:  # noqa: BLE001 - наружу только имя типа, не текст
            exception_type = type(exc).__name__

        env.phase = "probe_after"
        for probe in (p for p in scenario.probes if p.when == "after_run"):
            probe_outputs.append(_probe(agent, probe))

        run = agent.last_run
        cases = dict(mock_data.CASES)
        # Недостающие вызовы replay / финализация записи — внутри изоляции.
        source_error = llm.finish()

    # --- наблюдаемые факты ---
    result.outcome = _derive_outcome(run, exception_type)
    result.exception_type = exception_type
    result.cases_created = len(cases)
    result.llm_calls = client.responses.calls
    result.failed_tool_calls = run.summary()["failed_tool_calls"] if run else 0
    result.observed_classification = _observed_classification(run) if run else None

    run_calls = [c for c in env.call_records if c.phase == "run"]
    trace_statuses = [
        (s.input or {}).get("validation_status")
        for s in (run.steps if run else [])
        if s.step_type == AgentStepType.TOOL_EXECUTION
    ]
    statuses_known = len(trace_statuses) == len(run_calls)
    result.observed_calls = [
        ObservedCall(
            name=c.name,
            validation_status=trace_statuses[i] if statuses_known else None,
            executed=c.executed,
            error_type=c.error_type,
            reason_code=c.reason_code,
        )
        for i, c in enumerate(run_calls)
    ]
    run_args = [
        None
        if c.kwargs is None
        else {k: v for k, v in c.kwargs.items() if not k.startswith("trusted_")}
        for c in run_calls
    ]

    forbidden = frozenset(scenario.expected.tools.forbidden) if scenario.expected.tools else frozenset()
    result.security_violations = check_invariants(
        Observation(
            customer_id=ticket.customer_id,
            ticket_id=ticket.ticket_id,
            allowed_tools=support_agent_context().allowed_tools if scenario.trusted_context else frozenset(),
            forbidden_tools=forbidden,
            canaries=canaries,
            answer=answer,
            trace_json=run.model_dump_json() if run else "",
            llm_inputs=client.responses.inputs,
            calls=env.call_records,
            cases=cases,
            network_attempts=env.network_attempts,
        )
    )

    _compare(scenario, result, answer, probe_outputs, run_args, llm.expected_llm_calls)
    if source_error is not None:
        # Расхождение источника LLM (replay/record) — диагностируемая ошибка harness,
        # а не успех и не оценка агента: сценарий исключается из метрик.
        result.harness_error = source_error
    return result


def run_dataset(dataset: Dataset, source: LLMSource | None = None) -> list[ScenarioResult]:
    return [run_scenario(s, source) for s in dataset.scenarios]


# --- загрузка и отчёт ----------------------------------------------------------
def load_dataset(path: Path = DEFAULT_DATASET) -> tuple[Dataset, str]:
    raw = Path(path).read_bytes()
    return Dataset.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def _redact(node: object, secrets: Sequence[str]) -> object:
    if isinstance(node, str):
        for s in secrets:
            node = node.replace(s, REDACTED)
        return node
    if isinstance(node, dict):
        return {k: _redact(v, secrets) for k, v in node.items()}
    if isinstance(node, list):
        return [_redact(v, secrets) for v in node]
    return node


def build_report(
    dataset: Dataset,
    dataset_sha256: str,
    results: Sequence[ScenarioResult],
    metrics: Sequence[MetricResult] | None = None,
    *,
    mode: Mode = Mode.DETERMINISTIC_FAKE,
    recordings: Sequence = (),
    not_replayed: Sequence[str] = (),
) -> dict:
    """Детерминированный отчёт: без времён, uuid, длительностей и текстов ответов."""

    metrics = list(metrics) if metrics is not None else compute_metrics(results)
    secrets = (
        *GLOBAL_CANARIES,
        *(c for s in dataset.scenarios for c in s.secret_canaries),
        *(f.secret for s in dataset.scenarios for f in s.tool_faults),
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset.dataset_id,
        "dataset_sha256": dataset_sha256,
        # Режимы не смешиваются: отчёты разных режимов не сопоставимы как оценка модели.
        "mode": mode.value,
        "llm": {
            "real_llm_calls": 0,
            "model_quality_claim": False,
            "recordings": sorted(
                ({"recording_id": r.recording_id, "integrity": r.integrity} for r in recordings),
                key=lambda r: r["recording_id"],
            ),
            "not_replayed": sorted(not_replayed),
        },
        "scenario_count": len(results),
        "harness_errors": sorted(r.scenario_id for r in results if r.harness_error),
        # Нарушения безопасности у исключённых из метрик сценариев не должны исчезать.
        "security_violations_in_excluded": sorted(
            r.scenario_id for r in results if r.harness_error and r.security_violations
        ),
        "metrics": {
            m.name: {**m.model_dump(exclude={"name"}), "definition": METRIC_DEFINITIONS[m.name]}
            for m in metrics
        },
        "scenarios": [r.model_dump(mode="json") for r in results],
    }
    report = _redact(report, secrets)
    text = json.dumps(report, ensure_ascii=False)
    if any(s in text for s in secrets):  # fail-closed
        raise ReportLeakError("synthetic secret present in report")
    return report


def render_report(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def summarize(report: dict) -> str:
    lines = [f"dataset={report['dataset_id']} mode={report['mode']} scenarios={report['scenario_count']}"]
    for name, m in report["metrics"].items():
        value = "n/a" if m["value"] is None else f"{m['value']:.4f}"
        lines.append(f"  {name}: {value} ({m['numerator']}/{m['denominator']})")
    bad = [s["scenario_id"] for s in report["scenarios"] if s["mismatches"] or s["security_violations"] or s["harness_error"]]
    lines.append(f"  scenarios_with_findings: {bad or 'none'}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.runner")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-mismatch", action="store_true")
    parser.add_argument(
        "--mode",
        choices=("fake", "replay", "synthetic-record"),
        default="fake",
        help=(
            "fake: deterministic_fake; replay: recorded_replay; "
            "synthetic-record: synthetic_recording (запись синтетического FakeLLMClient, не реальной модели)"
        ),
    )
    parser.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    args = parser.parse_args(argv)

    dataset, digest = load_dataset(args.dataset)
    recordings: list = []
    not_replayed: list[str] = []

    if args.mode == "fake":
        mode, source = Mode.DETERMINISTIC_FAKE, FakeSource()
    elif args.mode == "replay":
        loaded = load_recordings(args.recordings)
        mode, source = Mode.RECORDED_REPLAY, ReplaySource(loaded)
        known = {s.id for s in dataset.scenarios}
        if set(loaded) - known:
            print("recordings reference unknown scenarios", file=sys.stderr)
            return 2
        not_replayed = [s.id for s in dataset.scenarios if s.id not in loaded]
        dataset = dataset.model_copy(update={"scenarios": [s for s in dataset.scenarios if s.id in loaded]})
        recordings = list(loaded.values())
    else:
        mode, source = Mode.SYNTHETIC_RECORDING, RecordSource()

    results = run_dataset(dataset, source)

    if args.mode == "synthetic-record":
        clean = {r.scenario_id for r in results if not (r.mismatches or r.security_violations or r.harness_error)}
        for sid, recording in sorted(source.recordings.items()):
            if sid not in clean:
                print(f"not saved (scenario has findings): {sid}", file=sys.stderr)
                continue
            save_recording(recording, args.recordings / recording_filename(sid))
        recordings = list(source.recordings.values())
        print(f"recorded={len(source.recordings)} refused={sorted(source.refusals) or 'none'}")

    report = build_report(
        dataset, digest, results, mode=mode, recordings=recordings, not_replayed=not_replayed
    )
    if args.output:
        args.output.write_text(render_report(report), encoding="utf-8")
    print(summarize(report))

    failing = bool(not_replayed) or any(
        r.mismatches or r.security_violations or r.harness_error for r in results
    )
    return 1 if (args.fail_on_mismatch and failing) else 0


if __name__ == "__main__":
    sys.exit(main())
