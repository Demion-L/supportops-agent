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

    def complete(self) -> None:
        """Mark the run as successfully completed."""

        self.run.status = AgentRunStatus.COMPLETED
        self.run.finished_at = datetime.now(UTC)

    def fail(self, error: str | None = None) -> None:
        """Mark the run as failed."""
        if error is not None:
            self.add_step(
                AgentStep(
                    step_number=self.next_step_number(),
                    step_type=AgentStepType.FAILURE,
                    error=error,
                )
            )

        self.run.status = AgentRunStatus.FAILED
        self.run.finished_at = datetime.now(UTC)
