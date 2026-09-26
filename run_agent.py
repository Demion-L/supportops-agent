from dotenv import load_dotenv
from openai import OpenAI

from supportops_agent.agent import SupportAgent
from supportops_agent.models import SupportTicket

load_dotenv()


client = OpenAI()

agent = SupportAgent(client)

ticket = SupportTicket(
    ticket_id="ticket_001",
    customer_id="customer_001",
    message="Where is my order order_001?"
)

answer = agent.run(ticket)

print("\nFinal answer:")
print(answer)