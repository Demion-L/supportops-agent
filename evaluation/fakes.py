"""Fake-компоненты для офлайн-прогона: LLM-клиент и классификатор.

Подключаются через публичный конструктор SupportAgent(client, classifier);
production-код не изменяется. Реальных сетевых вызовов нет.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from supportops_agent.classification import (
    CLASSIFIER_MODEL_NAME,
    ClassificationError,
    ClassificationResult,
    TicketClassification,
)

from .schemas import ClassifierScript, LLMTurn

# Фиксированный usage: суммы токенов детерминированы.
_USAGE = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)


class FakeResponse:
    def __init__(self, response_id: str, output: list, text: str):
        self.id = response_id
        self.output = output
        self.output_text = text
        self.usage = _USAGE


class FakeResponses:
    """Воспроизводит `client.responses.create` по сценарному скрипту."""

    def __init__(self, script: list[LLMTurn]):
        self._script = list(script)
        self.calls = 0
        self.inputs: list[str] = []  # сериализованный input каждого вызова (для leak-проверок)
        self.script_exhausted = False

    def create(self, **kwargs):
        self.calls += 1
        self.inputs.append(json.dumps(kwargs.get("input"), default=str, sort_keys=True))

        if self._script:
            turn = self._script.pop(0)
        else:
            # Скрипт короче, чем потребовал агент: это ошибка сценария, а не агента.
            self.script_exhausted = True
            turn = LLMTurn()

        if turn.raise_error:
            raise RuntimeError("synthetic llm provider failure")

        output = [
            SimpleNamespace(
                type="function_call",
                call_id=f"call_{self.calls}_{index}",
                name=call.name,
                arguments=(
                    call.raw_arguments
                    if call.raw_arguments is not None
                    else json.dumps(call.arguments, sort_keys=True)
                ),
            )
            for index, call in enumerate(turn.tool_calls)
        ]
        return FakeResponse(f"resp_{self.calls}", output, turn.text)

    @property
    def unused_turns(self) -> int:
        return len(self._script)


class FakeLLMClient:
    def __init__(self, script: list[LLMTurn]):
        self.responses = FakeResponses(script)


class FakeClassifier:
    """Replay заранее записанного результата классификации."""

    def __init__(self, script: ClassifierScript):
        self._script = script
        self.calls = 0

    def classify(self, ticket) -> ClassificationResult:
        self.calls += 1
        if self._script.mode == "expected_error":
            raise ClassificationError(self._script.error_kind, "synthetic classifier failure")
        if self._script.mode == "crash":
            raise RuntimeError("synthetic classifier defect")
        return ClassificationResult(
            classification=TicketClassification.model_validate(self._script.result),
            model=CLASSIFIER_MODEL_NAME,
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
