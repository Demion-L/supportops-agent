from supportops_agent.models import (
    AgentRunStatus, 
    AgentStep, 
    AgentStepType
    )
from supportops_agent.runtime.run_manager import AgentRunManager


def test_run_manager_creates_running_run():
    manager = AgentRunManager("ticket_001")

    assert manager.run.ticket_id == "ticket_001"
    assert manager.run.status == AgentRunStatus.RUNNING
    assert manager.run.started_at is not None
    assert manager.run.finished_at is None


def test_run_manager_adds_steps():
    manager = AgentRunManager("ticket_001")

    step = AgentStep(
        step_number=1,
        step_type=AgentStepType.LLM_CALL,
    )

    manager.add_step(step)

    assert len(manager.run.steps) == 1
    assert manager.run.steps[0] is step
    assert manager.next_step_number() == 2


def test_run_manager_completes_run():
    manager = AgentRunManager("ticket_001")

    manager.complete()

    assert manager.run.status == AgentRunStatus.COMPLETED
    assert manager.run.finished_at is not None


def test_run_manager_fails_run():
    manager = AgentRunManager("ticket_001")

    manager.fail()

    assert manager.run.status == AgentRunStatus.FAILED
    assert manager.run.finished_at is not None


def test_run_manager_records_failure_step():
    manager = AgentRunManager("ticket_001")

    manager.fail("Something went wrong")

    assert manager.run.status == AgentRunStatus.FAILED
    assert manager.run.finished_at is not None

    assert len(manager.run.steps) == 1

    failure_step = manager.run.steps[0]

    assert failure_step.step_number == 1
    assert failure_step.step_type == AgentStepType.FAILURE
    assert failure_step.error == "Something went wrong"