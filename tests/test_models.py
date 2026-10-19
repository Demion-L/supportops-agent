from datetime import UTC, datetime

from supportops_agent.models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepType,
)


def test_agent_run_starts_with_empty_steps():
    run = AgentRun(ticket_id="ticket_001")

    assert run.ticket_id == "ticket_001"
    assert run.steps == []
    assert run.run_id


def test_agent_step_has_generated_id():
    step = AgentStep(
        step_number=1,
        step_type=AgentStepType.TOOL_EXECUTION,
        input={"order_id": "order_001"},
    )

    assert step.step_number == 1
    assert step.step_type == AgentStepType.TOOL_EXECUTION
    assert step.input == {"order_id": "order_001"}
    assert step.step_id


def test_agent_run_summary_counts_steps_by_type():
    run = AgentRun(
        ticket_id="ticket_001",
        status=AgentRunStatus.COMPLETED,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        steps=[
            AgentStep(step_number=1, step_type=AgentStepType.LLM_CALL),
            AgentStep(step_number=2, step_type=AgentStepType.TOOL_EXECUTION),
            AgentStep(step_number=3, step_type=AgentStepType.LLM_CALL),
            AgentStep(step_number=4, step_type=AgentStepType.FINAL),
        ],
    )

    summary = run.summary()

    assert summary["status"] == AgentRunStatus.COMPLETED
    assert summary["duration_ms"] == 2000
    assert summary["llm_calls"] == 2
    assert summary["tool_calls"] == 1
    assert summary["failed_tool_calls"] == 0
    assert summary["error"] is None


def test_agent_run_summary_sums_token_usage_across_llm_calls():
    run = AgentRun(
        ticket_id="ticket_001",
        steps=[
            AgentStep(
                step_number=1,
                step_type=AgentStepType.LLM_CALL,
                output={"usage": {"total_tokens": 100}},
            ),
            AgentStep(
                step_number=2,
                step_type=AgentStepType.LLM_CALL,
                output={"usage": {"total_tokens": 50}},
            ),
        ],
    )

    assert run.summary()["total_tokens"] == 150


def test_agent_run_summary_reports_none_total_tokens_when_unavailable():
    run = AgentRun(
        ticket_id="ticket_001",
        steps=[
            AgentStep(
                step_number=1,
                step_type=AgentStepType.LLM_CALL,
                output={"usage": None},
            ),
        ],
    )

    assert run.summary()["total_tokens"] is None


def test_agent_run_summary_counts_failed_tool_calls_and_surfaces_error():
    run = AgentRun(
        ticket_id="ticket_001",
        status=AgentRunStatus.FAILED,
        steps=[
            AgentStep(
                step_number=1,
                step_type=AgentStepType.TOOL_EXECUTION,
                error="database connection failed",
            ),
            AgentStep(
                step_number=2,
                step_type=AgentStepType.FAILURE,
                error="Agent exceeded maximum number of steps: 10",
            ),
        ],
    )

    summary = run.summary()

    assert summary["failed_tool_calls"] == 1
    assert summary["error"] == "Agent exceeded maximum number of steps: 10"


def test_agent_run_summary_duration_is_none_while_running():
    run = AgentRun(ticket_id="ticket_001")

    assert run.summary()["duration_ms"] is None

