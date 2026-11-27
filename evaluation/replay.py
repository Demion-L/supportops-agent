"""Replay mode: клиент, возвращающий записанные ответы без сети.

Модуль не импортирует сетевые библиотеки и провайдерский SDK. Любое
несоответствие записи — `ReplayMismatch` с стабильным кодом; первая ошибка
«отравляет» клиент (все следующие вызовы падают тем же кодом), поэтому агент не
может «проглотить» расхождение и получить успешный прогон.

Коды: fingerprint_mismatch, sequence_mismatch, extra_call, missing_calls,
recorded_error (штатное воспроизведение записанной ошибки провайдера, не mismatch).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from .recording import Recording, RecordingError, ReferenceMap, project_request

_ERRORS = {"RuntimeError": RuntimeError, "TimeoutError": TimeoutError, "ConnectionError": ConnectionError}


class ReplayMismatch(RecordingError):
    code = "replay_mismatch"


class _ReplayResponse:
    def __init__(self, response_id: str, output: list, text: str, usage):
        self.id = response_id
        self.output = output
        self.output_text = text
        self.usage = usage


class _ReplayResponses:
    def __init__(self, recording: Recording):
        self._recording = recording
        self._refs = ReferenceMap()
        self.calls = 0
        self.inputs: list[str] = []
        self.error: ReplayMismatch | None = None

    def _fail(self, code: str, detail: str) -> ReplayMismatch:
        error = ReplayMismatch(code, detail)
        if self.error is None:
            self.error = error
        return self.error

    def create(self, **kwargs):
        self.calls += 1
        self.inputs.append(json.dumps(kwargs.get("input"), default=str, sort_keys=True))
        if self.error is not None:
            raise self.error

        interactions = self._recording.interactions
        index = self.calls - 1
        if index >= len(interactions):
            raise self._fail("extra_call", f"call={self.calls} recorded={len(interactions)}")

        expected = interactions[index]
        observed = project_request(kwargs, self._refs).fingerprint
        if observed != expected.request_fingerprint:
            # Тот же запрос, но на другой позиции — нарушена последовательность.
            other = [i.sequence for i in interactions if i.request_fingerprint == observed]
            code = "sequence_mismatch" if other else "fingerprint_mismatch"
            raise self._fail(code, f"sequence={expected.sequence}")

        response = expected.response
        if response.type == "error":
            raise _ERRORS[response.error_type or "RuntimeError"]("recorded llm provider failure")

        sequence = self.calls
        output = [
            SimpleNamespace(
                type="function_call",
                call_id=f"call_{sequence}_{i}",
                name=call.name,
                arguments=(
                    call.raw_arguments
                    if call.raw_arguments is not None
                    else json.dumps(call.arguments, sort_keys=True)
                ),
            )
            for i, call in enumerate(response.tool_calls)
        ]
        self._refs.register(sequence, f"resp_{sequence}", [item.call_id for item in output])
        usage = (
            SimpleNamespace(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                total_tokens=response.usage.total_tokens,
            )
            if response.usage
            else None
        )
        return _ReplayResponse(f"resp_{sequence}", output, response.text, usage)

    def finish(self) -> ReplayMismatch | None:
        """Недостающие вызовы: вызываются после завершения сценария."""

        if self.error is None and self.calls < len(self._recording.interactions):
            self._fail("missing_calls", f"calls={self.calls} recorded={len(self._recording.interactions)}")
        return self.error


class ReplayLLMClient:
    """Совместим с контрактом `client.responses.create`; сети не использует."""

    def __init__(self, recording: Recording):
        self.responses = _ReplayResponses(recording)

    @property
    def recording(self) -> Recording:
        return self.responses._recording  # noqa: SLF001

    def finish(self) -> ReplayMismatch | None:
        return self.responses.finish()

