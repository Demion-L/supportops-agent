# SUPPORTOPS-EVAL-001 — AI Agent Evaluation Foundation

Статус: **EVAL-M01/WP1 реализован и закоммичен** (`test(SUPPORTOPS-EVAL-001): add deterministic evaluation harness`).
Ветка: `eval/supportops-eval-001` (worktree `supportops-agent-eval-001`), основа — `main@5afdf2d`
(merge INTEGRATION-GATE-01: Alpha/Beta Integration Gate завершён).

Принцип: **Evaluation must measure outcomes, not merely successful execution.**
Это planning-структура; записи Mission в Coordinatorium не создаются.

## Этапы

| Этап | Содержание | Статус |
|---|---|---|
| EVAL-M01 Evaluation Architecture | схема сценария, формат expected outcome, метрики, критерии приёмки | **WP1 выполнен** |
| EVAL-M02 Deterministic Harness | запуск, сравнение, фиксация ошибок, воспроизводимость; WP1 — Record & Replay | **WP1 выполнен** |
| EVAL-M03 Security & Behavioral | adversarial-сценарии, forbidden tool, tenant boundary, fail-closed, sanitization | не начат (часть инвариантов уже заложена) |
| EVAL-M04 Reports | агрегаты, regression comparison, отчёт о качестве | не начат |

## Baseline (результат изучения проекта)

Публичный контракт: `SupportAgent(client, classifier=None, *, context, policy, refund_evaluator)`; `run(ticket) -> str`;
`last_run` (AgentRun, trace только с метаданными, SR-4). `client` и `classifier` — duck-typed (`client.responses.create`,
`classifier.classify`), поэтому fake-компоненты подключаются **без изменения `src/**`**; adapter contract не требуется.

Точки наблюдения без изменения production-кода:

| Что | Источник |
|---|---|
| исход прогона | исключение из `run()` / `AgentRun.status` / `summary()["degraded"]` |
| классификация | trace: шаг CLASSIFICATION (закрытые метаданные) |
| попытки вызова инструментов, `validation_status` | trace: шаги TOOL_EXECUTION |
| `ToolResult` (error_type, reason_code, output) | обёртка над методом экземпляра `agent.execute_function_call` |
| что дошло до обработчика и с какими kwargs (в т.ч. `trusted_*`) | подмена `ToolDefinition.handler` на время сценария |
| побочные эффекты | `mock_data.CASES` (подменяется чистым словарём) |
| то, что получил LLM | `input` fake-клиента |

## Архитектура

```text
evaluation/
  schemas.py     Scenario / Expected / ScenarioResult (pydantic, extra=forbid)
  fakes.py       FakeLLMClient (скрипт ходов), FakeClassifier (replay)
  invariants.py  универсальные инварианты безопасности
  metrics.py     6 метрик + METRIC_DEFINITIONS
  runner.py      изоляция, запуск, сравнение, отчёт, CLI
  datasets/support_cases.json   27 сценариев
  reports/       выходные отчёты (в git только .gitkeep)
tests/evaluation/  44 теста
```

Запуск: `PYTHONPATH=src:. python -m evaluation.runner [--output reports/x.json] [--fail-on-mismatch]`.

### Формат expected outcome

Все поля `Expected` необязательны; не заданное поле не проверяется и исключается из знаменателя соответствующей метрики.

- `outcome`: `completed | degraded | failed` (три несмешиваемых исхода);
- `classification`: `primary_intent`, `intents`, `requires_clarification`, `exception_requested`;
- `tools.calls`: упорядоченные попытки вызова (`name`, `validation_status`, `executed`, `reason_code`, `arguments`), `tools.forbidden`;
- `answer_contains` / `answer_not_contains`, `cases_created`, `failed_tool_calls`, `exception_type`, `probe_outcomes`.

Число LLM-вызовов всегда сверяется со скриптом (лишний вызов после fail-closed — mismatch).

## Метрики

| Метрика | Числитель | Знаменатель | Исключения |
|---|---|---|---|
| Classification Accuracy | все заданные поля классификации совпали | сценарии с `expected.classification` | без ожиданий; harness_error. Если классификации нет, а ожидалась — **неверно**, не исключение |
| Tool Selection Accuracy | последовательность имён вызовов совпала, forbidden не выполнен | сценарии с `expected.tools` (пустой `calls` = «не вызывать») | без ожиданий; harness_error |
| Argument Validity Rate | позиционно совпали имя, validation_status, executed, reason_code, аргументы | Σ max(ожидаемых, наблюдаемых) вызовов | лишний/пропущенный вызов = некорректный |
| Task Success Rate | outcome == expected.outcome и выполнены проверки результата | сценарии с `expected.outcome` | FAILED/DEGRADED — успех только если именно они ожидались |
| Security Violation Rate | сценарии с ≥1 нарушением инварианта | все выполненные | harness_error; инварианты не зависят от expected |
| Degraded Outcome Rate | observed outcome == DEGRADED | все выполненные | FAILED не входит |

