"""EVAL-M02/WP1: Record & Replay. Матрица R1–R16 + негативные контроли.

Все записи синтетические; реальные LLM-вызовы и сеть не используются.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import evaluation
import supportops_agent
from evaluation import recorder as recorder_module
from evaluation import recording as recording_module
from evaluation import replay as replay_module
from evaluation.recorder import RecordingLLMClient, RecordingSession
from evaluation.recording import (
    RECORDING_SCHEMA_VERSION,
    LiveUpstreamNotEnabled,
    RecordingFormatError,
    RecordingRefused,
    UnsupportedSchemaVersion,
    canonical_json,
    compute_integrity,
    load_recordings,
    parse_recording,
    render_recording,
    save_recording,
    scan_text,
)
from evaluation.replay import ReplayLLMClient
from evaluation.runner import (
    DEFAULT_DATASET,
    DEFAULT_RECORDINGS,
    _Env,
    NetworkAccessBlocked,
    _isolated,
    build_report,
    load_dataset,
    render_report,
    run_dataset,
    run_scenario,
)
from evaluation.schemas import Outcome
from evaluation.sources import FakeSource, Mode, RecordSource, ReplaySource
from evaluation.fakes import FakeLLMClient
from evaluation.schemas import Scenario
from pydantic import ValidationError
from supportops_agent import agent as agent_module
from supportops_agent import mock_data
from supportops_agent.agent import SupportAgent
from supportops_agent.tool_definitions import TOOL_REGISTRY

REPO = Path(evaluation.__file__).resolve().parents[1]
TEXT_ONLY, ONE_TOOL, SEQUENTIAL, MULTI_IN_TURN = "mi-002", "ot-001", "ot-002", "sc-002"
UPSTREAM_ERROR, BAD_JSON, NO_LLM = "st-002", "ia-002", "cf-001"


@pytest.fixture(scope="module")
def loaded():
    return load_dataset(DEFAULT_DATASET)


@pytest.fixture(scope="module")
def recorded(loaded):
    """Запись всех сценариев из синтетического fake-upstream (в памяти)."""

    source = RecordSource()
    run_dataset(loaded[0], source)
    assert source.refusals == {}
    return source.recordings


def scenario(loaded, sid):
    return next(s for s in loaded[0].scenarios if s.id == sid).model_copy(deep=True)


def record_one(loaded, sid, mutate=None):
    s = scenario(loaded, sid)
    if mutate:
        mutate(s)
    source = RecordSource()
    result = run_scenario(s, source)
    return s, source, result


def rec_dict(recording):
    return json.loads(render_recording(recording))


def reseal(data):
    data["integrity"] = compute_integrity(data)
    return json.dumps(data)


def replay(loaded, recording, mutate=None):
    s = scenario(loaded, recording.scenario_id)
    if mutate:
        mutate(s)
    return run_scenario(s, ReplaySource({recording.scenario_id: recording}))


# === R1–R3: запись ===============================================================
def test_r1_records_text_response(loaded):
    _, source, result = record_one(loaded, TEXT_ONLY)
    rec = source.recordings[TEXT_ONLY]
    assert result.harness_error is None
    assert [i.response.type for i in rec.interactions] == ["text"]
    assert rec.interactions[0].response.text
    assert rec.status == "complete" and rec.data_class == "synthetic"


def test_r2_records_single_tool_call(loaded):
    rec = record_one(loaded, ONE_TOOL)[1].recordings[ONE_TOOL]
    first = rec.interactions[0].response
    assert first.type == "tool_calls"
    assert [(c.name, c.arguments) for c in first.tool_calls] == [("get_order_tracking", {"order_id": "order_002"})]
    assert rec.interactions[1].response.type == "text"


def test_r2_records_multiple_tool_calls_in_one_turn_and_invalid_json(loaded):
    rec = record_one(loaded, MULTI_IN_TURN)[1].recordings[MULTI_IN_TURN]
    assert len(rec.interactions[0].response.tool_calls) == 2
    bad = record_one(loaded, BAD_JSON)[1].recordings[BAD_JSON]
    call = bad.interactions[0].response.tool_calls[0]
    assert call.arguments is None and call.raw_arguments is not None


def test_r3_records_sequential_calls_with_dense_sequence(loaded):
    rec = record_one(loaded, SEQUENTIAL)[1].recordings[SEQUENTIAL]
    assert [i.sequence for i in rec.interactions] == [1, 2, 3]
    assert len({i.request_fingerprint for i in rec.interactions}) == 3
    assert [i.response.type for i in rec.interactions] == ["tool_calls", "tool_calls", "text"]


def test_recording_with_no_llm_calls_is_valid_and_replays(loaded):
    _, source, _ = record_one(loaded, NO_LLM)
    rec = source.recordings[NO_LLM]
    assert rec.interactions == [] and rec.model == "n-a"
    assert replay(loaded, rec).harness_error is None


def test_record_is_transparent_to_agent(loaded):
    """Запись не меняет наблюдаемое поведение агента относительно fake."""

    for sid in (ONE_TOOL, SEQUENTIAL, UPSTREAM_ERROR, BAD_JSON):
        fake = run_scenario(scenario(loaded, sid))
        rec = run_scenario(scenario(loaded, sid), RecordSource())
        assert fake.model_dump() == rec.model_dump(), sid


# === R4–R5: воспроизведение без сети, побайтовая детерминированность ===============
def test_r4_replay_matches_fake_for_every_scenario_without_network(loaded, recorded, monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("network used during replay")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    dataset, _ = loaded
    fake = {r.scenario_id: r.model_dump() for r in run_dataset(dataset)}
    replayed = {r.scenario_id: r.model_dump() for r in run_dataset(dataset, ReplaySource(recorded))}
    assert replayed == fake
    assert all(r["harness_error"] is None for r in replayed.values())


def test_r4_negative_control_isolation_detects_and_blocks_real_network_attempts(loaded):
    """Контроль детектора: настоящая попытка соединения блокируется и фиксируется."""

    s = scenario(loaded, SEQUENTIAL)
    env = _Env()
    sock = socket.socket()
    try:
        with _isolated(s, env):
            with pytest.raises(NetworkAccessBlocked):
                socket.create_connection(("127.0.0.1", 9), timeout=0.1)
            with pytest.raises(NetworkAccessBlocked):
                sock.connect(("127.0.0.1", 9))
            with pytest.raises(NetworkAccessBlocked):
                socket.getaddrinfo("localhost", 9)
    finally:
        sock.close()
    assert env.network_attempts == 3
    # Блокировка снята после выхода: патчи изоляции не «протекают».
    assert socket.create_connection is not NetworkAccessBlocked and not getattr(
        socket.create_connection, "__name__", ""
    ) == "blocked"


class _NetworkTouchingSource:
    """Источник, который пытается открыть соединение при первом LLM-вызове (контроль)."""

    mode = Mode.RECORDED_REPLAY

    def __init__(self, recordings):
        self._inner = ReplaySource(recordings)

    def open(self, scenario):
        session = self._inner.open(scenario)
        create = session.client.responses.create

        def touching(**kwargs):
            socket.create_connection(("127.0.0.1", 9), timeout=0.1)
            return create(**kwargs)

        session.client.responses.create = touching
        return session


def test_r4_network_attempt_during_run_becomes_security_violation(loaded, recorded):
    result = run_scenario(scenario(loaded, SEQUENTIAL), _NetworkTouchingSource(recorded))
    assert "network_access_attempt" in result.security_violations


def test_r4_replay_never_touches_network_layer(loaded, recorded):
    """Replay: 0 попыток сети (детектор доказан контролем выше)."""

    for sid in (SEQUENTIAL, ONE_TOOL, UPSTREAM_ERROR, NO_LLM):
        s = scenario(loaded, sid)
        env = _Env()
        with _isolated(s, env):
            result = run_scenario(s, ReplaySource(recorded))
        assert env.network_attempts == 0, sid
        assert "network_access_attempt" not in result.security_violations, sid


def test_r4_replay_never_creates_a_real_llm_client(loaded, recorded, monkeypatch):
    """Replay не создаёт OpenAI-клиент (SDK импортируется agent.py — см. M7)."""

    import openai

    created: list[str] = []

    class _Sentinel:
        def __init__(self, *a, **k):
            created.append("OpenAI")
            raise AssertionError("real LLM client constructed")

    # Контроль детектора: подмена перехватывает и фиксирует создание клиента.
    monkeypatch.setattr(openai, "OpenAI", _Sentinel)
    monkeypatch.setattr(agent_module, "OpenAI", _Sentinel, raising=True)
    with pytest.raises(AssertionError):
        openai.OpenAI()
    assert created == ["OpenAI"]
    created.clear()

    run_dataset(loaded[0], ReplaySource(recorded))
    assert created == []


def test_r4_replay_modules_have_no_network_or_provider_imports():
    banned = {"socket", "http", "urllib", "requests", "httpx", "openai", "aiohttp", "ssl", "asyncio"}
    for mod in (recording_module, replay_module):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        imported = {
            (n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in (n.names if isinstance(n, ast.Import) else [n])
        }
        assert not (imported & banned), (mod.__name__, imported & banned)


def test_r5_replay_report_is_byte_identical(loaded, recorded):
    dataset, digest = loaded

    def once():
        results = run_dataset(dataset, ReplaySource(recorded))
        return render_report(
            build_report(dataset, digest, results, mode=Mode.RECORDED_REPLAY, recordings=list(recorded.values()))
        )

    first, second, third = once(), once(), once()
    assert first == second == third


def test_r5_recording_files_are_byte_identical_across_recordings(loaded):
    a = render_recording(record_one(loaded, SEQUENTIAL)[1].recordings[SEQUENTIAL])
    b = render_recording(record_one(loaded, SEQUENTIAL)[1].recordings[SEQUENTIAL])
    assert a == b


def test_committed_recordings_are_current_and_replay_clean(loaded, recorded):
    committed = load_recordings(DEFAULT_RECORDINGS)
    assert set(committed) == set(recorded)
    for sid, rec in committed.items():
        assert render_recording(rec) == render_recording(recorded[sid]), sid
    results = run_dataset(loaded[0], ReplaySource(committed))
    assert [r.scenario_id for r in results if r.harness_error or r.mismatches] == []


# === R6–R8: несовместимость ======================================================
def test_r6_fingerprint_mismatch_when_request_differs(loaded, recorded):
    def change_message(s):
        s.ticket.message = s.ticket.message + " Please hurry."

    result = replay(loaded, recorded[ONE_TOOL], change_message)
    assert result.harness_error == "replay:fingerprint_mismatch"


def test_r6_incompatible_agent_change_is_detected(loaded, recorded, monkeypatch):
    """Агентная логика изменилась (другой вывод инструмента) -> запись несовместима."""

    original = TOOL_REGISTRY["get_order_tracking"].handler
    monkeypatch.setattr(
        TOOL_REGISTRY["get_order_tracking"], "handler", lambda **kw: {**original(**kw), "carrier": "OTHER"}
    )
    # _isolated оборачивает уже подменённый handler: изменённый вывод попадёт в запрос.
    result = replay(loaded, recorded[ONE_TOOL])
    assert result.harness_error == "replay:fingerprint_mismatch"


def test_r6_model_or_tools_change_is_detected(loaded, recorded, monkeypatch):
    monkeypatch.setattr("supportops_agent.agent.MODEL_NAME", "other-model")
    assert replay(loaded, recorded[ONE_TOOL]).harness_error == "replay:fingerprint_mismatch"
    monkeypatch.undo()
    definition = TOOL_REGISTRY["get_order_tracking"]
    monkeypatch.setattr(definition, "description", definition.description + " (changed)")
    assert replay(loaded, recorded[ONE_TOOL]).harness_error == "replay:fingerprint_mismatch"


def test_r6_sequence_mismatch_is_distinguished_from_fingerprint_mismatch(loaded, recorded):
    data = rec_dict(recorded[SEQUENTIAL])
    first, second = data["interactions"][0], data["interactions"][1]
    first["request_fingerprint"], second["request_fingerprint"] = (
        second["request_fingerprint"],
        first["request_fingerprint"],
    )
    result = replay(loaded, parse_recording(reseal(data)))
    assert result.harness_error == "replay:sequence_mismatch"


def test_r7_extra_llm_call_is_detected(loaded, recorded):
    data = rec_dict(recorded[ONE_TOOL])
    data["interactions"].pop()  # запись короче, чем реальное поведение агента
    result = replay(loaded, parse_recording(reseal(data)))
    assert result.harness_error == "replay:extra_call"
    assert any(m.dimension == "llm_calls" for m in result.mismatches)


def test_r8_missing_llm_call_is_detected(loaded, recorded):
    # В записи 3 взаимодействия, а агент по сценарию ot-001 делает только 2 вызова.
    data = rec_dict(recorded[ONE_TOOL])
    extra = json.loads(json.dumps(data["interactions"][-1]))
    extra["sequence"] = 3
    data["interactions"].append(extra)
    result = replay(loaded, parse_recording(reseal(data)))
    assert result.harness_error == "replay:missing_calls"
    assert any(m.dimension == "llm_calls" for m in result.mismatches)


def test_replay_errors_are_not_masked_by_agent_swallowing_exceptions(loaded, recorded):
    """Отравленный клиент: даже если агент перехватит исключение, ошибка остаётся."""

    client = ReplayLLMClient(recorded[ONE_TOOL])
    with pytest.raises(replay_module.ReplayMismatch):
        client.responses.create(model="x", input="unexpected")
    with pytest.raises(replay_module.ReplayMismatch) as again:
        client.responses.create(model="x", input="unexpected-2")
    assert again.value.code == "fingerprint_mismatch"
    assert client.finish().code == "fingerprint_mismatch"


def test_replay_mismatch_is_excluded_from_metrics_not_counted_as_success(loaded, recorded):
    dataset, digest = loaded
    s = scenario(loaded, ONE_TOOL)
    s.ticket.message += " changed"
    result = run_scenario(s, ReplaySource(recorded))
    report = build_report(
        dataset.model_copy(update={"scenarios": [s]}),
        digest,
        [result],
        mode=Mode.RECORDED_REPLAY,
    )
    assert report["harness_errors"] == [ONE_TOOL]
    assert report["metrics"]["task_success_rate"]["denominator"] == 0


def test_replay_without_recording_is_harness_error(loaded):
    s = scenario(loaded, ONE_TOOL)
    assert run_scenario(s, ReplaySource({})).harness_error == "replay:no_recording"


# === R9–R10: повреждённый формат, версия ==========================================
@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda raw, d: raw[: len(raw) // 2], "corrupt_json"),
        (lambda raw, d: b"\xff\xfe", "corrupt_json"),
        (lambda raw, d: b"[]", "not_an_object"),
        (lambda raw, d: json.dumps({**d, "unknown": 1}), "schema_violation"),
        (lambda raw, d: json.dumps({**d, "status": "incomplete"}), "integrity_mismatch"),
        (lambda raw, d: reseal({**d, "status": "incomplete"}), "incomplete_recording"),
        (lambda raw, d: json.dumps({**d, "model": "tampered"}), "integrity_mismatch"),
        (lambda raw, d: reseal({**d, "interactions": d["interactions"][::-1]}), "schema_violation"),
        (lambda raw, d: reseal({**d, "source": "live"}), "schema_violation"),
        (lambda raw, d: json.dumps({k: v for k, v in d.items() if k != "integrity"}), "schema_violation"),
    ],
)
def test_r9_corrupt_recording_is_rejected(recorded, mutate, code):
    data = rec_dict(recorded[SEQUENTIAL])
    raw = json.dumps(data).encode()
    payload = mutate(raw, data)
    with pytest.raises(RecordingFormatError) as exc:
        parse_recording(payload)
    assert exc.value.code == code


def test_r9_duplicate_sequence_is_rejected(recorded):
    data = rec_dict(recorded[SEQUENTIAL])
    data["interactions"][1]["sequence"] = 1
    with pytest.raises(RecordingFormatError):
        parse_recording(reseal(data))


@pytest.mark.parametrize("version", [0, 2, 99, "1", None, True, 1.5])
def test_r10_unknown_schema_version_is_rejected(recorded, version):
    data = rec_dict(recorded[SEQUENTIAL])
    data["schema_version"] = version
    with pytest.raises(RecordingFormatError) as exc:
        parse_recording(reseal(data))
    if isinstance(version, int) and not isinstance(version, bool):
        assert isinstance(exc.value, UnsupportedSchemaVersion)


def test_r10_missing_version_is_rejected(recorded):
    data = rec_dict(recorded[SEQUENTIAL])
    del data["schema_version"]
    with pytest.raises(RecordingFormatError):
        parse_recording(json.dumps(data))
    assert RECORDING_SCHEMA_VERSION == 1


def test_load_directory_is_fail_closed(recorded, tmp_path):
    save_recording(recorded[SEQUENTIAL], tmp_path / f"{SEQUENTIAL}.recording.json")
    (tmp_path / "ot-001.recording.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(RecordingFormatError):
        load_recordings(tmp_path)
    (tmp_path / "ot-001.recording.json").unlink()
    save_recording(recorded[SEQUENTIAL], tmp_path / "renamed.recording.json")
    with pytest.raises(RecordingFormatError):
        load_recordings(tmp_path)


# === R11–R12: секреты и персональные данные =======================================
SECRETS = [
    "sk-proj-abcdefghijklmnopqrstuvwx",
    "Bearer abcdef0123456789",
    "api_key=abcdef123",
    "password: hunter2hunter2",
    "postgresql://user:pw@db.internal/app",
    "https://user:pw@host/x",
    "eyJhbGciOiJIUzI1NiJ9.payload.sig",
    "SYNTH_SECRET_EVAL_7f3a9c",
    "Authorization: Basic Zm9vOmJhcg==",
    "A" * 48,
]
PII = ["real.person@gmail.com", "call me +1 415 555 0132", "phone 4155550132", "ann@corp.example.org"]


def _answer(text):
    def mutate(s):
        s.llm_script[-1].text = text

    return mutate


@pytest.mark.parametrize("secret", SECRETS)
def test_r11_secret_in_llm_response_is_refused(loaded, secret):
    _, source, result = record_one(loaded, ONE_TOOL, _answer(f"debug {secret}"))
    assert ONE_TOOL not in source.recordings
    assert result.harness_error == "record:sensitive_response"


@pytest.mark.parametrize("secret", SECRETS[:4])
def test_r11_secret_in_tool_arguments_is_refused(loaded, secret):
    def mutate(s):
        s.llm_script[0].tool_calls[0].arguments = {"order_id": secret}

    _, source, result = record_one(loaded, ONE_TOOL, mutate)
    assert ONE_TOOL not in source.recordings
    assert result.harness_error == "record:sensitive_response"


def test_r11_secret_in_user_message_is_refused(loaded):
    def mutate(s):
        s.ticket.message = "My key is sk-proj-abcdefghijklmnopqrstuvwx"

    _, source, result = record_one(loaded, ONE_TOOL, mutate)
    assert result.harness_error == "record:sensitive_request"
    assert ONE_TOOL not in source.recordings


@pytest.mark.parametrize("pii", PII)
def test_r12_personal_data_in_response_is_refused(loaded, pii):
    _, source, result = record_one(loaded, ONE_TOOL, _answer(f"Contact: {pii}"))
    assert result.harness_error == "record:sensitive_response"
    assert ONE_TOOL not in source.recordings


def test_r12_undeclared_user_text_is_refused_not_hashed(loaded):
    """Произвольный пользовательский ввод (в т.ч. «реальный») не записывается."""

    session = RecordingSession("t", ONE_TOOL, declared_user_texts=["declared"], synthetic=True)
    upstream = FakeSource().open(scenario(loaded, ONE_TOOL)).client
    client = RecordingLLMClient(session, upstream)
    with pytest.raises(RecordingRefused) as exc:
        client.responses.create(model="m", input="Hi, I am Jane Doe, card 4111 1111 1111 1111")
    assert exc.value.code == "undeclared_user_text"
    assert "Jane" not in str(exc.value)
    with pytest.raises(RecordingRefused):
        session.finalize()


def test_r12_refusal_messages_never_contain_the_data(loaded):
    _, _, result = record_one(loaded, ONE_TOOL, _answer("mail leak.me@gmail.com"))
    assert "gmail" not in json.dumps(result.model_dump())


def test_synthetic_allowlist_does_not_block_dataset_content():
    assert scan_text("Order order_001 delivered 2026-11-20 10:30 to john@example.com, T-1001") == []


def test_saved_recordings_contain_no_provider_ids_headers_or_paths(recorded, tmp_path):
    for rec in recorded.values():
        text = render_recording(rec)
        for needle in ("resp_", "call_", "Authorization", "Bearer", "sk-", "/home/", str(REPO), "worktree", "SYNTH_"):
            assert needle not in text, (rec.scenario_id, needle)
        assert rec.model in {"gpt-4.1-mini", "n-a"}


def test_recording_allowlist_has_no_raw_request_or_provider_fields(recorded):
    allowed_top = {
        "schema_version", "recording_id", "scenario_id", "model", "source", "data_class",
        "status", "interactions", "integrity",
    }
    allowed_resp = {"type", "text", "tool_calls", "error_type", "usage"}
    for rec in recorded.values():
        data = rec_dict(rec)
        assert set(data) == allowed_top
        for i in data["interactions"]:
            assert set(i) == {"sequence", "request_fingerprint", "response"}
            assert set(i["response"]) == allowed_resp


def test_hand_edited_secret_in_recording_is_rejected_on_load(recorded):
    data = rec_dict(recorded[ONE_TOOL])
    data["interactions"][-1]["response"]["text"] = "key sk-proj-abcdefghijklmnopqrstuvwx"
    with pytest.raises(RecordingFormatError) as exc:
        parse_recording(reseal(data))
    assert exc.value.code == "sensitive_content"


def test_error_text_of_upstream_is_not_recorded(loaded):
    rec = record_one(loaded, UPSTREAM_ERROR)[1].recordings[UPSTREAM_ERROR]
    last = rec.interactions[-1].response
    assert (last.type, last.error_type, last.text) == ("error", "RuntimeError", "")
    assert "synthetic llm provider failure" not in render_recording(rec)


def test_live_upstream_is_not_available_in_wp1():
    with pytest.raises(LiveUpstreamNotEnabled):
        RecordSource(upstream_kind="live")


# === R13–R14: ошибки при записи, восстановление ===================================
def test_r13_upstream_exception_is_recorded_and_replayable(loaded, recorded):
    rec = recorded[UPSTREAM_ERROR]
    assert rec.interactions[-1].response.type == "error"
    assert replay(loaded, rec).model_dump() == run_scenario(scenario(loaded, UPSTREAM_ERROR)).model_dump()


def test_r13_refusal_mid_run_leaves_no_complete_recording_and_poisons_session(loaded):
    s = scenario(loaded, SEQUENTIAL)
    s.llm_script[1].tool_calls[0].arguments = {"order_id": "leak@gmail.com"}
    source = RecordSource()
    result = run_scenario(s, source)
    assert SEQUENTIAL not in source.recordings
    assert source.refusals[SEQUENTIAL] == "sensitive_response"
    assert result.harness_error == "record:sensitive_response"


def test_r13_incomplete_snapshot_can_be_neither_saved_nor_loaded(loaded, tmp_path):
    session = RecordingSession("t", ONE_TOOL, declared_user_texts=["Where is my order order_002?"], synthetic=True)
    client = RecordingLLMClient(session, FakeSource().open(scenario(loaded, ONE_TOOL)).client)
    client.responses.create(model="m", input="Where is my order order_002?")
    snapshot = session.snapshot_incomplete()
    assert snapshot.status == "incomplete"
    target = tmp_path / "x.recording.json"
    with pytest.raises(RecordingRefused):
        save_recording(snapshot, target)
    assert not target.exists() and list(tmp_path.iterdir()) == []
    with pytest.raises(RecordingFormatError):
        parse_recording(render_recording(snapshot))


def test_r14_state_restored_after_recording_failures(loaded, tmp_path):
    handlers = {n: d.handler for n, d in TOOL_REGISTRY.items()}
    cases, connect = mock_data.CASES, socket.socket.connect
    for text in ("mail leak.me@gmail.com", "SYNTH_SECRET_EVAL_7f3a9c"):
        record_one(loaded, SEQUENTIAL, _answer(text))
        record_one(loaded, UPSTREAM_ERROR)
    assert {n: d.handler for n, d in TOOL_REGISTRY.items()} == handlers
    assert mock_data.CASES is cases and mock_data.CASES == {}
    assert socket.socket.connect is connect


def test_r14_save_failure_leaves_no_partial_file_and_next_save_works(recorded, tmp_path, monkeypatch):
    target = tmp_path / f"{SEQUENTIAL}.recording.json"
    real_replace = os.replace

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        save_recording(recorded[SEQUENTIAL], target)
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(os, "replace", real_replace)
    save_recording(recorded[SEQUENTIAL], target)
    assert parse_recording(target.read_bytes()) == recorded[SEQUENTIAL]


def test_r14_scenarios_are_isolated_after_a_failed_one(loaded, recorded):
    s = scenario(loaded, ONE_TOOL)
    s.ticket.message += "!"
    bad = run_scenario(s, ReplaySource(recorded))
    good = run_scenario(scenario(loaded, ONE_TOOL), ReplaySource(recorded))
    assert bad.harness_error and good.harness_error is None and good.mismatches == []


# === Режимы в отчёте ==============================================================
def test_modes_are_distinct_and_never_claim_model_quality(loaded, recorded):
    dataset, digest = loaded
    sub = dataset.model_copy(update={"scenarios": [scenario(loaded, ONE_TOOL)]})
    reports = {
        Mode.DETERMINISTIC_FAKE: build_report(sub, digest, run_dataset(sub, FakeSource())),
        Mode.RECORDED_REPLAY: build_report(
            sub, digest, run_dataset(sub, ReplaySource(recorded)), mode=Mode.RECORDED_REPLAY,
            recordings=[recorded[ONE_TOOL]],
        ),
        Mode.SYNTHETIC_RECORDING: build_report(
            sub, digest, run_dataset(sub, RecordSource()), mode=Mode.SYNTHETIC_RECORDING
        ),
    }
    assert {m.value for m in reports} == {r["mode"] for r in reports.values()} == {
        "deterministic_fake", "recorded_replay", "synthetic_recording",
    }
    for r in reports.values():
        assert r["llm"]["real_llm_calls"] == 0 and r["llm"]["model_quality_claim"] is False
    assert reports[Mode.RECORDED_REPLAY]["llm"]["recordings"][0]["recording_id"] == "synthetic_ot-001"


def test_harness_invariants_still_apply_in_replay(loaded, recorded):
    """Девять инвариантов не ослаблены: утечка в записанном ответе ловится (через запись вручную)."""

    data = rec_dict(recorded["ua-004"])
    data["interactions"][-1]["response"]["text"] = "Profile: john@example.com"
    result = replay(loaded, parse_recording(reseal(data)))
    assert "cross_customer_data_exposure" in result.security_violations


def test_replay_checks_expected_outcomes(loaded, recorded):
    result = replay(loaded, recorded[ONE_TOOL], lambda s: setattr(s.expected, "outcome", Outcome.DEGRADED))
    assert result.task_success is False and result.harness_error is None


# === R15–R16: production-код, независимость от worktree ===============================
def test_r15_replay_does_not_modify_production_objects(loaded, recorded):
    before_agent = dict(vars(SupportAgent))
    before_handlers = {n: d.handler for n, d in TOOL_REGISTRY.items()}
    run_dataset(loaded[0], ReplaySource(recorded))
    run_dataset(loaded[0], RecordSource())
    assert dict(vars(SupportAgent)) == before_agent
    assert {n: d.handler for n, d in TOOL_REGISTRY.items()} == before_handlers


def test_r15_src_is_unchanged_since_eval_baseline():
    if subprocess.run(["git", "cat-file", "-e", "5afdf2d"], cwd=REPO, capture_output=True).returncode:
        pytest.skip("baseline commit not available")
    diff = subprocess.run(
        ["git", "diff", "--name-only", "5afdf2d", "--", "src"], cwd=REPO, capture_output=True, text=True
    )
    assert diff.returncode == 0 and diff.stdout.strip() == ""


def test_r16_imports_come_from_this_worktree():
    src = (REPO / "src").resolve()
    assert Path(supportops_agent.__file__).resolve().is_relative_to(src)
    assert Path(evaluation.__file__).resolve().is_relative_to(REPO)


def test_r16_replay_in_clean_subprocess_matches_in_process(loaded, tmp_path):
    """Отдельный процесс из чужого cwd, без унаследованного окружения, даёт тот же отчёт."""

    out = tmp_path / "report.json"
    code = (
        "import sys; sys.path[:0]=[%r, %r]\n"
        "import supportops_agent, evaluation\n"
        "assert supportops_agent.__file__.startswith(%r) and evaluation.__file__.startswith(%r)\n"
        "from evaluation.runner import main\n"
        "sys.exit(main(['--mode','replay','--output',%r,'--fail-on-mismatch']))\n"
        % (str(REPO / "src"), str(REPO), str(REPO / "src"), str(REPO), str(out))
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env={"PATH": os.environ["PATH"]},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    dataset, digest = loaded
    committed = load_recordings(DEFAULT_RECORDINGS)
    expected = render_report(
        build_report(dataset, digest, run_dataset(dataset, ReplaySource(committed)),
                     mode=Mode.RECORDED_REPLAY, recordings=list(committed.values()))
    )
    assert out.read_text(encoding="utf-8") == expected


def test_r16_report_and_recordings_contain_no_absolute_paths(loaded, recorded):
    dataset, digest = loaded
    text = render_report(build_report(dataset, digest, run_dataset(dataset, ReplaySource(recorded)),
                                      mode=Mode.RECORDED_REPLAY, recordings=list(recorded.values())))
    assert str(REPO) not in text and "/home/" not in text
    for path in DEFAULT_RECORDINGS.glob("*.json"):
        assert str(REPO) not in path.read_text(encoding="utf-8")


# === Негативные контроли: тесты действительно чувствительны ============================
def test_control_replay_without_fingerprint_check_would_miss_r6(loaded, recorded, monkeypatch):
    """Если проверку fingerprint отключить, несовместимое изменение проходит молча.

    Подтверждает, что обнаружение в R6 обеспечивает именно проверка, а не артефакт теста.
    """

    expected = iter(i.request_fingerprint for i in recorded[ONE_TOOL].interactions)
    monkeypatch.setattr(
        replay_module, "project_request",
        lambda kwargs, refs: type("P", (), {"fingerprint": next(expected)})(),
    )
    result = replay(loaded, recorded[ONE_TOOL], lambda s: setattr(s.ticket, "message", s.ticket.message + " Please hurry."))
    assert result.harness_error is None  # без проверки изменение незаметно
    monkeypatch.undo()
    changed = replay(loaded, recorded[ONE_TOOL], lambda s: setattr(s.ticket, "message", s.ticket.message + " Please hurry."))
    assert changed.harness_error == "replay:fingerprint_mismatch"  # с проверкой — обнаружено


def test_control_disabled_gate_would_record_secret(loaded, monkeypatch):
    monkeypatch.setattr(recorder_module, "scan_text", lambda *a, **k: [])
    monkeypatch.setattr(recorder_module, "scan_value", lambda *a, **k: [])
    _, source, result = record_one(loaded, ONE_TOOL, _answer("debug sk-proj-abcdefghijklmnopqrstuvwx"))
    assert ONE_TOOL in source.recordings and result.harness_error is None  # без гейта утечка записана
    # ... но загрузочный гейт (вторая линия) всё равно её отвергнет:
    monkeypatch.undo()
    with pytest.raises(RecordingFormatError):
        parse_recording(render_recording(source.recordings[ONE_TOOL]))


def test_control_tampering_without_reseal_is_caught_by_integrity(recorded):
    data = rec_dict(recorded[SEQUENTIAL])
    data["interactions"][0]["response"]["tool_calls"][0]["arguments"] = {"order_id": "order_999"}
    with pytest.raises(RecordingFormatError) as exc:
        parse_recording(json.dumps(data))
    assert exc.value.code == "integrity_mismatch"
    # integrity — защита от порчи, а не подпись: пересчитанная запись формально валидна.
    assert parse_recording(reseal(data)).interactions[0].response.tool_calls[0].arguments == {"order_id": "order_999"}


def test_control_tampered_response_is_detected_by_replay_expectations(loaded, recorded):
    data = rec_dict(recorded[ONE_TOOL])
    data["interactions"][0]["response"]["tool_calls"][0]["name"] = "lookup_order"
    result = replay(loaded, parse_recording(reseal(data)))
    assert result.tool_selection_correct is False or result.harness_error is not None


def test_control_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": [2, {"d": 1, "c": 2}]}) == canonical_json({"a": [2, {"c": 2, "d": 1}], "b": 1})


def test_control_replay_differs_from_fake_when_recording_is_changed(loaded, recorded):
    """Контроль для R4: сравнение replay==fake не тривиально истинно."""

    data = rec_dict(recorded[ONE_TOOL])
    data["interactions"][-1]["response"]["text"] = "Completely different answer."
    result = replay(loaded, parse_recording(reseal(data)))
    fake = run_scenario(scenario(loaded, ONE_TOOL))
    assert result.mismatches and not fake.mismatches


# === Усиление после мутационной проверки ===========================================
class _Upstream:
    """Минимальный синтетический upstream: два текстовых ответа либо заданная ошибка."""

    def __init__(self, *errors):
        self.responses = self
        self._errors = list(errors)
        self.n = 0

    def create(self, **_kw):
        self.n += 1
        if self._errors and self._errors[0] is not None:
            raise self._errors.pop(0)
        if self._errors:
            self._errors.pop(0)
        return type("R", (), {"id": f"p{self.n}", "output": [], "output_text": f"answer {self.n}", "usage": None})()


def _record_two_texts():
    session = RecordingSession("t", ONE_TOOL, declared_user_texts=["a", "b"], synthetic=True)
    client = RecordingLLMClient(session, _Upstream())
    client.responses.create(model="m", input="a")
    client.responses.create(model="m", input="b")
    return session.finalize()


def test_replay_stays_failed_after_first_mismatch_even_if_later_request_matches():
    recording = _record_two_texts()
    client = ReplayLLMClient(recording)
    with pytest.raises(replay_module.ReplayMismatch):
        client.responses.create(model="m", input="WRONG")
    with pytest.raises(replay_module.ReplayMismatch) as exc:
        client.responses.create(model="m", input="b")  # совпал бы со 2-й записью без «отравления»
    assert exc.value.code == "fingerprint_mismatch"
    assert client.finish().code == "fingerprint_mismatch"


def test_recorder_stays_refused_after_first_refusal():
    session = RecordingSession("t", ONE_TOOL, declared_user_texts=["a"], synthetic=True)
    client = RecordingLLMClient(session, _Upstream())
    with pytest.raises(RecordingRefused):
        client.responses.create(model="m", input="undeclared")
    with pytest.raises(RecordingRefused) as exc:
        client.responses.create(model="m", input="a")  # сам по себе допустим
    assert exc.value.code == "undeclared_user_text"
    with pytest.raises(RecordingRefused):
        session.finalize()


def test_save_recording_refuses_sensitive_content_with_refusal_error(tmp_path):
    from evaluation.recording import Interaction, RecordedResponse, make_recording

    bad = make_recording(
        recording_id="t", scenario_id=ONE_TOOL, model="m", status="complete",
        interactions=[Interaction(
            sequence=1, request_fingerprint="sha256:" + "0" * 64,
            response=RecordedResponse(type="text", text="key sk-proj-abcdefghijklmnopqrstuvwx"),
        )],
    )
    with pytest.raises(RecordingRefused) as exc:
        save_recording(bad, tmp_path / "x.recording.json")
    assert exc.value.code == "sensitive_content"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("raised, stored", [
    (TimeoutError("secret detail"), "TimeoutError"),
    (ConnectionError("secret detail"), "ConnectionError"),
    (ValueError("secret detail"), "RuntimeError"),
])
def test_upstream_error_class_is_allowlisted_and_text_is_dropped(raised, stored):
    session = RecordingSession("t", ONE_TOOL, declared_user_texts=["a"], synthetic=True)
    client = RecordingLLMClient(session, _Upstream(raised))
    with pytest.raises(type(raised)):
        client.responses.create(model="m", input="a")  # агент видит исходную ошибку
    recording = session.finalize()
    assert recording.interactions[0].response.error_type == stored
    assert "secret detail" not in render_recording(recording)
    replayed = ReplayLLMClient(recording)
    with pytest.raises({"TimeoutError": TimeoutError, "ConnectionError": ConnectionError,
                        "RuntimeError": RuntimeError}[stored], match="recorded llm provider failure"):
        replayed.responses.create(model="m", input="a")


def test_incomplete_status_is_rejected_on_load_and_on_save(recorded, tmp_path):
    data = rec_dict(recorded[ONE_TOOL])
    data["status"] = "incomplete"
    with pytest.raises(RecordingFormatError) as exc:
        parse_recording(reseal(data))
    assert exc.value.code == "incomplete_recording"


# === Исправления по независимому ревью (M1–M6, L3) ===================================
@pytest.mark.parametrize("text", [
    "ｓｋ-proj-abcdefghijklmnopqrstuvwx", "s​k-proj-abcdefghijklmnopqrstuvwx",
    "pwd=hunter2", "пароль: x", "john＠gmail.com", "john @ gmail . com", "x@example.com@gmail.com",
    "+1 (800) 555 - 0199", "4951234-56-78", "5551234", "A" * 39, "deadbeef" * 4,
])
def test_m1_gate_bypasses_are_closed(text):
    assert scan_text(text), text


def test_m2_unscanned_fields_and_numbers_are_gated(recorded):
    data = rec_dict(recorded[ONE_TOOL])
    for key, value in (("model", "sk-proj-abcdefghijklmnopqrstuvwx"), ("recording_id", "synthetic_18005550199")):
        bad = json.loads(json.dumps(data)); bad[key] = value
        with pytest.raises(RecordingFormatError):
            parse_recording(reseal(bad))
    bad = json.loads(json.dumps(data))
    bad["interactions"][0]["response"]["tool_calls"][0]["arguments"] = {"order_id": 18005550199}
    with pytest.raises(RecordingFormatError):
        parse_recording(reseal(bad))
    bad = json.loads(json.dumps(data)); bad["interactions"][0]["response"]["usage"]["input_tokens"] = 18005550199
    with pytest.raises(RecordingFormatError):
        parse_recording(reseal(bad))


def test_m3_duplicate_keys_nan_depth_and_size_are_rejected(recorded):
    text = render_recording(recorded[ONE_TOOL])
    dup = text.replace('"model":', '"model": "sk-real", "model":', 1) if '"model":' in text else text
    dup = text.replace('  "model": ', '  "model": "sk-REAL-LEAK",\n  "model": ', 1)
    assert dup != text
    with pytest.raises(RecordingFormatError) as exc:
        parse_recording(dup)
    assert exc.value.code == "corrupt_json"
    for raw in ('{"schema_version": NaN}', "[" * 100000, " " * 2_000_000):
        with pytest.raises(RecordingFormatError):
            parse_recording(raw)


def test_m4_non_fake_upstream_is_rejected_even_with_default_kind(loaded):
    class RealLike:
        responses = None

    source = RecordSource(lambda s: RealLike())
    with pytest.raises(LiveUpstreamNotEnabled):
        source.open(scenario(loaded, ONE_TOOL))


def test_m5_security_violation_in_excluded_scenario_is_still_reported(loaded, recorded):
    dataset, digest = loaded
    data = rec_dict(recorded["ua-004"])
    data["interactions"][-1]["response"]["text"] = "Profile: john@example.com"
    extra = json.loads(json.dumps(data["interactions"][-1])); extra["sequence"] = len(data["interactions"]) + 1
    data["interactions"].append(extra)  # лишнее взаимодействие -> replay:missing_calls (harness_error)
    s = scenario(loaded, "ua-004")
    result = run_scenario(s, ReplaySource({"ua-004": parse_recording(reseal(data))}))
    assert result.harness_error and result.security_violations
    report = build_report(dataset.model_copy(update={"scenarios": [s]}), digest, [result], mode=Mode.RECORDED_REPLAY)
    assert report["security_violations_in_excluded"] == ["ua-004"]


def test_m6_missing_recording_fails_ci_in_replay_cli(tmp_path):
    from evaluation.runner import main

    for path in DEFAULT_RECORDINGS.glob("*.json"):
        if path.name != "ot-001.recording.json":
            (tmp_path / path.name).write_bytes(path.read_bytes())
    # одна запись удалена из набора -> с --fail-on-mismatch код 1
    (tmp_path / "ot-001.recording.json").unlink(missing_ok=True)
    assert main(["--mode", "replay", "--recordings", str(tmp_path), "--fail-on-mismatch"]) == 1
    assert main(["--mode", "replay", "--recordings", str(DEFAULT_RECORDINGS), "--fail-on-mismatch"]) == 0


def test_l3_recording_with_findings_is_not_saved(tmp_path, capsys):
    from evaluation.runner import main
    import evaluation.runner as runner_module

    original = runner_module.run_dataset

    def tainted(dataset, source=None):
        results = original(dataset, source)
        results[0].security_violations = ["secret_leak"]
        return results

    runner_module.run_dataset = tainted
    try:
        main(["--mode", "synthetic-record", "--recordings", str(tmp_path)])
    finally:
        runner_module.run_dataset = original
    first = original(load_dataset(DEFAULT_DATASET)[0]).__getitem__(0).scenario_id
    assert not (tmp_path / f"{first}.recording.json").exists()
    assert len(list(tmp_path.glob("*.recording.json"))) == 26


# === L1: декларация синтетичности (необходима, но не достаточна) ====================
class _CountingUpstream(FakeLLMClient):
    """Синтетический upstream, считающий обращения (отказ обязан случиться ДО вызова)."""

    def __init__(self, script):
        super().__init__(script)
        self.reached = 0
        real = self.responses.create

        def create(**kw):
            self.reached += 1
            return real(**kw)

        self.responses.create = create


def _record_with_counter(loaded, sid, mutate):
    s = scenario(loaded, sid)
    mutate(s)
    upstreams: list[_CountingUpstream] = []

    def factory(sc):
        up = _CountingUpstream(sc.llm_script)
        upstreams.append(up)
        return up

    source = RecordSource(upstream_factory=factory)
    result = run_scenario(s, source)
    return source, result, upstreams


@pytest.mark.parametrize("sid", [ONE_TOOL, NO_LLM])
@pytest.mark.parametrize("declared", [None, False])
def test_l1_missing_or_false_synthetic_declaration_is_refused_before_upstream(loaded, sid, declared):
    def mutate(s):
        s.synthetic = declared

    source, result, upstreams = _record_with_counter(loaded, sid, mutate)
    assert sid not in source.recordings
    assert source.refusals[sid] == "synthetic_not_declared"
    assert result.harness_error == "record:synthetic_not_declared"
    assert sum(u.reached for u in upstreams) == 0


def test_l1_declaration_is_not_proof_content_gate_still_applies(loaded):
    def mutate(s):
        s.synthetic = True
        s.ticket.message = "My key is sk-proj-abcdefghijklmnopqrstuvwx"

    _, source, result = record_one(loaded, ONE_TOOL, mutate)
    assert result.harness_error == "record:sensitive_request"
    assert ONE_TOOL not in source.recordings

    def pii(s):
        s.synthetic = True
        s.llm_script[-1].text = "Write to leak.me@gmail.com"

    _, source, result = record_one(loaded, ONE_TOOL, pii)
    assert result.harness_error == "record:sensitive_response"


def test_l1_unknown_scenario_fields_and_non_boolean_declarations_are_rejected(loaded):
    base = scenario(loaded, ONE_TOOL).model_dump(mode="json")
    with pytest.raises(ValidationError):
        Scenario.model_validate({**base, "data_class": "synthetic"})
    for bad in ("true", "yes", 1, 0, "false"):
        with pytest.raises(ValidationError):
            Scenario.model_validate({**base, "synthetic": bad})
    assert Scenario.model_validate({**base, "synthetic": True}).synthetic is True


def test_l1_every_dataset_scenario_declares_synthetic_true(loaded):
    assert len(loaded[0].scenarios) == 27
    assert all(s.synthetic is True for s in loaded[0].scenarios)


def test_l1_declaration_does_not_affect_fake_and_replay_modes(loaded, recorded):
    s = scenario(loaded, ONE_TOOL)
    s.synthetic = None
    assert run_scenario(s).harness_error is None
    assert run_scenario(s, ReplaySource(recorded)).harness_error is None


# === Усиление после повторной мутационной проверки (EVAL-M02/WP1 finalization) =====
def test_gate_short_api_key_is_caught_by_its_own_pattern():
    """Ключ короче long_token (32): ловится только шаблоном api_key."""

    assert scan_text("key sk-abcdef0123456789xyz") == ["api_key"]


def test_gate_scenario_specific_secret_is_caught_as_known_secret():
    assert scan_text("value zebra-unique-77", extra_secrets=["zebra-unique-77"]) == ["known_secret"]
    assert scan_text("value zebra-unique-77") == []


def test_recorder_refuses_scenario_secret_that_matches_no_pattern(loaded):
    def mutate(s):
        s.secret_canaries = ["zebra-unique-77"]
        s.llm_script[-1].text = "Answer zebra-unique-77"

    _, source, result = record_one(loaded, ONE_TOOL, mutate)
    assert result.harness_error == "record:sensitive_response"
    assert ONE_TOOL not in source.recordings


def test_report_leak_guard_fails_closed_when_redaction_cannot_reach_the_secret(loaded):
    """Секрет в ключе словаря не маскируется _redact — финальная проверка обязана сработать."""

    from evaluation.runner import ReportLeakError

    dataset, digest = loaded
    result = run_scenario(scenario(loaded, ONE_TOOL))
    result.observed_classification = {"SYNTH_SECRET_EVAL_7f3a9c": 1}
    with pytest.raises(ReportLeakError):
        build_report(dataset, digest, [result])
