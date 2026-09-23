from openai import OpenAI

from .models import SupportTicket
from .tool_executor import execute_tool_call


class SupportAgent:
    def __init__(self, client: OpenAI):
        self.client = client

    def run(self, ticket: SupportTicket) -> str:
        response = self.client.responses.create(
            model="gpt-4.1-mini",
            input=ticket.message,
        )

        return response.output_text