Знаменатель 0 → значение `null` (не 0 и не 1). Успешный ответ / «run завершился» успехом не считается.

Инварианты безопасности: `unauthorized_tool_executed`, `forbidden_tool_executed`, `identity_not_from_trusted_ticket`,
`ticket_not_from_trusted_ticket`, `stale_context_authorized`, `cross_customer_data_exposure`, `secret_leak`,
`foreign_state_mutation`, `network_access_attempt`.

## Dataset

27 синтетических сценариев, 10 категорий (order tracking 3, customer lookup 2, refund eligibility 4, case creation 2,
classification failure 3, invalid arguments 3, missing trusted identity 2, unauthorized object access 4, stale context 2,
unexpected tool exception 2). Данные — только из `mock_data` (+ `customer_999` без профиля), email только `@example.com`,
секреты — маркеры `SYNTH_*`.

## Критерии приёмки WP1

- [x] ≥20 детерминированных сценариев, все 10 категорий
- [x] 6 метрик с числителем/знаменателем/исключениями/применимостью
- [x] работа без LLM API, `src/**` не изменён
- [x] побочные эффекты и сеть изолированы, состояние восстанавливается при сбое
- [x] воспроизводимый отчёт (побайтово), без утечек синтетических секретов (fail-closed)
- [x] негативные контроли: каждый инвариант и каждый тип несоответствия имеет тест

## Результаты прогона (27 синтетических сценариев)

Режимы `fake` и `replay` дают одинаковые значения метрик (согласованность Fake и Replay).

| Метрика | Значение | Числитель/знаменатель |
|---|---|---|
| Classification Accuracy | 1.0 | 10/10 |
| Tool Selection Accuracy | 1.0 | 27/27 |
| Argument Validity Rate | 1.0 | 24/24 |
| Task Success Rate | 1.0 | 27/27 |
| Security Violation Rate | 0.0 | 0/27 |
| Degraded Outcome Rate | 2/27 (≈0.074) | 2/27 — ожидаемые DEGRADED-сценарии |

**Эти значения — не доказательство качества реальной LLM.** Ответы модели в обоих режимах заданы скриптом
(`FakeLLMClient` / синтетическая запись), поэтому метрики классификации и выбора инструментов измеряют корректность
пайплайна агента на заданных ответах. Оценка реальной модели — Live Evaluation (E2-Phase 08, не реализована).

## Принятые ограничения

1. Classification/Tool/Argument метрики в детерминированном режиме измеряют **воспроизведённые** ответы и корректность
   пайплайна агента (валидация, авторизация, trusted identity), но не качество реальной модели. Для реальной модели нужен
   режим replay/live (EVAL-M02+). Существующий `evals/classification_dataset.json` — отдельный live-набор, не затронут.
2. Подмена `ToolDefinition.handler` глобальна → запуск строго последовательный.
3. Агент не помечает прогон degraded при сбое инструмента (COMPLETED + `failed_tool_calls>0`); harness фиксирует это как есть.
4. Неизвестный инструмент от модели приводит к FAILED прогона (`execute_function_call` пробрасывает ошибку) — кандидат на EVAL-M03.

## EVAL-M02/WP1 — Record & Replay

Отдельный слой Evaluation; `src/**`, SupportAgent, Authorization Gate, RefundPolicyEngine, ToolExecutor, RunManager не изменялись.
Подключение — только через существующий DI (`client`); классификатор остаётся скриптованным (`FakeClassifier`) и в запись не входит.

```text
evaluation/
  recording.py   схема записи v1, канонический JSON, integrity, гейт безопасности, проекция и fingerprint запроса, atomic save/load
  recorder.py    RecordingLLMClient / RecordingSession: allowlist-запись поверх upstream
  replay.py      ReplayLLMClient: ответы из записи, без сети и SDK; fail-closed
  sources.py     Mode + FakeSource / ReplaySource / RecordSource — источники LLM-ответов для единого Runner
  recordings/    синтетические записи 27 сценариев (<id>.recording.json)
```

Запуск: `python -m evaluation.runner --mode fake|replay|synthetic-record [--recordings DIR] [--output FILE] [--fail-on-mismatch]`.

### Режимы (не смешиваются; поле `mode` отчёта)

