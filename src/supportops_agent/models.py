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


class AgentStep(BaseModel):
    """A single step in an agent execution."""

    step_id: str = Field(default_factory=lambda: str(uuid4()))
    step_number: int
    step_type: str
    input: dict[str, object] | None = None
    output: dict[str, object] | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


class AgentRun(BaseModel):
    """Execution trace for a single agent run."""

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    ticket_id: str
    steps: list[AgentStep] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None