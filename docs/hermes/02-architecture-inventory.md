# Архитектурная инвентаризация и матрица переиспользования (Deliverables C, D)

**Дата:** 2026-11-27 · **Метод:** read-only чтение кода `main` и всех локальных веток/worktree; базовый прогон тестов.

Классификация: **A** — reusable as is · **B** — configuration adaptation · **C** — limited architectural adaptation · **D** — domain specific · **E** — missing capability.

---

## 1. Верифицированная база репозитория [Ф]

| Параметр | Значение |
|---|---|
| `main` | `5afdf2d` (merge SUPPORTOPS-API-004 Alpha/Beta, INTEGRATION-GATE-01), working tree чист на момент начала |
| Размер `src/` | ~4 960 строк: `supportops_agent` ≈ 2 800 (ядро), `supportops_service` ≈ 2 200 (API/security/persistence) |
| Тесты `main` | `pytest`: **934 passed, 138 skipped** (skipped — `postgres`-маркер без `SUPPORTOPS_TEST_DATABASE_URL`). Отчёт INTEGRATION-GATE-01 фиксирует 1072 passed с реальным PG 16 |
| LLM в тестах | Все тесты офлайн (fake клиент); платные вызовы в ходе исследования не выполнялись |
| Git | 67 коммитов, 2026-08-08 … 2026-11-17 |

### Ветки и worktree (10 worktree) [Ф]

| Worktree / ветка | Состояние относительно `main` | Состояние working tree | Значение для HERMES |
|---|---|---|---|
| `main` | — | чист | База |
| `eval/supportops-eval-001` | +2 коммита (6 500 строк): Evaluation Harness, Record/Replay, 27 файлов `*.recording.json` | untracked `docs/evaluation/eval-m02-wp2-live-evaluation-design.md` | **Evaluation Harness существует только здесь; в `main` не влит** |
| `feat/supportops-api004-http-slice` | = `main` | untracked: `routers/tickets.py` (GET), `schemas.py`, 2 теста | HTTP-срез в работе другой сессии; `POST /v1/tickets` намеренно отсутствует до решения владельца об источнике `customer_id` |
| `feat/supportops-api004-alpha-security` | влита в `main` | untracked копия `api-004-security-contract.md` | Возможное расхождение с версией в `main` — не трогалось |
| `feat/supportops-api004-beta-wp2`, `-m01`, `integration/…` | влиты | чисты | Закрытая работа |
| `design/supportops-api-003` | влита по содержанию (док в `main`) | чист | Дизайн сервисного слоя |
| `test/supportops-sec003-wp4` | = `main` | чист | Закрытая регрессия SEC-003 |
| `experiment/agent-commons-exp-001` | = `main` | untracked `experiments/agent_commons/` | Изолированный эксперимент (stdlib, 72 теста), **не** зависит от production-кода |
| `origin/feat/classification-layer` | удалённая | — | Исторический |

**Конфликтов с документированием нет:** все правки этой задачи — `README.md` и `docs/hermes/*` в основном worktree; чужие незакоммиченные файлы не затронуты. Риск для будущей интеграции: untracked-файлы в `api004-http`, `api004-alpha`, `eval-001`, `commons-exp-001` существуют только на диске одной машины.

---

## 2. Текущая архитектура [Ф]

```
Слой сервиса (supportops_service)                Слой агентного ядра (supportops_agent)
─────────────────────────────────                ───────────────────────────────────────
HTTP ─▶ RequestContextMiddleware                 SupportTicket ─▶ SupportAgent.run()
     ─▶ SecuredRoute (authn + scope)                 │  (один экземпляр = один активный run)
           │ API key ─▶ CredentialVerifier           ├─ _classify  (LLM, fail-closed, intents SupportOps)
           ▼             (HMAC+pepper, rotation)     ├─ _evaluate_refund (LLM предлагает, RefundPolicyEngine решает)
     TenantContext(tenant_id, scopes)                ├─ цикл LLM ↔ tools (MAX_AGENT_STEPS=10)
           │                                         │     execute_tool_call:
           ▼                                         │       Authorization Gate ─▶ валидация ─▶ handler
     TicketRepository (tenant_id обязателен)         │       trusted_customer_id / trusted_ticket_id
           │                                         ├─ compose_final_answer (политика приоритетнее LLM)
           ▼                                         └─ AgentRun / AgentStep (metadata-only trace, in-memory)
     PostgreSQL: api_credentials, tickets, runs
     (runs: status, attempt=fencing, lease_*)    ◀── МОСТА между слоями нет: сервис не вызывает SupportAgent
```

