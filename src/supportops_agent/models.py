"""Data models used by SupportOps Agent."""

from datetime import date
from enum import StrEnum

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