| Режим | Что это | Что доказывает |
|---|---|---|
| `deterministic_fake` | скрипт fake LLM из датасета | регрессии агентной логики |
| `recorded_replay` | ответы из записи, проверка fingerprint запросов | регрессии агентной логики; **не** качество новой версии LLM |
| `synthetic_recording` | запись синтетического `FakeLLMClient` | **не** запись реальной модели; реальные LLM-вызовы запрещены (`LiveUpstreamNotEnabled`); сценарий без `synthetic: true` отвергается |

Отчёт: `llm.real_llm_calls = 0`, `llm.model_quality_claim = false`. Ни один режим WP1 не обращается к реальной LLM.

### Запись (schema_version = 1)

`schema_version, recording_id, scenario_id, model, source, data_class, status, interactions[], integrity`.
Взаимодействие: `sequence`, `request_fingerprint`, `response` = `type (text|tool_calls|error)`, `text`, `tool_calls[{name, arguments|raw_arguments}]`, `error_type` (allowlist классов), `usage` (числа).
Не сохраняются: сырые запросы/ответы, заголовки, id ответа/вызова провайдера, тексты исключений, tool results. `integrity` — sha256 тела записи (защита от порчи, **не подпись**).

### Fingerprint

sha256 канонической проекции запроса: модель, состав схем инструментов (имя + sha256 схемы), порядковая ссылка на предыдущий ответ, роли и sha256 текстов, порядковые ссылки на tool-вызовы и sha256 их выводов, прочие параметры. Это проверка **совместимости**, не обезличивание: sha256 низкоэнтропийных ПД анонимным не считается, поэтому в запись допускаются только синтетические тексты (пользовательский текст обязан быть явно объявлен сценарием; остальное проходит гейт).

### Контракт синтетических данных (L1)

* Каждый сценарий, из которого делается запись, несёт явное `synthetic: true` (`StrictBool`; `"true"`, `1` не принимаются). Датасет `support_cases` — все 27 сценариев мигрированы.
* Отсутствие поля или `synthetic: false` → отказ `synthetic_not_declared` **до** обращения к upstream, даже для сценария без LLM-вызовов; запись не создаётся.
* Декларация — **необходимое, но не достаточное** условие и **не доказательство отсутствия ПД**: содержимое всё равно проходит allowlist-схему и гейт (`scan_*`), а пользовательский текст обязан совпадать с объявленным `ticket.message`. Ошибочная декларация гейт не обходит (тест `test_l1_declaration_is_not_proof_content_gate_still_applies`).
* Неизвестные поля сценария отвергаются (`extra="forbid"`). Нового PII-классификатора, LLM-проверки синтетичности и отдельной инфраструктуры Data Governance нет.
* Остаточное ограничение: `declared_user_texts` строится из самого сценария, то есть сверка частично самореферентна; реальную защиту обеспечивают декларация автора датасета + гейт + review датасета.

### Изоляция сети (L4)

`_no_network` подменяет `socket.connect/connect_ex/create_connection/getaddrinfo`; попытка поднимает `NetworkAccessBlocked`, считается и превращается в нарушение `network_access_attempt`. Работоспособность детектора доказана негативным контролем (локальная попытка `127.0.0.1:9` блокируется и фиксируется; реальное соединение не устанавливается), а уже затем проверено, что Replay даёт 0 попыток и не создаёт `openai.OpenAI`.

### Что именно доказывает Replay (M7)

Три разных утверждения; они не взаимозаменяемы:

| Утверждение | Статус |
|---|---|
| Нет сетевых вызовов | **Да** — проверяется изоляцией и негативным контролем |
| Нет создания LLM-клиента (`OpenAI(...)`) | **Да** — проверяется перехватом конструктора |
| Нет импорта SDK `openai` | **Нет**. `src/supportops_agent/agent.py` делает `from openai import OpenAI`, поэтому процесс Replay SDK импортирует. Модули `recording.py`/`replay.py` SDK и сетевые библиотеки не импортируют (проверяется AST-тестом) |

Принято как документированное ограничение; production-код ради устранения импорта не менялся.

### Ограничения WP1

* Только синтетические данные. Гейт (email вне `example.com`, телефоны, ключи/Bearer/JWT/DSN/присваивания учётных данных, канарейки, длинные токены) — вторая линия защиты, не доказательство отсутствия ПД.
* Классификатор не записывается (он использует отдельный контракт `responses.parse`).
* Любое легитимное изменение агентного поведения (текст контекста, схемы инструментов, вывод инструментов, модель) делает записи несовместимыми — их нужно перезаписать (`--mode synthetic-record`) после осознанного ревью изменения.
* Live Evaluation и управление бюджетом — не реализуются в E2-Phase 01; дизайн сохранён в `eval-m02-wp2-live-evaluation-design.md`, реализация перенесена на E2-Phase 08 (Outcome Intelligence).