Ключевое наблюдение [Ф]: **два слоя не соединены.** `supportops_service` не импортирует `supportops_agent` (запрет в docstring пакета, проверяется `tests/service/test_app_skeleton.py`; обратное — агент не импортирует service security — `tests/service/security/test_security_static_boundaries.py:71`), воркера, который забирал бы `runs` и запускал агента, в коде нет. `tenant_id` существует только в сервисном слое.

---

## 3. Инвентаризация компонентов

Для каждого: назначение · статус · контракты вх/вых · зависимости · предметная связанность · независимое использование · конфигурируемость · ограничения масштабирования · границы безопасности. Источники — файл:строка.

### 3.1 Agent Runtime / Execution Loop — `agent.py` (`SupportAgent`)

- **Назначение/статус:** синхронный цикл «LLM ↔ tools» с trace; реализован, зрелый (SEC-003 M04: изоляция прогонов, fail-safe seal).
- **Контракты:** вход `SupportTicket`; выход `str` + `AgentRun`; зависимости `OpenAI`-клиент (DI), `TicketClassifier`, `ExecutionContext`, `ToolAuthorizationPolicy`, `RefundEvaluationOrchestrator` — все через конструктор.
- **Связанность:** `MODEL_NAME="gpt-4.1-mini"` (`agent.py:46`), API Responses с `previous_response_id` (`agent.py:881-889`), этапы `_classify`/`_evaluate_refund` зашиты в `_start` (`agent.py:666`). Схемы инструментов — глобальные `get_llm_tool_schemas()` (`agent.py:885`).
- **Независимое использование:** один экземпляр — один активный `run()` (`_run_lock`, `agent.py:381`); нужен новый экземпляр на прогон.
- **Ограничения:** только последовательные вызовы инструментов; нет таймаута/отмены/паузы; лимит рекурсии — только `MAX_AGENT_STEPS=10`; провайдер привязан к OpenAI Responses.
- **Безопасность:** сильная (см. 3.4–3.6). **Класс: C** (скелет цикла универсален, конвейер и провайдер — нет).

### 3.2 Tool Registry — `tool_definitions.py`

- `ToolDefinition` (`:54`) несёт `has_side_effects`, `requires_approval`, `requires_trusted_identity`, `requires_trusted_ticket`, Pydantic-схему аргументов `extra="forbid"` и `to_llm_schema()` — единый источник истины для исполнения и LLM-схем.
- **Но:** `TOOL_REGISTRY` — глобальный словарь уровня модуля (`:140`), 5 инструментов SupportOps. Нет per-tenant/per-run реестра, нет версий, нет discovery. Флаги «trusted customer/ticket» — частные случаи общей идеи «trusted binding».
- **Класс: C** (метаданные — A, глобальность и два зашитых binding-флага — C).

### 3.3 Tool Executor — `tool_executor.py`

- Конвейер `lookup → _authorize → model_dump → handler → JSON round-trip`, структурированные ошибки без текста исключений; `SAFE_ERROR_CODES` (закрытый набор).
- Привязки `trusted_customer_id`/`trusted_ticket_id` подставляются после `model_dump()`, так что аргументы модели не могут их переопределить (`:208+`).
- Реестр берётся из глобального `TOOL_REGISTRY`.
- **Класс: B/C** — механизм универсален; параметризация реестром и обобщение trusted binding требуют малой доработки.

### 3.4 Authorization Gate — `authorization.py`

- Решение — только по `ToolDefinition` и `ExecutionContext`; три решения ALLOW/DENY/APPROVAL_REQUIRED; закрытый набор reason codes; fail-closed на отсутствие контекста/политики/ошибку политики/невалидный результат.
- `ExecutionContext(agent_id, allowed_tools)` (`:63`) — **без `tenant_id`, без capability, без бюджета, без делегирования.** `SUPPORT_AGENT_ALLOWED_TOOLS` — константа (`:119`).
- `APPROVAL_REQUIRED` всегда блокирует: канала одобрения нет (комментарий в модуле).
- Gate **не использует** `has_side_effects`; `APPROVAL_REQUIRED` — всегда блок.
- **Класс (по ревью): протокол политики и deny-by-default — A; фактическое поведение для HERMES — C/E** (tenant-awareness, делегирование, approval, классы эффектов дописываются, а не переиспользуются).

### 3.5 Business Policy — `policy.py`, `refund_evaluation.py`, `refund_facts.py`, `refund_response.py`

- Детерминированный движок возвратов без LLM/сети/БД; оркестратор «LLM предлагает → доверенные факты → политика решает»; policy-first ответ.
- **Класс: D** по содержанию. **Паттерн** «LLM предлагает, детерминированное решает, ответ собирается из решения» — переиспользуем (**A** как паттерн, не как код).

### 3.6 Classification — `classification.py`

