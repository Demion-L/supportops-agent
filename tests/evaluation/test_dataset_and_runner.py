"""EVAL-M01/WP1: датасет, воспроизводимость, соответствие baseline, изоляция."""

import json
import re
import socket

import pytest

from evaluation.runner import (
    DEFAULT_DATASET,
    _isolated,
    _Env,
    build_report,
    load_dataset,
    render_report,
    run_dataset,
    run_scenario,
)
from evaluation.schemas import Category, Dataset, Outcome
from supportops_agent.business import mock_data
from supportops_agent.tools.definitions import TOOL_REGISTRY


@pytest.fixture(scope="module")
def loaded():
    return load_dataset(DEFAULT_DATASET)


@pytest.fixture(scope="module")
def baseline(loaded):
    dataset, _ = loaded
    return run_dataset(dataset)


def scenario(loaded, sid):
    return next(s for s in loaded[0].scenarios if s.id == sid).model_copy(deep=True)


# --- датасет ---------------------------------------------------------------------
def test_dataset_has_at_least_20_scenarios_and_all_categories(loaded):
    dataset, _ = loaded
    assert len(dataset.scenarios) >= 20
    assert {s.category for s in dataset.scenarios} == set(Category)


def test_dataset_is_synthetic_only(loaded):
    text = DEFAULT_DATASET.read_text(encoding="utf-8")
    domains = set(re.findall(r"@([\w.-]+)", text))
    assert domains <= {"example.com"}
    known = set(mock_data.CUSTOMERS) | {"customer_999"}
    for s in loaded[0].scenarios:
        assert s.ticket.customer_id in known, s.id
        assert s.ticket.ticket_id.startswith("T-"), s.id


def test_dataset_contains_negative_and_positive_expectations(loaded):
    outcomes = {s.expected.outcome for s in loaded[0].scenarios}
    assert outcomes == set(Outcome)  # completed, degraded и failed заданы раздельно


# --- baseline --------------------------------------------------------------------
def test_baseline_all_scenarios_match_expectations(baseline):
    assert all(r.harness_error is None for r in baseline)
    assert [(r.scenario_id, r.mismatches) for r in baseline if r.mismatches] == []
    assert [(r.scenario_id, r.security_violations) for r in baseline if r.security_violations] == []


def test_baseline_outcomes_are_not_conflated(baseline):
    by_id = {r.scenario_id: r.outcome for r in baseline}
    assert by_id["cf-001"] == Outcome.DEGRADED
    assert by_id["cf-003"] == Outcome.FAILED
    assert by_id["st-002"] == Outcome.FAILED
    # Сбой инструмента не превращает прогон ни в FAILED, ни в DEGRADED (поведение агента).
    assert by_id["te-001"] == Outcome.COMPLETED


# --- воспроизводимость ------------------------------------------------------------
def test_report_is_byte_identical_across_runs(loaded):
    dataset, digest = loaded
    first = render_report(build_report(dataset, digest, run_dataset(dataset)))
    second = render_report(build_report(dataset, digest, run_dataset(dataset)))
    assert first == second


def test_results_do_not_depend_on_execution_order(loaded):
    dataset, _ = loaded
    forward = {r.scenario_id: r.model_dump() for r in run_dataset(dataset)}
    reversed_ds = dataset.model_copy(update={"scenarios": list(reversed(dataset.scenarios))})
    backward = {r.scenario_id: r.model_dump() for r in run_dataset(reversed_ds)}
    assert forward == backward


# --- обнаружение несоответствий ---------------------------------------------------
def test_wrong_expected_outcome_is_detected(loaded):
    s = scenario(loaded, "ot-001")
    s.expected.outcome = Outcome.DEGRADED
    r = run_scenario(s)
    assert r.task_success is False
    assert any(m.dimension == "outcome" for m in r.mismatches)


def test_wrong_expected_classification_is_detected(loaded):
    s = scenario(loaded, "ot-001")
    s.expected.classification.primary_intent = "complaint"
    r = run_scenario(s)
    assert r.classification_correct is False


def test_missing_classification_counts_as_incorrect_not_excluded(loaded):
    s = scenario(loaded, "cf-001")  # классификатор упал
    from evaluation.schemas import ExpectedClassification

    s.expected.classification = ExpectedClassification(primary_intent="order_status")
    r = run_scenario(s)
    assert r.observed_classification is None
    assert r.classification_correct is False


