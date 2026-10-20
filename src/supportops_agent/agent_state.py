"""In-progress state for a single agent run."""

from dataclasses import dataclass

from .models import SupportTicket


@dataclass
class AgentState:
    ticket: SupportTicket
    response: object
    step_count: int = 0