- Протокол `TicketClassifier`, fail-closed (`ClassificationError` → фиксированный ответ), `TicketIntent` = {order_status, refund_request, complaint, general_inquiry, unknown} — **домен**. Реализация через `client.responses.parse`.
- **Класс: D** (интенты, схема, инструкции); **C** для превращения в общий «Intent Router» с набором интентов из конфигурации.

### 3.7 State Management / Run Lifecycle — `models.py`, `run_manager.py`, `agent_state.py`

- `AgentRun`/`AgentStep`, `AgentRunStatus` {running, completed, failed}, `summary()` (токены, длительность, degraded). Всё **в памяти процесса**; состояние цикла (`AgentState`) не сериализуется; продолжение зависит от `previous_response_id` на стороне провайдера.
- `AgentStepType` — закрытый enum SupportOps (`classification`, `policy_decision`, …).
- **Класс: B** для trace-модели (расширить типы шагов), **E** для durable state, паузы и возобновления.

### 3.8 Trace sanitization (SR-4) — `agent.py:77-312`

- Ограничения глубины/узлов/размера, закрытые словари кодов, метаданные вместо значений; применяется до записи в менеджер.
- **Класс: A для принципа, C для кода.** ≈235 строк внутри файла в 900 строк вперемешку с SupportOps-диагностиками; закрытый `AgentStepType`. Трудоёмкость выноса **не оценена** (утверждение «малая» из первой редакции снято по ревью).

### 3.9 Multi-tenancy (сервис) — `interfaces.py`, `security/*`, `repositories/*`

- `TenantContext(tenant_id, credential_id, scopes)` создаётся только `build_tenant_context` после аутентификации (статический тест запрещает иные места). Закрытый набор `Scope` (`tickets:write|read`, `trace:read`).
- Все методы `TicketRepository` принимают `tenant_id` keyword-only без значения по умолчанию; чужой/несуществующий/некорректный id → `None`.
- Не реализовано: таблица `tenants`, проверка «tenant отключён» (`API4-TD-TENANT`), per-tenant конфигурация/бюджеты/ключи интеграций, RLS (отложено).
- **Класс: A** для аутентификации и tenant-scoped репозиторного паттерна; **E** для tenant-конфигурации возможностей.

### 3.10 Persistence — `db/*`, `migrations/0001_initial.sql`

- Таблицы `api_credentials`, `tickets` (иммутабельные триггером), `runs` (status, `attempt` как fencing token, `lease_owner/lease_until`, `max_attempts`, CHECK-инварианты, индексы под claim). Идемпотентность `UNIQUE(tenant_id, idempotency_key)` + `request_hash`.
- Предметная связанность: `tickets` хранит `customer_id`, `message`, `order_id`; `runs` — обобщаемая сущность «исполнение».
- Кода claim/lease/heartbeat (воркера) **нет** — только схема и индексы.
- **Класс:** `api_credentials`, пул, миграции, паттерн идемпотентности — **A**; `runs` — **C** (идея lease/fencing переиспользуема, но `UNIQUE(ticket_id)`, иммутабельный `tickets` (триггер) и запрет обновления терминальных `runs` делают схему непригодной для сущностей с состоянием и нескольких прогонов на сущность — нужна новая схема); `tickets` — **D/C**; воркер — **E**.

### 3.11 API — `app.py`, `security/dependencies.py`, `route_audit.py`, `errors.py`

- Фабрика приложения fail-closed (нет `/v1` без credential repo и pepper; аудит маршрутов при старте: каждый `/v1` маршрут обязан быть `SecuredRoute`, scope — в сигнатуре маршрута); фиксированные коды ошибок; `AuthenticationGate` (3 одновременных аутентификации при пуле 4).
- В `main`: `/healthz`; тикетные маршруты — только в worktree `api004-http` (GET), `POST` ожидает решения владельца.
- **Класс: A** (каркас и аудит), **E** (маршруты приёма обращений/каналы).

### 3.12 Observability — `models.AgentRun.summary()`, middleware

- Токены, длительности, число вызовов, degraded. Нет стоимости, нет связи с бизнес-исходом, нет агрегации, нет экспорта. Структурированного логирования почти нет.
- **Класс: B** (summary — основа), **E** (cost, outcome, агрегаты).

### 3.13 Evaluation Harness, Record & Replay — ветка `eval/supportops-eval-001`

