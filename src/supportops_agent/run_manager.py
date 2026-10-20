"""Lifecycle management for agent runs."""

from datetime import UTC, datetime

from supportops_agent.models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepType
    )


class AgentRunManager:
    """Manage the lifecycle and trace of a single agent run."""

    def __init__(self, ticket_id: str):
        self.run = AgentRun(ticket_id=ticket_id)

    def add_step(self, step: AgentStep) -> None:
        """Append an execution step to the current run."""

        self.run.steps.append(step)

    def next_step_number(self) -> int:
        """Return the next sequential step number."""

        return len(self.run.steps) + 1

    def record(
        self,
        step_type: AgentStepType,
        *,
        input: dict[str, object] | None = None,
        output: dict[str, object] | None = None,
        error: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        duration_ms: float | None = None,
    ) -> AgentStep:
        """Build, append, and return the next step in the run."""

        step_kwargs = {
            "step_number": self.next_step_number(),
            "step_type": step_type,
            "input": input,
            "output": output,
            "error": error,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
        }
        if started_at is not None:
            step_kwargs["started_at"] = started_at

        step = AgentStep(**step_kwargs)
        self.add_step(step)

        return step

    def complete(self) -> None:
        """Mark the run as successfully completed."""

        self.run.status = AgentRunStatus.COMPLETED
        self.run.finished_at = datetime.now(UTC)

    def fail(self, error: str | None = None) -> None:
        """Mark the run as failed."""
        if error is not None:
            self.record(AgentStepType.FAILURE, error=error)

        self.run.status = AgentRunStatus.FAILED
        self.run.finished_at = datetime.now(UTC)