def test_wrong_expected_tool_and_arguments_are_detected(loaded):
    s = scenario(loaded, "ot-001")
    s.expected.tools.calls[0].name = "lookup_order"
    r = run_scenario(s)
    assert r.tool_selection_correct is False
    assert r.argument_calls_matched == 0

    s = scenario(loaded, "ot-001")
    s.expected.tools.calls[0].arguments = {"order_id": "order_999"}
    r = run_scenario(s)
    assert r.tool_selection_correct is True  # имя верное
    assert (r.argument_calls_matched, r.argument_calls_total) == (0, 1)  # аргументы нет


def test_extra_and_missing_calls_are_invalid(loaded):
    s = scenario(loaded, "ot-002")
    s.expected.tools.calls = s.expected.tools.calls[:1]
    r = run_scenario(s)
    assert r.tool_selection_correct is False
    assert (r.argument_calls_matched, r.argument_calls_total) == (1, 2)


def test_forbidden_tool_execution_is_detected(loaded):
    s = scenario(loaded, "ot-001")
    s.expected.tools.forbidden = ["get_order_tracking"]
    r = run_scenario(s)
    assert "forbidden_tool_executed" in r.security_violations
    assert r.tool_selection_correct is False


def test_unused_or_extra_llm_turn_is_a_mismatch(loaded):
    s = scenario(loaded, "ot-001")
    s.llm_script.append(s.llm_script[-1])  # скрипт длиннее, чем потребовал агент
    r = run_scenario(s)
    assert any(m.dimension == "llm_calls" for m in r.mismatches)
    assert r.task_success is False

    s = scenario(loaded, "cf-001")
    s.llm_script = s.llm_script  # LLM после fail-closed не должен вызываться
    s.classifier = None  # классификация отключена -> агент пойдёт в LLM без скрипта
    r = run_scenario(s)
    assert any(m.dimension == "llm_calls" for m in r.mismatches)


def test_empty_expected_checks_nothing(loaded):
    s = scenario(loaded, "ot-001")
    s.expected = type(s.expected)()
    r = run_scenario(s)
    assert (r.task_success, r.classification_correct, r.tool_selection_correct) == (None, None, None)
    assert r.mismatches == []


def test_leaked_secret_in_answer_is_detected_and_redacted_from_report(loaded):
    s = scenario(loaded, "te-001")
    s.llm_script[-1].text = "debug: SYNTH_SECRET_TE001_a1b2c3"
    r = run_scenario(s)
    assert "secret_leak" in r.security_violations
    dataset, digest = loaded
    report = render_report(build_report(dataset.model_copy(update={"scenarios": [s]}), digest, [r]))
    assert "SYNTH_SECRET" not in report


def test_other_customers_data_in_answer_is_detected(loaded):
    s = scenario(loaded, "ua-004")
    s.llm_script[-1].text = "Profile: john@example.com"
    r = run_scenario(s)
    assert "cross_customer_data_exposure" in r.security_violations


def test_invalid_ticket_is_harness_error_not_agent_failure(loaded):
    s = scenario(loaded, "ot-001")
    s.ticket.message = ""
    r = run_scenario(s)
    assert r.harness_error == "invalid_ticket"
    assert r.outcome is None


# --- изоляция и отсутствие побочных эффектов ---------------------------------------
def test_run_leaves_global_state_untouched(loaded):
    cases_before = mock_data.CASES
    handlers_before = {n: d.handler for n, d in TOOL_REGISTRY.items()}
    run_dataset(loaded[0])
    assert mock_data.CASES is cases_before and mock_data.CASES == {}
    assert {n: d.handler for n, d in TOOL_REGISTRY.items()} == handlers_before


def test_isolation_is_restored_when_scenario_body_raises(loaded):
    cases_before = mock_data.CASES
    handlers_before = {n: d.handler for n, d in TOOL_REGISTRY.items()}
    connect_before = socket.socket.connect
    with pytest.raises(ZeroDivisionError):
        with _isolated(scenario(loaded, "ot-001"), _Env()):
            1 / 0
    assert mock_data.CASES is cases_before
    assert {n: d.handler for n, d in TOOL_REGISTRY.items()} == handlers_before
    assert socket.socket.connect is connect_before


def test_network_is_blocked_and_counted_inside_scenarios(loaded):
    env = _Env()
    with _isolated(scenario(loaded, "ot-001"), env):
        with pytest.raises(Exception, match="blocked"):
            socket.create_connection(("example.com", 80))
    assert env.network_attempts == 1


def test_synthetic_secrets_never_reach_report(loaded, baseline):
    dataset, digest = loaded
    text = render_report(build_report(dataset, digest, baseline))
    assert "SYNTH_" not in text
    json.loads(text)  # валидный JSON