- Сценарии с ожидаемым **исходом** (`Outcome`: completed/degraded/failed), метрики (`classification_accuracy`, `tool_selection_accuracy`, `argument_validity_rate`, `task_success_rate`, `security_violation_rate`, `degraded_outcome_rate`), инварианты безопасности (`unauthorized_tool_executed`, `cross_customer_data_exposure`, `secret_leak`, `network_access_attempt`, …), запрет сети в прогоне, Replay на уровне `client`/`classifier`.
- Связанность: `Category` (10 значений SupportOps), запуск именно `SupportAgent`; инварианты частично предметные (customer/ticket).
- Live-режим — только дизайн (untracked), не реализован.
- **Класс: C** (концепция — A; привязка к SupportAgent/Category и к `customer_id` — C). Tenant-инвариантов нет. **В `main` отсутствует.** Не считать готовым активом: переработка входит в смету HX-1.

### 3.14 Configuration — `config.py`, `llm_mode.py`

- Явное чтение окружения (не при import), Demo по умолчанию без сети и секретов, Live fail-closed, `SecretStr`. **Класс: A.** Префикс `SUPPORTOPS_` — технический идентификатор (см. план миграции).

### 3.15 Данные предметной области — `mock_data.py`, `tools.py`

- Глобальные словари `CUSTOMERS/ORDERS/TRACKING/CASES` (изменяемый `CASES`), `BUSINESS_DATE` константой. `OrderDataPort` упомянут в дизайне API-003, **в коде отсутствует.**
- **Класс: D** и **E** (порт данных per tenant).

### 3.16 Не найдено в репозитории [Ф — отсутствие подтверждено поиском]

Email/SMTP/IMAP/webhook/Telegram/CRM-код — нет (упоминания только в дизайн-документах как «отложено»). Human task / approval — нет (только `APPROVAL_REQUIRED` и `HUMAN_REVIEW` как исходы без потребителя). Capability Registry, делегирование субагентам, параллельное исполнение, отмена, durable execution — нет.

---

## 4. Матрица переиспользования (Deliverable D)

| Компонент | Класс | Что переиспользуется | Что требует изменений | Уверенность |
|---|---|---|---|---|
| Authorization Gate (протокол политики, deny-by-default) | **A** (поведение для HERMES — C/E) | deny-by-default, reason codes, fail-closed | `has_side_effects`, approval, tenant не используются | Высокая для A, средняя для целого |
| Gate: tenant/capability/бюджет/делегирование | **E** | — | расширение `ExecutionContext` или новая политика | Высокая |
| Tool Executor | **B/C** | конвейер, sanitization, trusted binding | реестр как параметр; обобщение binding | Средняя |
| Tool Registry | **C** | `ToolDefinition` | per-tenant/per-run сборка реестра | Средняя |
| Agent loop | **C** | скелет, изоляция прогона, seal trace | провайдер, этапы конвейера как плагины, модель из конфигурации | Средняя |
| Trace sanitization (SR-4) | **A** (принцип) / **C** (код) | ограничители, закрытые словари | вынести из `agent.py`; трудоёмкость не оценена | Средняя |
| Run/Step trace | **B** | модель, `summary()` | типы шагов, cost, outcome | Средняя |
| Refund policy/engine | **D** | паттерн «LLM предлагает → детерминированное решает» | — | Высокая |
| Classification | **D**/**C** | протокол, fail-closed | набор интентов из конфигурации | Средняя |
| TenantContext + auth | **A** | всё | — | Высокая |
| Tenant → agent core | **E** | — | связывание tenant с `ExecutionContext` и данными | Высокая |
| Credentials, pool, migrations | **A** | всё | — | Высокая |
| `runs` (lease/fencing) | **C** | идея lease, fencing, CHECK | схема переделывается: привязка к событию, несколько прогонов на сущность | Средняя |
| `tickets` | **D** | паттерн иммутабельности, идемпотентности | — | Высокая |
| Worker (claim/lease/retry) | **E** | схема и индексы | весь код | Высокая |
| Config (Demo/Live) | **A** | всё | — | Высокая |
| Evaluation Harness | **C** | инварианты, метрики-исходы, no-network, replay | абстрагировать от `SupportAgent` и `Category`; влить в `main` | Средняя |
| Observability (cost/outcome) | **E** | `summary()` как источник | стоимость, привязка к исходу | Высокая |
| Capability Registry/Contracts | **E** | — | отсутствует | Высокая |
| Orchestration (multi-module, пауза, параллель) | **E** | — | отсутствует | Высокая |
| Channels (email, chat, webhook) | **E** | каркас API | отсутствует | Высокая |
| Human task / approval | **E** | исходы `APPROVAL_REQUIRED`, `HUMAN_REVIEW` | отсутствует | Высокая |
| Данные предметной области | **D**/**E** | — | порт данных per tenant | Высокая |

**Честный итог [В]:** компонентов класса A — те, что касаются **безопасности и сервисной оболочки**. Компонентов, составляющих **собственно оркестрацию**, в классе A/B нет: они в классе E.
