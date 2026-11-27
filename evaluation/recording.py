"""Формат записи LLM-взаимодействий, fingerprint запроса и гейт безопасности.

Принципы (EVAL-M02/WP1):

* Запись — allowlist структурированных полей. Сырые запросы, ответы провайдера,
  заголовки, id провайдера и тексты исключений НЕ сохраняются.
* Только синтетические данные. Гейт `scan_*` — вторая линия защиты (defense in
  depth), а не доказательство безопасности: первая — allowlist структуры и явная
  декларация синтетических пользовательских текстов (см. recorder.py).
* Fingerprint — проверка СОВМЕСТИМОСТИ запроса с записью, а не обезличивание.
  Он содержит sha256 канонической проекции запроса; для низкоэнтропийных
  персональных данных такой хеш не анонимен, поэтому в fingerprint допускаются
  только тексты, прошедшие гейт (синтетические). Реальные данные запись отвергает.
* Загрузка fail-closed: любое отклонение формата — `RecordingFormatError` /
  `UnsupportedSchemaVersion`, а не «мягкая» деградация.
* Сериализация канонична и детерминирована (без времён, uuid, абсолютных путей).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from .invariants import GLOBAL_CANARIES

RECORDING_SCHEMA_VERSION = 1
SOURCE_SYNTHETIC_FAKE = "synthetic_fake_upstream"
ALLOWED_ERROR_TYPES = ("RuntimeError", "TimeoutError", "ConnectionError")
ALLOWED_EMAIL_DOMAIN = "example.com"

_MAX_TEXT = 4000
_MAX_ARG_STRING = 500
_MAX_DEPTH = 5
_MAX_INTERACTIONS = 64
_MAX_FILE_BYTES = 1_000_000
_MAX_KEYS = 50
_MAX_INT = 10_000_000  # числа в аргументах/usage: длинные числа — канал утечки (телефон, карта)

_FINGERPRINT_RE = r"^sha256:[0-9a-f]{64}$"


# --- ошибки (сообщения содержат только стабильные коды, никогда — данные) --------
class RecordingError(Exception):
    code = "recording_error"

    def __init__(self, code: str | None = None, detail: str = ""):
        self.code = code or self.code
        self.detail = detail
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


class RecordingFormatError(RecordingError):
    """Повреждённая или несовместимая запись (fail-closed)."""

    code = "invalid_recording"


class UnsupportedSchemaVersion(RecordingFormatError):
    code = "unsupported_schema_version"


class RecordingRefused(RecordingError):
    """Запись отвергнута: небезопасное содержимое или недекларированный ввод."""

    code = "recording_refused"


class LiveUpstreamNotEnabled(RecordingError):
    code = "live_upstream_not_enabled"


# --- схема -----------------------------------------------------------------------
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordedUsage(_Strict):
    input_tokens: int = Field(ge=0, le=_MAX_INT)
    output_tokens: int = Field(ge=0, le=_MAX_INT)
    total_tokens: int = Field(ge=0, le=_MAX_INT)


class RecordedToolCall(_Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    # Ровно одно из двух: разобранные аргументы либо невалидный JSON «как есть».
    arguments: dict[str, JsonValue] | None = None
    raw_arguments: str | None = Field(default=None, max_length=_MAX_ARG_STRING)

    @model_validator(mode="after")
    def _one_of(self) -> "RecordedToolCall":
        if (self.arguments is None) == (self.raw_arguments is None):
            raise ValueError("exactly one of arguments / raw_arguments is required")
        return self


class RecordedResponse(_Strict):
    type: Literal["text", "tool_calls", "error"]
    text: str = Field(default="", max_length=_MAX_TEXT)
    tool_calls: list[RecordedToolCall] = Field(default_factory=list)
    # Только имя класса из allowlist; текст исключения не сохраняется.
    error_type: Literal["RuntimeError", "TimeoutError", "ConnectionError"] | None = None
    usage: RecordedUsage | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "RecordedResponse":
        if self.type == "error":
            if self.error_type is None or self.text or self.tool_calls or self.usage:
                raise ValueError("error response carries only error_type")
        else:
            if self.error_type is not None:
                raise ValueError("error_type only for type=error")
            if self.type == "tool_calls" and not self.tool_calls:
                raise ValueError("type=tool_calls requires tool_calls")
            if self.type == "text" and self.tool_calls:
                raise ValueError("type=text forbids tool_calls")
        return self


class Interaction(_Strict):
    sequence: int = Field(ge=1)
    request_fingerprint: str = Field(pattern=_FINGERPRINT_RE)
    response: RecordedResponse


class Recording(_Strict):
    schema_version: int
    recording_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    scenario_id: str = Field(pattern=r"^[a-z]{2,4}-\d{3}$")
    model: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,64}$")
    source: Literal["synthetic_fake_upstream"]
    data_class: Literal["synthetic"]
    status: Literal["complete", "incomplete"]
    interactions: list[Interaction] = Field(max_length=_MAX_INTERACTIONS)
    integrity: str = Field(pattern=_FINGERPRINT_RE)

    @model_validator(mode="after")
    def _check(self) -> "Recording":
        if [i.sequence for i in self.interactions] != list(range(1, len(self.interactions) + 1)):
            raise ValueError("sequence must be 1..n without gaps or duplicates")
        return self


# --- каноническая сериализация --------------------------------------------------
def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_integrity(payload: dict[str, Any]) -> str:
    """sha256 тела записи без поля integrity. Защита от порчи, не подпись."""

    body = {k: v for k, v in payload.items() if k != "integrity"}
    return "sha256:" + sha256_hex(canonical_json(body))


def make_recording(
    *,
    recording_id: str,
    scenario_id: str,
    model: str,
    status: Literal["complete", "incomplete"],
    interactions: list[Interaction],
) -> Recording:
    payload: dict[str, Any] = {
        "schema_version": RECORDING_SCHEMA_VERSION,
        "recording_id": recording_id,
        "scenario_id": scenario_id,
        "model": model,
        "source": SOURCE_SYNTHETIC_FAKE,
        "data_class": "synthetic",
        "status": status,
        "interactions": [i.model_dump(mode="json") for i in interactions],
    }
    payload["integrity"] = compute_integrity(payload)
    return Recording.model_validate(payload)


def render_recording(recording: Recording) -> str:
    return json.dumps(recording.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# --- гейт безопасности ----------------------------------------------------------
_ISO_DATETIME = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?(?!\d)")
_EMAIL_OK = re.compile(
    rf"(?<![\w.+-])[\w.+-]+@{re.escape(ALLOWED_EMAIL_DOMAIN)}(?![\w.-])", re.IGNORECASE
)
_PHONE = re.compile(r"(?<![A-Za-z0-9-])\+?\d(?:[\s.()-]{0,3}\d){6,}(?![A-Za-z0-9])")
_HARD_PATTERNS: dict[str, re.Pattern[str]] = {
    "api_key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    "bearer": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\."),
    "dsn": re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|redis|mongodb)://|://[^\s/:@]+:[^\s/@]+@"),
    "credential_assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|secret|passw\w*|pwd|pepper|token|authorization|dsn|пароль|токен|ключ)\b\s*[:=]\s*\S+"
    ),
    "canary": re.compile(r"SYNTH_(?:SECRET|TOKEN)\w*"),
    "long_token": re.compile(r"(?<![\w/+-])[A-Za-z0-9+/-]{32,}={0,2}(?![\w/+-])"),
}


def scan_text(text: str, *, extra_secrets: Iterable[str] = ()) -> list[str]:
    """Коды нарушений для строки (пусто — гейт пройден). Значения не возвращаются."""

    found: set[str] = set()
    # NFKC сворачивает fullwidth/совместимые символы; Cf (zero-width) удаляются.
    text = "".join(ch for ch in unicodedata.normalize("NFKC", text) if unicodedata.category(ch) != "Cf")
    stripped = _ISO_DATETIME.sub(" ", text)
    # Любой «@» обязан быть частью точной формы local@example.com.
    if "@" in _EMAIL_OK.sub("", text):
        found.add("email")
    if _PHONE.search(stripped):
        found.add("phone")
    for code, pattern in _HARD_PATTERNS.items():
        if pattern.search(text):
            found.add(code)
    secrets = (*GLOBAL_CANARIES, *extra_secrets)
    if any(s and s in text for s in secrets):
        found.add("known_secret")
    return sorted(found)


def scan_value(node: Any, *, extra_secrets: Iterable[str] = (), _depth: int = 0) -> list[str]:
    """Рекурсивный гейт над JSON-значением (включая ключи) + лимиты формы."""

    if _depth > _MAX_DEPTH:
        return ["too_deep"]
    found: set[str] = set()
    if isinstance(node, dict) and len(node) > _MAX_KEYS:
        found.add("too_many_keys")
    if isinstance(node, str):
        if len(node) > _MAX_ARG_STRING:
            found.add("string_too_long")
        found.update(scan_text(node, extra_secrets=extra_secrets))
    elif isinstance(node, dict):
        for key, value in node.items():
            found.update(scan_text(str(key), extra_secrets=extra_secrets))
            found.update(scan_value(value, extra_secrets=extra_secrets, _depth=_depth + 1))
    elif isinstance(node, list):
        for value in node:
            found.update(scan_value(value, extra_secrets=extra_secrets, _depth=_depth + 1))
    elif isinstance(node, bool) or node is None:
        pass
    elif isinstance(node, int):
        if abs(node) >= _MAX_INT:
            found.add("large_number")
    elif isinstance(node, float):
        if not -_MAX_INT < node < _MAX_INT or node != node:
            found.add("large_number")
    else:
        found.add("unsupported_type")
    return sorted(found)


def scan_recording(recording: Recording, *, extra_secrets: Iterable[str] = ()) -> list[str]:
    """Повторный гейт над всеми сохраняемыми строками (в т.ч. при загрузке)."""

    found: set[str] = set()
    for field_value in (recording.model, recording.recording_id, recording.scenario_id):
        found.update(scan_text(field_value, extra_secrets=extra_secrets))
    for interaction in recording.interactions:
        resp = interaction.response
        if len(resp.text) > _MAX_TEXT:
            found.add("string_too_long")
        found.update(scan_text(resp.text, extra_secrets=extra_secrets))
        for call in resp.tool_calls:
            found.update(scan_text(call.name, extra_secrets=extra_secrets))
            found.update(scan_value(call.arguments, extra_secrets=extra_secrets))
            if call.raw_arguments is not None:
                found.update(scan_text(call.raw_arguments, extra_secrets=extra_secrets))
    return sorted(found)


# --- проекция и fingerprint запроса ----------------------------------------------
@dataclass
class ReferenceMap:
    """Нормализация id провайдера в порядковые ссылки.

    id ответа и call_id произвольны и зависят от провайдера: в запись и fingerprint
    попадает только позиция (sequence, index) того ответа, на который ссылается запрос.
    """

    responses: dict[str, int] = field(default_factory=dict)
    calls: dict[str, tuple[int, int]] = field(default_factory=dict)

    def register(self, sequence: int, response_id: Any, call_ids: Iterable[Any]) -> None:
        if isinstance(response_id, str):
            self.responses[response_id] = sequence
        for index, call_id in enumerate(call_ids):
            if isinstance(call_id, str):
                self.calls[call_id] = (sequence, index)


@dataclass
class ProjectedRequest:
    projection: dict[str, Any]
    texts: list[str] = field(default_factory=list)  # всё, что хешируется (для гейта)
    user_texts: list[str] = field(default_factory=list)  # role=user / голый str input
    unsupported: list[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return "sha256:" + sha256_hex(canonical_json(self.projection))


_KNOWN_KEYS = frozenset({"model", "input", "tools", "previous_response_id"})


def _text_ref(text: str) -> dict[str, str]:
    return {"sha256": sha256_hex(text)}


def project_request(kwargs: dict[str, Any], refs: ReferenceMap) -> ProjectedRequest:
    """Детерминированная проекция запроса `responses.create(**kwargs)`.

    Учитывает только безопасные данные: модель, наличие/состав схем инструментов,
    ссылку на предыдущий ответ (порядковую), роли и хеши текстов, ссылки на tool-вызовы.
    """

    out = ProjectedRequest(projection={"v": 1})
    proj = out.projection

    model = kwargs.get("model")
    proj["model"] = model if isinstance(model, str) else None
    if not isinstance(model, str):
        out.unsupported.append("model")

    prev = kwargs.get("previous_response_id")
    if prev is None:
        proj["previous"] = None
    elif isinstance(prev, str) and prev in refs.responses:
        proj["previous"] = refs.responses[prev]
    else:
        proj["previous"] = "unknown"
        out.unsupported.append("unknown_previous_response")

    tools = kwargs.get("tools")
    if tools is None:
        proj["tools"] = None
    elif isinstance(tools, list) and all(isinstance(t, dict) for t in tools):
        # Схемы инструментов — статическая конфигурация агента (не данные пользователя).
        proj["tools"] = [{"name": str(t.get("name")), "sha256": sha256_hex(canonical_json(t))} for t in tools]
    else:
        proj["tools"] = "unsupported"
        out.unsupported.append("tools")

    payload = kwargs.get("input")
    if isinstance(payload, str):
        proj["input"] = {"text": _text_ref(payload)}
        out.texts.append(payload)
        out.user_texts.append(payload)
    elif isinstance(payload, list):
        items: list[dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                call_id, output = item.get("call_id"), item.get("output")
                ref = refs.calls.get(call_id) if isinstance(call_id, str) else None
                if ref is None or not isinstance(output, str):
                    out.unsupported.append("tool_output")
                    items.append({"type": "function_call_output", "ref": "unknown"})
                    continue
                items.append({"type": "function_call_output", "ref": list(ref), "output": _text_ref(output)})
                out.texts.append(output)
            elif isinstance(item, dict) and item.get("role") in ("developer", "user", "system") and isinstance(
                item.get("content"), str
            ):
                items.append({"role": item["role"], "content": _text_ref(item["content"])})
                out.texts.append(item["content"])
                if item["role"] == "user":
                    out.user_texts.append(item["content"])
            else:
                out.unsupported.append("input_item")
                items.append({"unsupported": True})
        proj["input"] = items
    else:
        proj["input"] = "unsupported"
        out.unsupported.append("input")

    # Любой параметр сверх известных меняет поведение запроса и обязан быть виден.
    extra = {k: kwargs[k] for k in sorted(set(kwargs) - _KNOWN_KEYS)}
    if extra:
        if all(v is None or isinstance(v, (bool, int, float, str)) for v in extra.values()):
            proj["extra"] = {k: _text_ref(canonical_json(v)) for k, v in extra.items()}
            out.texts.extend(v for v in extra.values() if isinstance(v, str))
        else:
            proj["extra"] = "unsupported"
            out.unsupported.append("extra_params")
    return out


# --- загрузка / сохранение ------------------------------------------------------
def parse_recording(raw: bytes | str) -> Recording:
    """Строгий разбор: версия -> форма -> целостность -> статус -> гейт. Fail-closed."""

    if len(raw) > _MAX_FILE_BYTES:
        raise RecordingFormatError("too_large")

    def _no_duplicates(pairs):
        keys = [k for k, _ in pairs]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate_key")
        return dict(pairs)

    def _no_constants(_name):
        raise ValueError("non_finite_number")

    try:
        data = json.loads(raw, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise RecordingFormatError("corrupt_json") from exc
    if not isinstance(data, dict):
        raise RecordingFormatError("not_an_object")

    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise RecordingFormatError("missing_schema_version")
    if version != RECORDING_SCHEMA_VERSION:
        raise UnsupportedSchemaVersion(detail=f"found={version} supported={RECORDING_SCHEMA_VERSION}")

    try:
        recording = Recording.model_validate(data)
    except ValidationError as exc:
        # Только расположение полей: значения (возможно, чувствительные) не раскрываем.
        locs = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise RecordingFormatError("schema_violation", ",".join(locs)[:200]) from exc

    if compute_integrity(data) != recording.integrity:
        raise RecordingFormatError("integrity_mismatch")
    if recording.status != "complete":
        raise RecordingFormatError("incomplete_recording")
    violations = scan_recording(recording)
    if violations:
        raise RecordingFormatError("sensitive_content", ",".join(violations))
    return recording


def load_recording(path: Path) -> Recording:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise RecordingFormatError("unreadable") from exc
    return parse_recording(raw)


def save_recording(recording: Recording, path: Path) -> None:
    """Атомарная запись: либо полный валидный файл, либо ничего (без хвостов)."""

    if recording.status != "complete":
        raise RecordingRefused("incomplete_recording")
    violations = scan_recording(recording)
    if violations:
        raise RecordingRefused("sensitive_content", ",".join(violations))
    text = render_recording(recording)
    parse_recording(text)  # round-trip: сохраняем только то, что сможем загрузить

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".rec-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def recording_filename(scenario_id: str) -> str:
    return f"{scenario_id}.recording.json"


def load_recordings(directory: Path) -> dict[str, Recording]:
    """Все `*.recording.json` каталога по scenario_id. Любая ошибка — исключение."""

    result: dict[str, Recording] = {}
    for path in sorted(Path(directory).glob("*.recording.json")):
        recording = load_recording(path)
        if recording.scenario_id in result:
            raise RecordingFormatError("duplicate_scenario_recording", recording.scenario_id)
        if path.name != recording_filename(recording.scenario_id):
            raise RecordingFormatError("filename_mismatch")
        result[recording.scenario_id] = recording
    return result
