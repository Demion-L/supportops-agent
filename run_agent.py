from dotenv import load_dotenv
from openai import OpenAI

from supportops_agent.runtime.agent import SupportAgent
from supportops_agent.tools.authorization import support_agent_context
from supportops_agent.classification import OpenAITicketClassifier
from supportops_agent.models import SupportTicket
from supportops_agent.business.refund_evaluation import default_refund_evaluator


def build_agent(client: OpenAI) -> SupportAgent:
    """Явная композиция зависимостей (ничего не берётся из ответов модели)."""

    return SupportAgent(
        client,
        classifier=OpenAITicketClassifier(client),
        context=support_agent_context(),
        refund_evaluator=default_refund_evaluator(),
    )


def main() -> None:
    load_dotenv()
    agent = build_agent(OpenAI())

    # Доверенная идентичность: customer_id задаёт приложение (здесь — демо-тикет),
    # а не текст обращения.
    ticket = SupportTicket(
        ticket_id="ticket_001",
        customer_id="customer_001",
        message="What is the email address of customer_001?",
    )

    answer = agent.run(ticket)

    print("\nFinal answer:")
    print(answer)


if __name__ == "__main__":
    main()
