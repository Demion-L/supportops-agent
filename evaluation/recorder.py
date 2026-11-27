"""Record mode: обёртка над upstream-клиентом, сохраняющая только allowlist-поля.

Клиент прозрачен для агента: возвращает upstream-ответ без изменений и
не меняет контракт `client.responses.create(**kwargs)`.

В WP1 upstream — только синтетический fake, сценарий обязан нести `synthetic: true`.
Подключение реальной модели запрещено на уровне конструктора источника (sources.py) и схемы записи
(`source` допускает единственное значение).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from .recording import (
    ALLOWED_ERROR_TYPES,
    Interaction,
    RecordedResponse,
    RecordedToolCall,
    RecordedUsage,
    Recording,
    RecordingRefused,
    ReferenceMap,
    make_recording,
    project_request,
    scan_text,
    scan_value,
)

_NO_MODEL = "n-a"


class RecordingSession:
    """Состояние записи одного сценария. После первой ошибки «отравлено» (fail-closed)."""

    def __init__(
        self,
        recording_id: str,
        scenario_id: str,
        *,
        declared_user_texts: Iterable[str],
        synthetic: bool | None = None,
        extra_secrets: Iterable[str] = (),
    ):
        self.recording_id = recording_id
        self.scenario_id = scenario_id
        self._declared = frozenset(declared_user_texts)
        self._secrets = tuple(extra_secrets)
        self._refs = ReferenceMap()
        self._interactions: list[Interaction] = []
        self._model: str | None = None
        self.error: RecordingRefused | None = None
        # Fail-closed: без явного `synthetic is True` сессия отравлена с первой секунды —
        # upstream не вызывается, finalize() отказывает. Декларация — необходимое, но не
        # достаточное условие: содержимое всё равно проходит гейт (_gate_request/_convert).
        if synthetic is not True:
            self.refuse("synthetic_not_declared")

    # --- ошибки ---
    def refuse(self, code: str, detail: str = "") -> RecordingRefused:
        error = RecordingRefused(code, detail)
        if self.error is None:
            self.error = error
        return self.error

    # --- проверка запроса ---
    def _gate_request(self, kwargs: dict[str, Any]):
        projected = project_request(kwargs, self._refs)
        if projected.unsupported:
            raise self.refuse("unsupported_request", ",".join(sorted(set(projected.unsupported))))
        for text in projected.user_texts:
            if text not in self._declared:
                # Произвольный (потенциально реальный) пользовательский ввод не записывается.
                raise self.refuse("undeclared_user_text")
        found: set[str] = set()
        for text in projected.texts:
            found.update(scan_text(text, extra_secrets=self._secrets))
        if found:
            raise self.refuse("sensitive_request", ",".join(sorted(found)))
        model = projected.projection["model"]
        if self._model not in (None, model):
            raise self.refuse("model_changed")
        self._model = model
        return projected

    # --- преобразование ответа в allowlist ---
    @staticmethod
    def _usage(response: Any) -> RecordedUsage | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        try:
            return RecordedUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
            )
        except Exception:  # noqa: BLE001 - произвольная форма usage не записывается
            return None

    def _convert(self, response: Any) -> tuple[RecordedResponse, list[Any]]:
        calls: list[RecordedToolCall] = []
        call_ids: list[Any] = []
        for item in getattr(response, "output", None) or []:
            if getattr(item, "type", None) != "function_call":
                continue
            raw = getattr(item, "arguments", None)
            name = getattr(item, "name", None)
            if not isinstance(raw, str) or not isinstance(name, str):
                raise self.refuse("malformed_response")
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            try:
                if isinstance(parsed, dict):
                    calls.append(RecordedToolCall(name=name, arguments=parsed))
                else:
                    calls.append(RecordedToolCall(name=name, raw_arguments=raw))
            except ValueError:
                raise self.refuse("malformed_response") from None
            call_ids.append(getattr(item, "call_id", None))

        text = getattr(response, "output_text", "")
        if not isinstance(text, str):
            raise self.refuse("malformed_response")
        try:
            recorded = RecordedResponse(
                type="tool_calls" if calls else "text",
                text=text,
                tool_calls=calls,
                usage=self._usage(response),
            )
        except ValueError:
            raise self.refuse("malformed_response") from None

        found: set[str] = set(scan_text(recorded.text, extra_secrets=self._secrets))
        for call in recorded.tool_calls:
            found.update(scan_value(call.arguments, extra_secrets=self._secrets))
            if call.raw_arguments is not None:
                found.update(scan_text(call.raw_arguments, extra_secrets=self._secrets))
        if found:
            raise self.refuse("sensitive_response", ",".join(sorted(found)))
        return recorded, call_ids

    # --- основной шаг ---
    def call(self, upstream_create: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
        if self.error is not None:
            raise self.error
        projected = self._gate_request(kwargs)
        sequence = len(self._interactions) + 1

        try:
            response = upstream_create(**kwargs)
        except Exception as exc:
            name = type(exc).__name__
            self._interactions.append(
                Interaction(
                    sequence=sequence,
                    request_fingerprint=projected.fingerprint,
                    response=RecordedResponse(
                        type="error",
                        error_type=name if name in ALLOWED_ERROR_TYPES else "RuntimeError",
                    ),
                )
            )
            raise  # агент видит исходную ошибку upstream; текст не сохраняется

        recorded, call_ids = self._convert(response)
        self._refs.register(sequence, getattr(response, "id", None), call_ids)
        self._interactions.append(
            Interaction(sequence=sequence, request_fingerprint=projected.fingerprint, response=recorded)
        )
        return response

    # --- завершение ---
    def finalize(self) -> Recording:
        if self.error is not None:
            raise self.error
        return self._build("complete")

    def snapshot_incomplete(self) -> Recording:
        """Диагностический срез; сохранить/загрузить его нельзя (status=incomplete)."""

        return self._build("incomplete")

    def _build(self, status: str) -> Recording:
        return make_recording(
            recording_id=self.recording_id,
            scenario_id=self.scenario_id,
            model=self._model or _NO_MODEL,
            status=status,  # type: ignore[arg-type]
            interactions=list(self._interactions),
        )


class _RecordingResponses:
    def __init__(self, session: RecordingSession, upstream: Any):
        self._session = session
        self._upstream = upstream
        self.calls = 0
        self.inputs: list[str] = []

    def create(self, **kwargs):
        self.calls += 1
        self.inputs.append(json.dumps(kwargs.get("input"), default=str, sort_keys=True))
        return self._session.call(self._upstream.responses.create, kwargs)


class RecordingLLMClient:
    """Совместим с контрактом `client.responses.create`."""

    def __init__(self, session: RecordingSession, upstream: Any):
        self.responses = _RecordingResponses(session, upstream)
