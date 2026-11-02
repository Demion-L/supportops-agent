"""Ручной live-smoke классификатора на небольшом наборе синтетических обращений.

НЕ входит в pytest. Делает не более MAX_CASES (10) запросов к OpenAI.

Запуск (из корня проекта; ключ берётся из окружения или .env):
    PYTHONPATH=src .venv/bin/python scripts/eval_classification.py
    PYTHONPATH=src .venv/bin/python scripts/eval_classification.py --dry-run   # без API
    PYTHONPATH=src .venv/bin/python scripts/eval_classification.py --only exception_flight

Ключ никогда не печатается; в тексте ошибок он маскируется.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from supportops_agent.classification import ClassificationError, OpenAITicketClassifier
from supportops_agent.models import SupportTicket

MAX_CASES = 10
DATASET = Path(__file__).resolve().parent.parent / "evals" / "classification_dataset.json"


def check(case: dict, c) -> list[str]:
    """Вернуть список расхождений ожидаемого и фактического результата."""

    problems = []
    if c.primary_intent.value not in case["primary"]:
        problems.append(f"primary={c.primary_intent.value}, ожидалось одно из {case['primary']}")
    missing = set(case["required_intents"]) - {i.value for i in c.intents}
    if missing:
        problems.append(f"нет intents: {sorted(missing)}")
    if c.entities.order_id != case["order_id"]:
        problems.append(f"order_id={c.entities.order_id!r}, ожидалось {case['order_id']!r}")
    if "exception_requested" in case and c.context.exception_requested != case["exception_requested"]:
        problems.append(f"exception_requested={c.context.exception_requested}")
    if case.get("claimed_reason_present") and not c.context.claimed_reason:
        problems.append("claimed_reason пуст")
    if "requires_clarification" in case and c.requires_clarification != case["requires_clarification"]:
        problems.append(f"requires_clarification={c.requires_clarification}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="только проверить датасет")
    parser.add_argument("--only", help="id одного обращения")
    args = parser.parse_args()

    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
    if len(cases) > MAX_CASES:
        sys.exit(f"Датасет больше {MAX_CASES} обращений — live-прогон отклонён.")
    if args.dry_run:
        print(f"Датасет валиден: {len(cases)} обращений, API не вызывался.")
        return 0

    load_dotenv()
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY не задан — live evaluation не выполнялась.")

    classifier = OpenAITicketClassifier(OpenAI())
    failed = 0
    for case in cases:
        ticket = SupportTicket(ticket_id=f"eval_{case['id']}", customer_id="eval_customer",
                               message=case["message"])
        print(f"\n[{case['id']}] {case['message']}")
        print(f"  ожидалось: primary∈{case['primary']} required={case['required_intents']} "
              f"order_id={case['order_id']!r}")
        try:
            c = classifier.classify(ticket).classification
        except ClassificationError as exc:
            failed += 1
            print(f"  ОШИБКА классификации: {str(exc).replace(key, '***')[:300]}")
            continue
        print(f"  фактически: intents={[i.value for i in c.intents]} primary={c.primary_intent.value} "
              f"order_id={c.entities.order_id!r} exception={c.context.exception_requested} "
              f"reason={c.context.claimed_reason!r} clarify={c.requires_clarification}")
        problems = check(case, c)
        failed += bool(problems)
        print("  " + ("OK" if not problems else "РАСХОЖДЕНИЕ: " + "; ".join(problems)))

    print(f"\nИтог: {len(cases) - failed}/{len(cases)} совпали с ожиданиями.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
