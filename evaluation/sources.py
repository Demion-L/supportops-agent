"""Источники LLM-ответов для Runner: fake, replay, record.

Один Runner, три режима. Режим попадает в отчёт и не смешивается:

  deterministic_fake — скриптованный fake (M01); качество модели не измеряет;
  recorded_replay    — воспроизведение записи; детектор регрессий агентной
                       логики, НЕ измерение качества новой версии LLM;
  synthetic_recording — запись через СИНТЕТИЧЕСКИЙ upstream (FakeLLMClient). Это не
                       запись реальной модели: реальные LLM-вызовы запрещены, а сценарий
                       без явного `synthetic: true` отвергается.

Источник открывает сессию на сценарий: клиент (duck-typed `responses.create`),
ожидаемое число LLM-вызовов и `finish()`, возвращающий код ошибки источника
(несовпадение replay / отказ записи) либо None.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from .fakes import FakeLLMClient
from .recorder import RecordingLLMClient, RecordingSession
from .recording import (
    LiveUpstreamNotEnabled,
    Recording,
    RecordingError,
    RecordingRefused,
)
from .replay import ReplayLLMClient
from .schemas import Scenario


class Mode(StrEnum):
    DETERMINISTIC_FAKE = "deterministic_fake"
    RECORDED_REPLAY = "recorded_replay"
    SYNTHETIC_RECORDING = "synthetic_recording"


@dataclass
class LLMSession:
    client: Any
    expected_llm_calls: int
    finish: Callable[[], str | None]


class LLMSource(Protocol):
    mode: Mode

    def open(self, scenario: Scenario) -> LLMSession: ...


class FakeSource:
    mode = Mode.DETERMINISTIC_FAKE

    def open(self, scenario: Scenario) -> LLMSession:
        return LLMSession(FakeLLMClient(scenario.llm_script), len(scenario.llm_script), lambda: None)


class ReplaySource:
    mode = Mode.RECORDED_REPLAY

    def __init__(self, recordings: Mapping[str, Recording]):
        self.recordings = dict(recordings)

    def open(self, scenario: Scenario) -> LLMSession:
        recording = self.recordings.get(scenario.id)
        if recording is None:
            # Нет записи — не «пустой успех»: сценарий помечается ошибкой harness.
            return LLMSession(_NoLLM(), 0, lambda: "replay:no_recording")
        client = ReplayLLMClient(recording)

        def finish() -> str | None:
            error = client.finish()
            return None if error is None else f"replay:{error.code}"

        return LLMSession(client, len(recording.interactions), finish)


class _NoLLMResponses:
    calls = 0

    def __init__(self) -> None:
        self.inputs: list[str] = []

    def create(self, **_kwargs):
        self.calls += 1
        raise RecordingError("no_recording")


class _NoLLM:
    def __init__(self) -> None:
        self.responses = _NoLLMResponses()


class RecordSource:
    """Запись через upstream. WP1: upstream — только синтетический fake.

    `upstream_factory(scenario)` — точка расширения для будущего live-источника;
    `upstream_kind` обязан быть `synthetic_fake`, иначе конструктор отказывает.
    """

    mode = Mode.SYNTHETIC_RECORDING

    def __init__(
        self,
        upstream_factory: Callable[[Scenario], Any] | None = None,
        *,
        upstream_kind: str = "synthetic_fake",
        recording_id_prefix: str = "synthetic",
    ):
        if upstream_kind != "synthetic_fake":
            raise LiveUpstreamNotEnabled(detail="only synthetic upstream is allowed in WP1")
        self._factory = upstream_factory or (lambda s: FakeLLMClient(s.llm_script))
        self._custom_factory = upstream_factory is not None
        self._prefix = recording_id_prefix
        self.recordings: dict[str, Recording] = {}
        self.refusals: dict[str, str] = {}

    def open(self, scenario: Scenario) -> LLMSession:
        session = RecordingSession(
            f"{self._prefix}_{scenario.id}",
            scenario.id,
            declared_user_texts=[scenario.ticket.message],
            synthetic=scenario.synthetic,
            extra_secrets=[*scenario.secret_canaries, *(f.secret for f in scenario.tool_faults)],
        )
        upstream = self._factory(scenario)
        if not isinstance(upstream, FakeLLMClient):
            # WP1: единственный допустимый upstream — синтетический fake (не самодекларация).
            raise LiveUpstreamNotEnabled(detail="upstream must be FakeLLMClient in WP1")
        client = RecordingLLMClient(session, upstream)

        def finish() -> str | None:
            try:
                self.recordings[scenario.id] = session.finalize()
            except RecordingRefused as exc:
                self.refusals[scenario.id] = exc.code
                return f"record:{exc.code}"
            return None

        return LLMSession(client, len(scenario.llm_script), finish)
