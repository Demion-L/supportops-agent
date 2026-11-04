"""Data models used by SupportOps Agent."""

from datetime import UTC, datetime, date
from enum import StrEnum
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field



class OrderStatus(StrEnum):
    """Supported order lifecycle states."""

    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class SupportTicket(BaseModel):
    """Incoming customer support request."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    ticket_id: str = Field(min_length=1, max_length=100)
    customer_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=4000)
    order_id: str | None = None


class OrderRecord(BaseModel):
    """Order information returned by an order lookup tool."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    order_id: str = Field(min_length=1, max_length=100)
    customer_id: str = Field(min_length=1, max_length=100)
    status: OrderStatus
    amount_cents: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    created_at: date
    # Канонический источник даты доставки. None — доставка не подтверждена.
    # Не выводится из created_at и не из TRACKING.last_update.
    # Доменная модель сериализуется полностью; публичный контракт инструмента
    # lookup_order задаётся явно в tools.serialize_order_for_tool.
    delivered_at: date | None = None


class CasePriority(StrEnum):
    """Supported priorities of an internal support case."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class SupportCase(BaseModel):
    """Internal support case created from a ticket."""

    case_id: str
    ticket_id: str
    reason: str
    priority: CasePriority
    status: str = "open"


class AgentStepType(StrEnum):
    CLASSIFICATION = "classification"
    POLICY_DECISION = "policy_decision"
    LLM_CALL = "llm_call"
    TOOL_EXECUTION = "tool_execution"
    FINAL = "final"
    FAILURE = "failure"
    
    
class AgentStep(BaseModel):
    """A single step in an agent execution."""

    step_id: str = Field(default_factory=lambda: str(uuid4()))
    step_number: int
    step_type: AgentStepType
    input: dict[str, object] | None = None
    output: dict[str, object] | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    duration_ms: float | None = None


class AgentRunStatus(StrEnum):
    """Lifecycle state of an agent run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class AgentRun(BaseModel):
    """Execution trace for a single agent run."""

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    ticket_id: str
    status: AgentRunStatus = AgentRunStatus.RUNNING
    steps: list[AgentStep] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def summary(self) -> dict[str, object]:
        """One-shot overview of the run, for debugging and evaluation."""

        llm_steps = [s for s in self.steps if s.step_type == AgentStepType.LLM_CALL]
        tool_steps = [s for s in self.steps if s.step_type == AgentStepType.TOOL_EXECUTION]
        failure_steps = [s for s in self.steps if s.step_type == AgentStepType.FAILURE]
        classification_steps = [
            s for s in self.steps if s.step_type == AgentStepType.CLASSIFICATION
        ]

        duration_ms = None
        if self.finished_at is not None:
            duration_ms = (self.finished_at - self.started_at).total_seconds() * 1000

        total_tokens = sum(
            usage["total_tokens"]
            for step in [*classification_steps, *llm_steps]
            if (usage := (step.output or {}).get("usage"))
        )

        return {
            "status": self.status,
            "duration_ms": duration_ms,
            "llm_calls": len(llm_steps),
            "classification_failed": any(
                s.error is not None for s in classification_steps
            ),
            "tool_calls": len(tool_steps),
            "failed_tool_calls": sum(1 for s in tool_steps if s.error is not None),
            "total_tokens": total_tokens or None,
            "error": failure_steps[-1].error if failure_steps else None,
        }




