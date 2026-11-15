# SupportOps → бизнес-сервис: Discovery & Design (API-003 Product Track)

> **Статус:** предложение, ревизия 3. Только проектирование; production-код не затрагивается.
> **Основа:** ветка `design/supportops-api-003` от `2e4a149` (worktree `../supportops-agent-api-003`). С момента ревизии 2 `main` ушёл вперёд: SR-4 влит (`5d1459c`, metadata-only trace), governance (`5f90c84`). Фактические ссылки на «HEAD `2e4a149`» ниже описывают состояние кода на момент исследования; ветка дизайна в `main` не влита и не перебазирована.
> **Не затрагивается:** production-код, Authorization Gate, RefundPolicyEngine, MONOLITH Core, SEC-003.
> **Автор:** Klodius Beta. Push и merge нет.
> **Ревизия 3 (решения владельца):** D-1 идентификатор `SUPPORTOPS-API-004`; D-6 «не более одного support case на ticket» — **временное бизнес-ограничение Demo MVP**, не универсальный контракт платформы, с обязательным стабильным operation identifier; D-8 только синтетические данные, реальный запуск — отдельный compliance review; D-10/D-11 регистрация в MONOLITH и параллельная работа Alpha/Beta разрешены при разделении файлов и worktrees; D-12 два режима LLM — Demo (fake LLM, детерминированно, без сети) и Live (реальный LLM, только синтетические тикеты, явная конфигурация). Тесты работают без внешнего LLM. Нумерация WP-1…WP-9 в этом документе — собственная; она **не совпадает** с WP SEC-003 и с Mission API-004 (см. §16).
> **Утверждено владельцем:** D-2 backend-to-backend API; D-3 PostgreSQL (один API, один worker, одна БД); D-4 синтетический Demo Tenant без реальных данных и CRM; D-5 стек FastAPI + Pydantic + драйвер PostgreSQL + ASGI-сервер (установка зависимостей — отдельным шагом с подтверждением). Автоматический replay бизнес-операций запрещён, пока не решена стабильная идемпотентность (§13).
> **Что изменилось в ревизии 2:** §4 (fencing по `attempt`), §5 (схема «сейчас/позже»), §6.3, §7, §8 (пакеты), §10 (статусы решений); новые §11–§16 (контракты, срез, идемпотентность, lifecycle, данные, идентификатор Story).

**Коллизия имён.** В `roadmap-index.md` идентификатор `SUPPORTOPS-API-003` уже занят историей «Production Security & Deployment» (Phase 7, PLANNED). Этот трек его не использует. Рабочее название до решения владельца — **SupportOps Service API Track**; предложение по идентификатору — §16.

---

## 1. Existing Architecture

Факты проверены по коду на HEAD. Модуль, строки.

**Конвейер одного прогона** (`agent.py`, `SupportAgent.run`):
`SupportTicket` → классификация (LLM, fail-closed) → `RefundEvaluationOrchestrator` (LLM предлагает intent и order_id, детерминированный `RefundPolicyEngine` решает) → цикл LLM ↔ tools (до `MAX_AGENT_STEPS=10`) → `compose_final_answer` (политика имеет приоритет над текстом LLM) → строка-ответ + `AgentRun` (trace).

**Границы доверия, уже реализованные:**

| Механизм | Где | Что гарантирует |
|---|---|---|
| Authorization Gate | `authorization.py`, `tool_executor.py` | deny-by-default; решение только по `ToolDefinition` + `ExecutionContext`, не по тексту, классификации или аргументам |
| Trusted identity | `execute_tool_call(trusted_customer_id, trusted_ticket_id)` | идентичность идёт из `SupportTicket`, который строит приложение. В аргументах tool для неё нет поля (`extra="forbid"`) |
| Object-level ownership | `tools.get_owned_order` | чужой и несуществующий объект неразличимы |
| Policy engine | `policy.py` | детерминированный, без LLM/сети/БД |
| Sanitization | `_failure_code`, `SAFE_ERROR_CODES` | текст исключений не попадает ни в trace, ни в LLM |
| Run isolation | `_run_lock`, `_run_owner` | один экземпляр — один активный `run()`, иначе `ConcurrentRunError` |
| Идемпотентный эффект | `tools._case_id` | `create_support_case` идемпотентен по `sha256(ticket_id + нормализованный reason)` — **только при дословно совпадающем `reason`, а его формулирует LLM (§13)** |

**Единственная конфигурация идентичности:** `SupportTicket.customer_id`. Docstring `SupportAgent.__init__` прямо говорит, что приложение обязано получить её из аутентифицированной сессии. Сейчас этого приложения нет, роль играет `run_agent.py` с захардкоженным демо-тикетом.

**Объём:** ~2 800 строк в `src/`, около 20 тестовых файлов. Все тесты офлайн (fake OpenAI).

---

## 2. Production Readiness Gaps

Разделено на **MVP-блокеры** (без них сервис нельзя безопасно открыть реальному обращению) и **отложенное**.

### 2.1 MVP-блокеры

| # | Пробел | Доказательство |
|---|---|---|
| G1 | **Нет HTTP-слоя.** Точка входа — демо-скрипт. | `run_agent.py`; в `pyproject.toml` нет web-фреймворка |
| G2 | **Нет источника идентичности.** Некому аутентифицировать вызывающего и поставить `customer_id`. | docstring `SupportAgent.__init__` |
| G3 | **Бизнес-данные — глобальные in-memory словари**, включая изменяемый `CASES`. Нет порта к реальной системе заказов. | `mock_data.CUSTOMERS/ORDERS/TRACKING/CASES`, импортируются прямо в `tools.py` |
| G4 | **Нет понятия tenant.** | `grep -i tenant src/` пуст. Все данные и кейсы глобальны, в процессе один «магазин» |
| G5 | **Trace живёт только в памяти** и пропадает вместе с процессом. | `AgentRunManager.run` |
| G6 | **Trace на HEAD хранит сырые данные.** Шаг tool-execution пишет `arguments` и `output`, а `lookup_customer` возвращает email. Сохранять такой trace в БД нельзя. SR-4 (metadata-only trace) это исправляет и **уже влит в `main` (`5d1459c`)**; на HEAD `2e4a149` (основа ревизии 2) исправления нет. | `agent.py:execute_function_calls`; `tests/test_trace_metadata_only.py` (в `main`) |
| G7 | **Нет жизненного цикла прогона вне процесса.** Нет очереди, lease, повторной попытки. Прогон — синхронный вызов в одном потоке. | `SupportAgent.run`, блокирующий `OpenAI` client |
| G8 | **Нет идемпотентности запроса.** Двойной вызов с тем же тикетом даёт два платных прогона. | Идемпотентен только `create_support_case` |
| G9 | **Нет конфигурации и дедлайнов.** `MODEL_NAME` и лимиты — константы. Нет таймаута на прогон, `OpenAI()` с дефолтами SDK. Структурированного логирования нет (3 упоминания `logging`). | `agent.py:MODEL_NAME`, `run_agent.py`; API-002 покрывает это отдельно |
| G10 | **`SupportAgent` — одноразовый stateful-объект**, а не сервис. Синглтон на процесс сломается на втором параллельном запросе (`ConcurrentRunError`). | `_run_lock` |

### 2.2 Отложенное (не нужно в первом MVP)

Human escalation и approval workflow (ACT-002/003); полноценный lifecycle тикета (ACT-001); пошаговое возобновление прогона; исходящие webhooks; multi-tenant self-service onboarding; RLS в БД как единственный барьер; UI оператора; метрики и dashboard (API-002); мультирегион; exactly-once для внешних эффектов (см. §6, невозможно в общем случае).

---

## 3. Proposed API Architecture

### 3.1 Форма

Один процесс `api` (FastAPI, sync-эндпоинты) плюс воркер в том же образе. Для первого MVP воркер — отдельный поток или процесс, берущий задания из таблицы `runs`. Отдельная очередь (Redis/SQS/Celery) не нужна: БД даёт атомарный захват (`SELECT … FOR UPDATE SKIP LOCKED`).

```
Tenant backend ──HTTPS+API key──▶ api ──┬─▶ tickets / runs (PostgreSQL)
                                        │
worker ◀── claim run (lease) ───────────┘
  └─ SupportAgent (новый экземпляр на прогон) ─▶ OpenAI
        └─ tools ─▶ OrderDataPort ─▶ данные tenant (см. D-4)
```

**Новый `SupportAgent` на каждый прогон.** Он дешёвый (клиент и классификатор общие). Единый экземпляр запрещён блокировкой SEC-003/M04, и этот контракт трогать нельзя.

### 3.2 Модель доверия вызывающего

В MVP клиентов-людей к API не подключаем. Вызывает **бэкенд tenant** (магазина), который уже аутентифицировал своего покупателя.

- Credential (API key) определяет `tenant_id`. Это единственный источник tenant.
- `customer_id` задаёт аутентифицированный бэкенд tenant при создании тикета. Он доверенный по контракту: tenant — авторитет для собственных клиентов.
- Текст обращения и всё, что вернул LLM, — недоверенные (как сейчас).
- Прямой доступ конечных покупателей (JWT от провайдера идентичности) — отложено. Контракт API при этом не меняется: меняется только способ получения `tenant_id`/`customer_id`.

### 3.3 Эндпоинты (минимальный набор)

Все под `/v1`, JSON, `Authorization: Bearer <api_key>`. Ошибки в едином формате `{"error": {"code": "...", "request_id": "..."}}` без текста исключений и без данных обращения.

**E1. `POST /v1/tickets` — принять обращение и поставить прогон в очередь**

| | |
|---|---|
| Назначение | Создать тикет и один прогон агента (`attempt=1`). Отдельный `POST /runs` в MVP не нужен: один тикет — один прогон. |
| Request | Заголовок `Idempotency-Key` (обязателен, ≤100 символов). Тело — поля `SupportTicket` без `ticket_id`: `{customer_id, message, order_id?}` (те же лимиты, `extra=forbid`). |
| Response | `202 {ticket_id, run_id, status:"accepted"}`. Повтор с тем же ключом и тем же телом → `200` с теми же id. |
| AuthN | API key → `tenant_id`, scope `tickets:write` |
| AuthZ | `tenant_id` из credential записывается в тикет и далее неизменяем |
| Идемпотентность | `UNIQUE(tenant_id, idempotency_key)` + `request_hash`. Тот же ключ с другим телом → `409 idempotency_key_reuse`, прогон не создаётся |
| Ошибки | `401` нет/неверный key; `403` нет scope; `409` конфликт ключа; `422` валидация (поля Pydantic без значений в ответе); `429` лимит; `503` БД недоступна |
| Границы доверия | `message` недоверенный → в LLM только как user content. `customer_id` — по контракту с tenant. `ticket_id`/`run_id` генерирует сервер |

**E2. `GET /v1/tickets/{ticket_id}` — статус и результат**

| | |
|---|---|
| Назначение | Опрос результата (вместо webhook в MVP) |
| Response | `200 {ticket_id, run_id, status: accepted\|running\|completed\|failed, answer?, failure?, created_at, finished_at?}`. `answer` только при `completed`; при `failed` — только безопасный `failure_code` из `SAFE_ERROR_CODES` |
| AuthN/AuthZ | scope `tickets:read`; выборка всегда `WHERE tenant_id = :t AND ticket_id = :id` |
| Ошибки | Чужой и несуществующий тикет → один и тот же `404` (неразличимы, по образцу `get_owned_order`) |
| Границы доверия | Ответ строится из записанного результата, а не из текущего состояния LLM. `answer` — уже прошедший `compose_final_answer` |

**E3. `GET /v1/runs/{run_id}/trace` — диагностика (поздняя стадия MVP)**

| | |
|---|---|
| Назначение | Отладка и поддержка интеграции |
| Response | Metadata-only trace: типы шагов, тайминги, `usage`, коды ошибок, `summary()` |
| AuthN | Отдельный scope `trace:read`, по умолчанию не выдаётся |
| AuthZ | Фильтр по `tenant_id`; каждое чтение пишется в аудит |
| Ограничение | SR-4 влит в `main` (G6); маршрут отложен до WP-8 |

**E4. `GET /healthz` и `GET /readyz`** — операционные, без аутентификации. `readyz` проверяет БД. Бизнес-данных не отдают.

**E5 (`PUT /v1/orders`) не входит в MVP.** D-4 утверждён как синтетический Demo Tenant: данные заказов — `mock_data` за Demo Tenant Adapter. Вопрос возвращается при выборе пилотного tenant (WP-9).

**Сознательно не добавляем:** `GET /tickets` (список), `POST /runs` (повторный запуск), `DELETE`, webhook-подписки, админ-API для tenants и ключей (в MVP ключи выдаёт оператор скриптом), эндпоинт «одобрить возврат».

---

## 4. Trust Boundaries

| # | Граница | Что считается доверенным | Контроль |
|---|---|---|---|
| TB1 | Интернет → API | ничего | TLS, лимит размера тела, rate limit на tenant, валидация Pydantic `extra=forbid` |
| TB2 | API key → `tenant_id` | результат поиска credential | ключи хранятся хэшем; сравнение константного времени; неактивный ключ = 401. `tenant_id` никогда не берётся из тела и заголовков |
| TB3 | `customer_id` от tenant | по контракту tenant | фиксируется при создании, неизменяем; в прогон попадает только из сохранённого тикета |
| TB4 | Текст обращения | **недоверенный** | существующие меры: Gate, политика, fail-closed классификация |
| TB5 | Вывод LLM | **недоверенный** | Gate + `ToolCall`-валидация + policy-first ответ (без изменений) |
| TB6 | Tools → данные заказов | данные того же `tenant_id` | порт `OrderDataPort` получает `tenant_id` из контекста прогона (как `trusted_customer_id`), не из аргументов LLM |
| TB7 | Приложение → БД | запросы с `tenant_id` | все репозиторные методы принимают `tenant_id` обязательным параметром без значения по умолчанию. Row-Level Security — второй эшелон, не замена |
| TB8 | Egress: trace, логи, ответы ошибок | только метаданные | единый контракт SR-4; фиксированные коды ошибок |
| TB9 | Egress: OpenAI | **данные покупателя уходят третьей стороне** | вне контроля кода: нужны DPA, retention-настройки провайдера, решение владельца (D-8) |
| TB10 | Воркер ↔ БД | lease и fencing по `attempt` | запись результата только с условием `WHERE run_id=… AND attempt=… AND status='running'` (защита от «зависшего» воркера) |

**Правило вывода для тестов:** чужой tenant, чужой клиент и несуществующий объект наружу неразличимы (`404`).

---

## 5. Persistence Strategy

### 5.1 Минимальный набор

Решение владельца D-3: **PostgreSQL**; для MVP — одна БД, один API, один worker. SQLite допустим только как тестовая подмена за интерфейсом репозитория, но не как целевое хранилище. Причины выбора: атомарный захват заданий (`FOR UPDATE SKIP LOCKED`), уникальные ограничения как примитив идемпотентности, составные внешние ключи как защита изоляции tenant.

Прежний дизайн содержал семь таблиц. Для первого вертикального среза нужны **три**:

| Таблица | Срез 1 | Обоснование |
|---|---|---|
| `api_credentials` | **Нужна сейчас** | Единственный источник `tenant_id` и scopes (TB2) |
| `tickets` | **Нужна сейчас** | Доверенная неизменная привязка `tenant_id + customer_id + ticket_id`; ключ идемпотентности запроса |
| `runs` | **Нужна сейчас** | Lifecycle, захват, результат. Источник истины очереди (не память процесса) |
| `tenants` | Позже | Один Demo Tenant: `tenant_id` — slug в `api_credentials`. Таблица нужна со вторым tenant (статус, отключение, FK) и добавляется аддитивной миграцией. Цена отсрочки: нет FK-целостности по tenant, пока он один |
| `support_cases` | Позже (WP-6) | Появляется вместе со стабильным бизнес-ключом (§13). `create_support_case` сейчас менять нельзя; в срезе кейсы остаются в `mock_data.CASES` (не durable — известное ограничение среза) |
| `run_steps` | Позже (WP-8) | Зависит от SR-4 (metadata-only trace) |
| `audit_events` | Позже (WP-8) | Первый потребитель — аудит чтения trace. До этого — структурный лог с `request_id` |

Эскиз (иллюстрация для ревью, не миграция):

```sql
api_credentials(
  credential_id uuid PRIMARY KEY,
  tenant_id     text   NOT NULL,                 -- slug, напр. 'demo'
  key_prefix    text   NOT NULL UNIQUE,          -- для поиска; не секрет
  key_hash      bytea  NOT NULL,                 -- HMAC-SHA256(server_secret, key); ключ не хранится
  scopes        text[] NOT NULL,                 -- tickets:write, tickets:read, trace:read
  created_at    timestamptz NOT NULL DEFAULT now(),
  revoked_at    timestamptz)

tickets(                                          -- append-only
  ticket_id       text PRIMARY KEY,               -- 'tkt_' + 32 hex, генерирует сервер
  tenant_id       text NOT NULL,
  customer_id     text NOT NULL,                  -- задаёт доверенный backend tenant
  message         text NOT NULL,
  order_id        text,
  idempotency_key text  NOT NULL,
  request_hash    bytea NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, idempotency_key),
  UNIQUE (tenant_id, ticket_id))                  -- цель составного FK

runs(
  run_id        text PRIMARY KEY,                 -- 'run_' + 32 hex
  tenant_id     text NOT NULL,
  ticket_id     text NOT NULL,
  status        text NOT NULL CHECK (status IN ('accepted','running','completed','failed')),
  attempt       int  NOT NULL DEFAULT 0,          -- он же fencing-токен
  max_attempts  int  NOT NULL DEFAULT 1,
  lease_owner   text,
  lease_until   timestamptz,
  answer        text,
  failure_code  text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  finished_at   timestamptz,
  UNIQUE (ticket_id),                             -- один тикет — один run
  FOREIGN KEY (tenant_id, ticket_id) REFERENCES tickets (tenant_id, ticket_id),
  CHECK (status <> 'completed' OR answer IS NOT NULL),
  CHECK (status <> 'failed'    OR failure_code IS NOT NULL))
```

Инварианты схемы:

- `tickets` только вставляется и читается. Роль приложения имеет `INSERT, SELECT` без `UPDATE/DELETE`: привязка `tenant_id + customer_id + ticket_id` неизменна на уровне БД, а не только кода.
- Составной FK не даёт `runs` принадлежать другому tenant, чем его тикет.
- Попытки — счётчик `attempt`, не строки. Захват увеличивает его, запись результата возможна только `WHERE attempt=:a AND status='running'`.
- Идентификаторы случайные (128 бит), не последовательные; запрос чужого id неотличим от несуществующего.
- Каждый метод репозитория для запросов API принимает `tenant_id` обязательным параметром без значения по умолчанию. Worker читает run по `run_id` и берёт tenant из строки.

### 5.2 Что хранится, что нет

- `tickets.message` — это ПД. В MVP хранится, потому что прогон должен пережить рестарт и его можно повторить. Нужны срок хранения и процедура удаления (D-8).
- `run_steps` — **только** то, что разрешит SR-4: типы, тайминги, usage, коды. Persist-слой не принимает произвольный dict: он принимает типизированную структуру метаданных.
- Ответ LLM, `arguments` и `output` tools в БД не сохраняются (кроме финального `answer`, который и есть продукт).

### 5.3 Ключевая зависимость

Персистентность trace (`run_steps`) опирается на контракт SR-4 (в `main` с `5d1459c`); перед WP-8 контракт нужно перечитать по актуальному коду. Пакеты WP-1…WP-7 от SR-4 не зависят.

---

## 6. Durable State Reuse

Источник: `the_monolith/docs/adr/ADR-017-durable-execution-state.md` (статус в самом ADR — `proposed`) и `src/durable/` (TypeScript, ~15 тыс. строк вместе с тестами). Это ADR и код, не публичный SDK, и нигде нет Python-реализации.

### 6.1 Вывод

**Код не копировать, runtime-зависимости не создавать. Заимствовать пять идей.**

Причины отказа от переноса:

1. Это TypeScript. Перенос означает переписывание, а не копирование.
2. Модель рассчитана на другую задачу: долгие агентные задачи с ожиданием ответов днями, план-DAG, допущения/доказательства, машина времени. Прогон SupportOps — секунды-минуты и укладывается в одну транзакционную цепочку.
3. Бэкенд — файловая система с `link()`, сегментами, tombstone и prune-протоколом (Deккер на create-only примитивах). Весь этот объём решает проблему «нет транзакционной БД». У нас она есть.
4. ADR-017 сам называет универсальность ценой (domain-neutral JSON). Строить универсальный workflow engine нам запрещено задачей.
5. ADR отмечает недоверифицированные ограничения (NFS, часы между процессами) — наследовать их незачем.

### 6.2 Что заимствуем

| Идея ADR-017 | Реализация в SupportOps (через БД) | Когда |
|---|---|---|
| Запись как compare-and-set, а не блокировка | `UPDATE … WHERE status='accepted'` / `WHERE attempt=:a`; уникальные ключи | MVP |
| **Lease + fencing epoch.** Зависший воркер не должен писать после перехвата | `lease_until`, `attempt` как fencing-токен; результат пишется только при совпадении `attempt` (иначе отбрасывается) | MVP |
| Идемпотентность как предусловие повтора | Прогон повторяется только если эффекты идемпотентны по стабильному ключу (§13). До этого stale-прогон → `failed` | Повтор — после WP-6 |
| Честность о внешних эффектах: `intended → completed / failed / uncertain` | Таблица `effects` с этими состояниями | **Не MVP.** Нужна, когда появятся эффекты, не идемпотентные по самой природе (возврат денег, письмо) — ACT-003 |
| Append-only журнал событий | `run_steps` и `audit_events` только вставляются | MVP |

Хэш-цепочка, сегменты, prune, проекция «состояние в момент X», assess, DAG плана — **не берём**.

### 6.3 Восстановление: сначала fail-safe, потом повтор

Решение владельца: автоматический replay бизнес-операций запрещён, пока не решена стабильная идемпотентность. Поэтому:

1. **Срез 1 (до WP-6):** lease истёк → прогон переводится в `failed` с кодом `worker_lost`; `max_attempts=1`. Повтор — только явным новым запросом клиента (новый `Idempotency-Key`).
2. **После WP-6:** lease истёк → прогон возвращается в `accepted` (`attempt+1`) до `max_attempts`, затем `failed`.

Пошаговый resume не нужен в обоих режимах: LLM-вызовы не эффекты, а расход. Пункт 2 заблокирован, потому что единственный эффект `create_support_case` **сейчас не стабилен** (§13.1).

---

## 7. MVP Scope

Цель MVP — **первый работающий вертикальный срез** (§12), а не платформа.

### 7.1 Срез 1 (MUST)

1. Пакет `supportops_service` (FastAPI): `POST /v1/tickets`, `GET /v1/tickets/{id}`, `/healthz`, `/readyz`.
2. API-key аутентификация: `tenant_id` и scopes только из credential.
3. PostgreSQL: `api_credentials`, `tickets`, `runs`.
4. Lifecycle `accepted → running → completed | failed`; один worker; захват через `SKIP LOCKED`; fencing по `attempt`.
5. Идемпотентность запроса `POST /v1/tickets`.
6. Новый `SupportAgent` на каждый прогон; таймауты клиента LLM.
7. Demo Tenant Adapter = существующий `mock_data`, с жёстким запретом исполнения для любого tenant, кроме `demo` (иначе глобальные данные станут общими для tenants).
8. Устаревший прогон → `failed(worker_lost)`; **автоматического повтора нет**.
9. Тесты через HTTP (свой/чужой tenant, повтор ключа, `tenant_id`/`customer_id` подмены в теле, падение worker).

### 7.2 До первого реального tenant (не в срезе 1)

Стабильный бизнес-ключ эффекта и durable `support_cases` (WP-6); автоматическое восстановление (WP-7); trace API (WP-8, после SR-4); `OrderDataPort`, реальный адаптер, retention, rate limit, RLS, хостинг (WP-9); решения §15.

### 7.3 Не в MVP (WON'T)

Escalation, approvals, webhooks, пошаговый resume, журнал внешних эффектов, UI, самообслуживание tenant, прямой доступ покупателей, дашборды, мультирегион, список тикетов, ручной перезапуск прогона.

---

## 8. Work Packages

Последовательность соответствует указанию владельца. Каждый пакет оставляет `main` рабочим и тестируемым офлайн (кроме тестов с PostgreSQL). Размеры не оценивались.

### 8.1 Что можно до завершения SEC-003

| WP | Касается файлов SEC-003 | Технически | Процессно |
|---|---|---|---|
| WP-1, WP-2, WP-3 | Нет: новый пакет `supportops_service`, общие только `pyproject.toml`/`uv.lock` | Можно | Параллельная работа разрешена владельцем (D-11) при разделении файлов и worktrees |
| WP-4, WP-5 | Вызывают публичный `SupportAgent.run`; SR-4/SR-5 могут менять `agent.py` | Лучше после M04 | После M04 |
| WP-6 | Да: `tools.py` и тесты SEC-003 | **Только после завершения SEC-003** | После |
| WP-7 | Нет, но требует WP-6 | После WP-6 | После |
| WP-8 | Да: контракт trace из SR-4 | SR-4 влит (`5d1459c`); перед стартом перечитать контракт | После |
| WP-9 | Нет | После решений D-8/D-9 | После |

### 8.2 Пакеты

**WP-1 API Skeleton**
- *Цель:* запускаемое приложение с контрактной «оболочкой», без БД и агента.
- *Компоненты:* `src/supportops_service/` (фабрика приложения, конфигурация из env, `request_id`, единый envelope ошибок, переопределённый обработчик 422 без значений input), `/healthz`, `/readyz`; группа зависимостей `service` в `pyproject.toml`.
- *Acceptance:* `/healthz` 200; неизвестный маршрут → 404 в envelope; 422 не содержит введённых значений; `X-Request-ID` возвращается всегда; `/docs` выключен по умолчанию; весь существующий набор тестов зелёный.
- *Тесты:* `TestClient`, без сети и БД.
- *Зависимости:* подтверждение установки (D-5).
- *Риски:* изменение `uv.lock`; дефолтный 422 FastAPI отражает input (ловушка, закрыта тестом).
- *Не входит:* аутентификация, БД, агент.

**WP-2 Authentication & Tenant Context**
- *Цель:* `Authorization: Bearer` → неизменяемый `TenantContext(tenant_id, credential_id, scopes)`.
- *Компоненты:* формат ключа (`prefix` + секрет ≥256 бит), HMAC-хэш, `CredentialRepository` (Protocol) + in-memory fake для тестов, зависимость `require_scope`.
- *Acceptance:* отсутствующий, искажённый, неизвестный и отозванный ключ дают **один и тот же** 401 (тело и заголовки, кроме `request_id`); нет scope → 403; `tenant_id` в теле/заголовке (`X-Tenant-Id`) не влияет и в теле отклоняется (422); сравнение — `hmac.compare_digest`; ключ не попадает в логи (тест).
- *Тесты:* юнит + HTTP.
- *Зависимости:* WP-1.
- *Риски:* формат ключа тяжело менять после выдачи — зафиксировать сразу.
- *Не входит:* PostgreSQL-реализация, выдача/ротация ключей через API.

**WP-3 Ticket Persistence**
- *Цель:* реальные `POST /v1/tickets` и `GET /v1/tickets/{id}`; прогон остаётся `accepted` (исполнителя ещё нет).
- *Компоненты:* миграции для трёх таблиц (предложение: версионированные SQL-файлы и минимальный runner с таблицей `schema_migrations`, без ORM; выбор финализирует реализация), PostgreSQL-репозитории с обязательным `tenant_id`, PG-реализация `CredentialRepository`, seed-скрипт Demo Tenant (ключ генерируется локально, печатается один раз, в БД только хэш).
- *Acceptance:* весь контракт §11.1–§11.2; изоляция двух tenant (чужой и несуществующий тикет неразличимы); N параллельных POST с одним ключом → 1 тикет, 1 run; ticket+run создаются в одной транзакции (инъекция сбоя → ни одной строки); роль приложения не может `UPDATE tickets`.
- *Тесты:* PG-интеграционные (пропускаются без PG); HTTP.
- *Зависимости:* WP-2; драйвер PostgreSQL (D-5).
- *Риски:* PG в CI; дрейф схемы без ORM.
- *Не входит:* исполнение прогона, `tenants`, `support_cases`, trace.

**WP-4 First Execution Worker**
- *Цель:* worker исполняет `SupportAgent.run()` и сохраняет результат.
- *Компоненты:* `python -m supportops_service.worker`; захват (`SKIP LOCKED`, `attempt+1`, lease); новый `SupportAgent` на каждый run; fenced-запись результата; отображение исключений в закрытый набор `failure_code`; дедлайн прогона и таймауты клиента LLM; guard «только tenant `demo`»; reaper-lite: просроченный `running` → `failed(worker_lost)`.
- *Acceptance:* `accepted → running → completed` с `answer` (fake LLM); ошибка → `failed` с кодом из закрытого набора, текст исключения нигде не появляется (тест с маркером); два worker на N заданиях — каждое выполнено ровно раз; запись «чужого» `attempt` отброшена; два run — два экземпляра `SupportAgent`; просроченный run **не** запускает `SupportAgent.run` повторно.
- *Тесты:* PG-интеграционные с fake LLM; тест гонки захвата.
- *Зависимости:* WP-3; технически — завершение M04.
- *Риски:* блокирующий вызов LLM не прерывается дедлайном (§14.5); `run()` возвращает только `str`, флага `degraded` нет — наружу отдавать только через подтверждённый доступ к `agent.last_run`, иначе не отдавать.
- *Не входит:* автоповтор, `run_steps`, несколько worker как продуктовая функция.

**WP-5 HTTP Vertical Slice**
- *Цель:* демонстрируемый сценарий §12.
- *Компоненты:* `docker-compose` (dev: PostgreSQL + api + worker), seed Demo Tenant, сценарий `curl` и e2e-тест; fake-LLM режим для CI; опциональный live-режим (синтетические данные, ключ LLM из окружения).
- *Acceptance:* создать → опрос → `completed` с ответом; повтор ключа → те же id; второй tenant не видит тикет (404); `kill -9` worker посреди прогона → после reaper `failed(worker_lost)`, следующий тикет проходит.
- *Зависимости:* WP-4.
- *Не входит:* прод-хостинг, реальные данные.

**WP-6 Stable Business Idempotency**
- *Цель:* не более одного кейса на `(tenant, ticket)` независимо от текста `reason` (§13).
- *Компоненты:* ключ в `create_support_case`; таблица `support_cases` с `UNIQUE (tenant_id, ticket_id)` и `INSERT … ON CONFLICT DO NOTHING RETURNING`; обновление описания tool для LLM и существующих тестов (`test_business_tools.py`, `test_trusted_ticket_binding.py`, `test_stale_trusted_context.py`).
- *Acceptance:* тесты T1–T10 (§13.6); форма ответа tool (`created`/`already_exists`) сохранена; Gate и RefundPolicyEngine не изменены.
- *Зависимости:* решение D-6; **завершение SEC-003**; WP-4.
- *Риски:* tenant должен дойти до обработчика без расширения `ExecutionContext` (затрагивает файлы SEC-003) — решить в дизайне пакета (инъекция хранилища эффектов вместо контекста).
- *Не входит:* журнал внешних эффектов, эскалация.

**WP-7 Recovery**
- *Цель:* автоматическое восстановление.
- *Компоненты:* reaper возвращает просроченный run в `accepted` (`attempt<max_attempts`, иначе `failed`); `max_attempts=2`.
- *Acceptance:* `kill -9` worker посреди прогона → прогон завершается с `attempt=2`, кейс по тикету ровно один; проснувшийся старый worker не перезаписывает результат; после `max_attempts` — `failed(worker_lost)`.
- *Зависимости:* WP-6 (жёстко), WP-5.
- *Не входит:* пошаговый resume, внешние эффекты.

**WP-8 Trace API**
- *Цель:* `GET /v1/runs/{id}/trace`.
- *Компоненты:* `run_steps` (типизированная metadata-only структура), `audit_events`, scope `trace:read`.
- *Acceptance:* тест сериализует всю строку БД и не находит синтетических маркеров PII; без scope → 403; чужой tenant → 404; каждое чтение пишет `audit_events`.
- *Зависимости:* **SR-4 влит**, WP-5.
- *Не входит:* поиск/список trace, экспорт.

**WP-9 Production Integrations** (намеренно не декомпозирован)
- `OrderDataPort` и адаптер первого реального tenant, `tenants`, RLS, rate limit, ротация ключей, retention-job, хостинг и секреты, наблюдаемость, прод-образ.
- *Зависимости:* D-8, D-9, выбор пилотного tenant, юридические решения §15.

**Критический путь:** WP-1 → WP-2 → WP-3 → WP-4 → WP-5 (срез готов). Ветка идемпотентности WP-6 → WP-7 идёт после и не блокирует демонстрацию среза.

---

## 9. Risks & Trade-offs

| # | Риск / компромисс | Влияние | Митигация |
|---|---|---|---|
| R-1 | **Дубли кейсов при повторном прогоне.** Идентичность кейса зависит от текста `reason` от LLM (§6.3) | Два кейса на один тикет после краха | §13: ключ «один кейс на тикет» (D-6). До его внедрения автоматический повтор прогона запрещён |
| R-2 | **Данные покупателя уходят в OpenAI** (TB9) | Юридический и репутационный риск, регуляторика | Решение владельца до первого реального обращения; DPA, retention, минимизация |
| R-3 | **ПД в БД** (`tickets.message`) | Утечка, обязательства по удалению | Retention-срок, шифрование диска, доступ только через репозиторий; удаление по тикету |
| R-4 | **Trace до SR-4 содержал PII** (на `2e4a149`) | Утечка при сохранении | SR-4 влит; WP-8 обязан тестом доказать отсутствие PII в сохранённых строках |
| R-5 | **Доверие к `customer_id` от tenant** | Скомпрометированный ключ позволяет выдавать себя за любого клиента этого tenant | Это границы MVP: ущерб ограничен одним tenant. Ключи отзываемы, аудит фиксирует вызывающего. Позже — подписанный токен покупателя |
| R-6 | **Синхронный стек.** `SupportAgent.run` блокирующий, воркер = потоки | Пропускная способность ограничена числом потоков и лимитами OpenAI | Достаточно для первого tenant. Переход на async — отдельное решение, контракт API его не затрагивает |
| R-7 | **Стоимость и злоупотребление.** Каждый тикет — 2+ платных вызова LLM | Расход, DoS кошельком | Rate limit на tenant, лимит размера, дедлайн, `max_attempts` |
| R-8 | **Prompt injection через `message`** | Манипуляция ответом | Уже закрыто архитектурой (Gate независим от текста; ответ — policy-first). SEC-004 расширит негативными сценариями |
| R-9 | **Выбор PostgreSQL** добавляет операционную зависимость | Сложнее локальный запуск | SQLite за интерфейсом для dev/тестов; docker-compose для разработки |
| R-10 | **Конфликты с SEC-003** в `agent.py`/`tool_executor.py`/`tools.py` | Переделка | Пакеты WP-1…WP-3 живут в новом пакете `supportops_service` и этих файлов не касаются; WP-4 начинается после M04; WP-6 — после завершения SEC-003 (§8) |
| R-11 | **Повторный прогон может дать иной ответ** | Тест «ответ стабилен» невозможен | Сохраняется ответ единственного завершившегося прогона; запись по условию `attempt` |
| R-12 | **Упрощение: одно-тенантное развёртывание** | Если реальных tenants будет несколько на одной БД, ошибка в фильтре = утечка | `tenant_id` в схеме с первого дня, обязательный параметр; RLS добавляется до подключения второго tenant |

**Главный компромисс.** Выбрано «fail-safe без автоповтора» (срез 1) → «повтор с начала + идемпотентные эффекты» (после WP-6) вместо пошагового возобновления. Цена среза 1: упавший прогон не восстанавливается сам, клиент должен отправить новый запрос. Выигрыш: нельзя создать дубликат эффекта повтором. Условие пересмотра: появление необратимого или внешнего эффекта (возврат, письмо клиенту) — тогда журнал эффектов `intended/completed/uncertain` из ADR-017 обязателен (ACT-003).

**Честная граница гарантий.** Exactly-once для внешнего мира недостижим. Достижимо: «не более одного кейса на `(tenant, ticket)`» (после WP-6), «результат прогона записывается один раз», «после краха прогон либо завершится, либо получит `failed` с кодом».

---

## 10. Decisions Required From Owner

| # | Решение | Статус | Рекомендация / примечание |
|---|---|---|---|
| D-1 | Идентификатор и место трека | **Утверждено** | `SUPPORTOPS-API-004` (§16) |
| D-2 | Backend-to-backend API | Утверждено | Покупатели напрямую — позже |
| D-3 | PostgreSQL, один API/worker/БД | Утверждено | SQLite только как тестовая подмена |
| D-4 | Синтетический Demo Tenant | Утверждено | Пилотный tenant и источник данных — к WP-9 |
| D-5 | FastAPI, Pydantic, драйвер PG, ASGI | Направление утверждено | Совместимость проверена (Приложение A). Установка — отдельным подтверждённым шагом на старте WP-1 |
| D-6 | Ключ эффекта `create_support_case` | **Утверждено (временно)** | Вариант A «не более одного case на ticket» — временное ограничение Demo MVP, не контракт платформы; предусмотреть стабильный operation identifier (§13.2). Код не менять до конца SEC-003 |
| D-7 | Trace API в первом релизе | Уточнено | Нет; WP-8 после SR-4 |
| D-8 | Политика данных, LLM-провайдер | **Утверждено в части разработки** | Только синтетические данные; реальный запуск требует отдельного compliance review (§15) |
| D-9 | Хостинг и секреты | Открыто | Нужно к WP-9 |
| D-10 | Оформление в MONOLITH (Story, Missions) | **Разрешено** | Регистрация без изменения SEC-003 и `active.md` (§16) |
| D-11 | Параллельная разработка Alpha/Beta | **Утверждено** | Разделение файлов и worktrees обязательно |
| D-12 | Режимы LLM | **Утверждено** | Demo Mode (fake LLM, fixtures, без сети) и Live Mode (реальный LLM, синтетические тикеты, явная конфигурация). Тесты без внешнего LLM |

---

## 11. API Contracts

### 11.0 Общие правила

- Префикс `/v1`, JSON UTF-8, `Authorization: Bearer <api_key>`. CORS не включается (клиенты — серверы).
- `X-Request-ID`: валидный входящий (`[A-Za-z0-9-]{1,64}`) принимается, иначе генерируется; возвращается всегда.
- `Cache-Control: no-store` на всех ответах `/v1`.
- Идентификаторы: `tkt_<32 hex>`, `run_<32 hex>`, генерирует только сервер.
- Лимит тела `POST /v1/tickets`: 16 KiB (верхняя граница полей — 4 000 символов `message`).
- Envelope ошибки: `{"error": {"code": "<фикс.>", "message": "<фикс. текст для кода>", "request_id": "…"}}`. Для 422 добавляется `fields: [{"path": [...], "type": "<код Pydantic>"}]` — **без значений input** (дефолт FastAPI их отражает, обработчик переопределяется в WP-1).
- Порядок проверок: аутентификация (401) → scope (403) → `Idempotency-Key` (400) → размер (413) → валидация (422) → операция. Аутентификация всегда раньше разбора тела.

### 11.1 `POST /v1/tickets`

| | |
|---|---|
| Scope | `tickets:write` |
| Заголовки | `Authorization`; `Idempotency-Key` (обязателен, `[A-Za-z0-9._:-]{8,100}`, рекомендуется UUID); `Content-Type: application/json` |
| Тело | `{"customer_id": str 1–100, "message": str 1–4000, "order_id": str 1–100 \| null}`; `extra=forbid` — `tenant_id`, `ticket_id`, `run_id` и любые другие поля → 422 (отклоняем, а не молча игнорируем: ошибку интеграции видно сразу) |
| Tenant binding | `tenant_id` берётся только из credential и записывается в тикет; `customer_id` — от доверенного backend tenant; после вставки неизменяемы (`tickets` append-only) |
| Успех | `202` `{"ticket_id", "run_id", "status": "accepted", "created_at"}` + `Location: /v1/tickets/{ticket_id}` |
| Повтор | `200`, те же `ticket_id`/`run_id`, текущий `status`, заголовок `Idempotent-Replayed: true` |

Семантика идемпотентности запроса (область ключа — tenant):

| Ситуация | Результат |
|---|---|
| Новый ключ | Тикет и run создаются одной транзакцией → 202 |
| Тот же ключ, то же каноническое тело | 200, те же id |
| Тот же ключ, другое тело | `409 idempotency_key_reuse`, ничего не создаётся |
| Одновременные первые запросы с одним ключом | Выигрывает `UNIQUE (tenant_id, idempotency_key)`; остальные получают 200 |
| Тот же ключ у другого tenant | Независим |

Каноническое тело: `sha256` от JSON `{customer_id, message, order_id}` после нормализации Pydantic (strip), ключи отсортированы. Ключ хранится, пока существует тикет.

| Код | `error.code` | Когда |
|---|---|---|
| 400 | `idempotency_key_required` | Нет/некорректный заголовок |
| 401 | `unauthenticated` | Нет, искажён, неизвестен, отозван ключ (одинаковый ответ) |
| 403 | `insufficient_scope` | Нет `tickets:write` |
| 409 | `idempotency_key_reuse` | См. выше |
| 413 | `payload_too_large` | Тело > 16 KiB |
| 415 | `unsupported_media_type` | Не JSON |
| 422 | `validation_error` | Схема |
| 429 | `rate_limited` + `Retry-After` | WP-9 |
| 503 | `service_unavailable` | БД недоступна |

### 11.2 `GET /v1/tickets/{ticket_id}`

| | |
|---|---|
| Scope | `tickets:read` |
| Авторизация объекта | Выборка всегда `WHERE tenant_id=:t AND ticket_id=:id`. Чужой, несуществующий и синтаксически некорректный id → один и тот же `404 not_found` |
| 200 | `{"ticket_id", "run_id", "status", "created_at", "started_at"?, "finished_at"?, "answer"?, "failure"?}` |
| `accepted`/`running` | Без `answer` и `failure` |
| `completed` | `answer` — строка, возвращённая `SupportAgent.run()` (уже прошла `compose_final_answer`) |
| `failed` | `failure: {"code": …}` из закрытого набора: `SAFE_ERROR_CODES` (`tool_executor.py`) ∪ {`deadline_exceeded`, `worker_lost`, `unsupported_tenant`} |
| Не возвращается | `message`, `customer_id`, `order_id`, trace, текст исключений |
| Прочие ошибки | 401, 403 (`insufficient_scope`), 503 |

`degraded` из первой версии документа убран: `run()` возвращает только `str`. Отдавать флаг можно лишь при подтверждённом доступе через `agent.last_run` (WP-4).

### 11.3 `GET /v1/runs/{run_id}/trace`

| | |
|---|---|
| Scope | Отдельный `trace:read`, по умолчанию не выдаётся |
| Доступ | `WHERE tenant_id=:t AND run_id=:id`; иначе единый 404 |
| Ответ | Только metadata-only: `{"run_id", "ticket_id", "status", "steps": [{"step_number", "type", "started_at", "duration_ms", "outcome_code", "usage"?}]}`. Точный набор полей определяет SR-4; всё вне закрытого списка отбрасывается типизированной структурой |
| Аудит | Каждое чтение → `audit_events` |
| Статус | **Маршрут не регистрируется** до WP-8: `run_steps` и `audit_events` должны существовать (SR-4 уже влит) |

Примечание: `run_id` в API — идентификатор сервисного run. Внутренний `AgentRun.run_id` (`uuid4`) принадлежит агенту и наружу не отдаётся.

### 11.4 Health

| | |
|---|---|
| `GET /healthz` | Liveness: `200 {"status":"ok"}`, без обращений к зависимостям |
| `GET /readyz` | Readiness: `200`, если БД доступна и версия схемы равна ожидаемой; иначе `503 {"status":"unavailable"}` без причин. Состояние worker в readiness API **не** входит |
| Аутентификация | Нет. В развёртывании доступны только во внутренней сети. Бизнес-данных не отдают |

Других endpoint-ов нет. Обоснование отсутствия `POST /runs`, списков, DELETE, webhooks — §3.3.

---

## 12. First Vertical Slice

```text
POST /v1/tickets                        [WP-3]
  ├─ authenticate key → TenantContext   [WP-2]  api_credentials
  ├─ validate, canonical hash           [WP-3]
  ├─ INSERT tickets + runs(accepted)    [WP-3]  одна транзакция; ON CONFLICT по (tenant, key)
  └─ 202 {ticket_id, run_id}

worker loop                             [WP-4]
  ├─ claim: accepted → running          attempt+1, lease  (SKIP LOCKED)
  ├─ guard: tenant == 'demo'            иначе failed(unsupported_tenant)
  ├─ SupportAgent().run(SupportTicket)  новый экземпляр; данные — mock_data (Demo Tenant Adapter)
  └─ commit: running → completed|failed WHERE attempt=:a AND status='running'

GET /v1/tickets/{id}                    [WP-3 + WP-4]
  └─ WHERE tenant_id AND ticket_id → status, answer | failure
```

Демонстрация (WP-5), только синтетические данные:

1. `docker compose up` (PostgreSQL, api, worker); seed создаёт Demo Tenant и печатает ключ один раз.
2. `curl POST /v1/tickets` с демо-заказом из `mock_data` → `202`.
3. Опрос `GET /v1/tickets/{id}` → `accepted → running → completed` с ответом.
4. Повтор POST с тем же ключом → `200`, те же id. Другое тело с тем же ключом → `409`.
5. Второй ключ другого tenant → `GET` того же тикета даёт `404`.
6. `kill -9` worker посреди прогона → тикет получает `failed(worker_lost)`, следующий тикет проходит.

Ограничения среза, названные явно: кейсы живут в `mock_data.CASES` и теряются при рестарте worker; повтор прогона не выполняется; trace не хранится; `demo` — единственный допустимый tenant исполнения.

---

## 13. Idempotency Design

### 13.1 Что делает код сейчас

Факты по HEAD `2e4a149` (`tools.py`, `tool_definitions.py`):

- `_case_id` (строки 148–152): `"case_" + sha256(ticket_id + "\n" + " ".join(reason.split()).casefold())[:10]` — идентичность задают `ticket_id` и **нормализованный `reason`**. Нормализация — схлопывание пробелов и `casefold`; смысловые перефразировки разными ключами остаются.
- `reason` — аргумент LLM (`CreateSupportCaseArguments.reason`, ≤1000 символов), то есть недоверенный и недетерминированный. `priority` в идентичность не входит.
- `create_support_case` (строки 161–205): проверка наличия и запись в `mock_data.CASES` — две отдельные операции без атомарности. В одном процессе под GIL это сходит с рук, на БД даст гонку.
- `CASES` — глобальный словарь процесса: пропадает при рестарте.
- Усечённый хэш 40 бит; при коллизии возвращается `ticket_context_required`, то есть легитимный запрос получает вводящий в заблуждение отказ (вероятность пренебрежимо мала).
- Найденные тесты проверяют только повтор с тем же `reason` (`test_business_tools.py:143`, `test_trusted_ticket_binding.py:205-211`, `test_stale_trusted_context.py:193`). Теста на разные `reason` я не нашёл.

**Вывод.** Утверждение «идемпотентен» верно только для дословно одинакового текста. Три сценария создают дубликат:

1. Повторный прогон после краха: LLM иначе сформулировала `reason`.
2. Один прогон: цикл допускает до 10 шагов, LLM может вызвать tool дважды с разными `reason`.
3. Любая будущая БД-реализация без уникального ограничения по тикету.

### 13.2 Стабильный бизнес-ключ

Ключ эффекта определяется **доверенными** величинами, а не текстом LLM:

```text
effect_key = (tenant_id, ticket_id, kind="support_case")
```

Смысл: «на тикет — не более одного открытого кейса». Это **временное бизнес-ограничение Demo MVP** (D-6), а не универсальный контракт платформы. Чтобы не закрепить его навсегда, хранилище эффектов должно использовать стабильный operation identifier (`kind` в ключе выше), позволяющий позже ввести несколько операций на тикет без смены схемы идентичности.

| Вариант | Ключ | Оценка |
|---|---|---|
| **A (рекомендуется)** | `(tenant, ticket)` | Просто, детерминированно, закрывает сценарии 1–3. Потеря: второй вызов с иной причиной не создаёт кейс (возвращает существующий; добавление заметки — позже) |
| B | `(tenant, ticket, reason_code)`, `reason_code` — закрытый enum, выводимый детерминированно из решения политики/классификации | Точнее, но требует модели решения тикета (ACT-001); не для MVP |
| C | `(run_id, …)` | Не защищает от повторного прогона. Отклонён |
| D | как сейчас: `hash(reason)` | Корень проблемы. Отклонён |
| E | ключ, присланный LLM | Недоверенный источник. Отклонён |

Реализация (WP-6): `UNIQUE (tenant_id, ticket_id)` на `support_cases`, `INSERT … ON CONFLICT DO NOTHING RETURNING` и чтение существующей строки при конфликте. Идентификатор кейса не должен зависеть от `reason`. Это **меняет** `create_support_case`, его описание для LLM и тесты, поэтому требует D-6 и откладывается до конца SEC-003. На этом этапе код не менять.

### 13.3 Граница идемпотентности

| Уровень | Ключ | Механизм | Что гарантирует |
|---|---|---|---|
| Запрос API | `(tenant_id, Idempotency-Key)` | `UNIQUE` + `request_hash` | Один тикет и один run на логический запрос |
| Run | `run_id`, `attempt` | CAS-запись результата | Результат фиксируется один раз; устаревший worker не пишет |
| Эффект | `(tenant_id, ticket_id, kind)` | `UNIQUE` + `ON CONFLICT` | Не более одного кейса на тикет |
| Вызов LLM, классификация | — | Не идемпотентны | Повтор стоит денег и может дать другой текст; **не эффект** |

Содержимое ответа LLM не стабильно; стабильны только идентичность и единственность записанного результата.

### 13.4 Поведение

**Повторный запрос.** По §11.1. Дополнительного прогона не создаётся.

**Crash.**

| Точка сбоя | Состояние | Срез 1 (до WP-6) | После WP-6/WP-7 |
|---|---|---|---|
| API упал после COMMIT, до ответа | тикет и run есть | Клиент повторяет тот же ключ → 200 с теми же id | То же |
| Тикет принят, worker не успел взять | `accepted` | Взят при следующем опросе | То же |
| Сбой во время LLM, эффекта ещё нет | `running`, lease истёк | `failed(worker_lost)` | Возврат в `accepted`, `attempt+1` |
| Сбой после эффекта, до записи результата | `running`, lease истёк | `failed(worker_lost)`; в срезе кейс в памяти, он потерян | Возврат в `accepted`; `ON CONFLICT` вернёт существующий кейс, дубликата нет |
| Сбой после записи результата | `completed` | Ничего | Ничего |

**Неизвестный результат внешнего эффекта.** Для кейса в нашей БД состояния «неизвестно» нет: вставка атомарна. Оно появляется у внешних систем (CRM, письмо, платёж). Правила на будущее (ACT-003), заранее фиксируемые:

1. В систему передаётся ключ идемпотентности провайдера, равный `effect_key`.
2. Намерение записывается в журнал (`intended`) до вызова, исход (`completed/failed/unknown`) — после.
3. `unknown` — **не повторять автоматически**: run → `failed(effect_outcome_unknown)`, разбор вручную.
4. Необратимый эффект без идемпотентности у провайдера допускается только через `requires_approval` и журнал.

### 13.5 Что запрещено в MVP

Любой автоматический повтор run до WP-6; добавление tool с внешним эффектом; ключ эффекта из аргументов LLM.

### 13.6 Предлагаемые тесты

| # | Тест | Ожидание |
|---|---|---|
| T1 | Два прогона одного тикета, LLM называет разные `reason` | Один кейс. **Сегодня падает** — характеризующий тест фиксирует разрыв |
| T2 | Один прогон, два вызова tool с разными `reason` | Один кейс, второй вызов `already_exists` |
| T3 | Разные тикеты, одинаковый `reason` | Два кейса |
| T4 | Повтор с другим `priority` | Существующий кейс, приоритет не меняется |
| T5 | 20 потоков/соединений создают кейс одного тикета | Одна строка |
| T6 | Сбой после записи кейса, до результата run; повтор | Один кейс (WP-7) |
| T7 | До WP-6: просроченный run | `failed(worker_lost)`, `SupportAgent.run` не вызван второй раз |
| T8 | Старый worker пишет результат после перехвата | Запись отброшена, строка не изменилась |
| T9 | POST: тот же ключ и тело ×N конкурентно | Один тикет, один run |
| T10 | POST: тот же ключ, другое тело | 409, новых строк нет |

---

## 14. Durable Execution

### 14.1 Жизненный цикл

```text
(нет) ──API──▶ accepted ──worker claim──▶ running ──▶ completed
                                            │  └────▶ failed
                    ◀── reaper (после WP-7) ┘ ──────▶ failed(worker_lost)  (срез 1)
```

`completed` и `failed` терминальны: CAS-условия не допускают переходов из них.

| Переход | Кто | Условие |
|---|---|---|
| ∅ → `accepted` | API | `INSERT tickets` + `INSERT runs` одной транзакцией. Run создаёт только API |
| `accepted` → `running` | worker | Захват строки `WHERE status='accepted' … FOR UPDATE SKIP LOCKED LIMIT 1`; `attempt = attempt+1`; `lease_owner`, `lease_until = now()+ttl`. **Право исполнять** даёт только этот захват |
| `running` → `completed` / `failed` | тот же worker | `UPDATE … WHERE run_id=:r AND attempt=:a AND status='running'`; 0 строк → результат отбрасывается |
| `running` → `failed(worker_lost)` | reaper | `lease_until < now()`; срез 1 |
| `running` → `accepted` | reaper | Только после WP-7: `attempt < max_attempts`, иначе `failed` |

### 14.2 Предотвращение двойного исполнения

1. Захват атомарен (`SKIP LOCKED`): две конкуренции за одну строку невозможны.
2. `attempt` — fencing-токен: даже если старый worker «проснётся» после перехвата, его запись с прежним `attempt` изменит 0 строк.
3. Уникальность `runs(ticket_id)` не даёт создать второй run для тикета.

### 14.3 После рестарта

API не хранит состояния. Worker при старте и периодически запускает reaper. Очередь — таблица `runs`; процессная очередь не источник истины. Допустим in-process сигнал пробуждения как оптимизация задержки, но опрос раз в секунду остаётся основой.

### 14.4 Что можно и нельзя повторять

| Автоматически можно | Автоматически нельзя |
|---|---|
| Чтения БД, захват, reaper | Повтор целого run до WP-6 |
| Вызовы LLM и классификация (расход, не эффект) — **после** WP-6 в рамках повтора run | Эффект без стабильного ключа |
| Fenced-запись результата | Эффект с неизвестным исходом |
| API replay по `Idempotency-Key` | Любые переходы из терминального состояния |

### 14.5 Один worker и его пределы

- Один процесс, один run одновременно; в API `SupportAgent` не вызывается никогда (решает G10).
- `lease_ttl` больше дедлайна прогона с запасом. Начальные значения (допущение, не измерение): дедлайн 120 с, lease 180 с; heartbeat в срезе 1 нет.
- Блокирующий поток нельзя прервать. Дедлайн обеспечивается таймаутами клиента LLM и `MAX_AGENT_STEPS`; если вызов завис, процесс перезапускается супервизором, reaper отмечает run. Дефолты таймаутов пакета `openai` не проверялись (Приложение B).

---

## 15. Data Protection

### 15.1 Правила MVP

- Только синтетические данные. Никаких реальных клиентов, заказов и CRM. Demo Tenant помечен как demo-only; worker отказывается исполнять любой другой tenant (иначе общий `mock_data` стал бы общим для tenants).
- Нет production-ключей в репозитории: ключи генерируются локально и печатаются один раз, в БД только HMAC-хэш; `.env` не версионируется; секрет сервера — из окружения.
- В БД только то, что нужно: `message`, `answer`, коды. Не сохраняются `arguments`/`output` tools, ответы LLM, trace (до SR-4).
- Логи, ответы об ошибках и трассировки не содержат `message`, `answer`, ключей и текста исключений — проверяется тестом.
- БД среза — dev-only (локальный том), с документированным способом очистки.

### 15.2 Решения до реального коммерческого запуска

Не оценивались юридически; это перечень вопросов, не заключение.

| Область | Что решить |
|---|---|
| GDPR | Роли (tenant — контролёр, SupportOps — процессор?), основание обработки, реестр обработки, уведомление об инцидентах |
| Retention | Срок хранения `message`/`answer`, удаление по запросу, бэкапы |
| DPA | Договор с tenant; перечень субпроцессоров (LLM-провайдер, хостинг, БД) |
| Данные у LLM-провайдера | Zero data retention, запрет обучения, регион, мониторинг злоупотреблений, трансграничная передача |
| Изоляция tenant | RLS как второй эшелон, шифрование, процедура экспорта/удаления данных tenant |
| Доступ аудита | Кто из операторов читает данные tenant, break-glass, журнал таких чтений |
| EU AI Act | Роль (provider/deployer), обязанность раскрытия, что ИИ общается с человеком, отнесение к уровню риска — требует юридической оценки |
| Минимизация | Что из `message` необходимо передавать в LLM, псевдонимизация `customer_id` |

Блокирует **запуск**, а не разработку; зафиксировано как D-8.

---

## 16. Roadmap Collision & Identifier

**Факт.** `SUPPORTOPS-API-003` — «Production Security & Deployment», Phase 7, PLANNED. Свободных следующих номеров в префиксе `API-` — `API-004` и далее. Идентификатора `SUPPORTOPS-SEC-003` этот трек не заслуживает: SEC-003 — активная Story про object-level authorization с восемью критериями, и дизайн сервиса в них не входит. Использование её ID исказило бы трассировку коммитов и `active.md`.

| Вариант | Оценка |
|---|---|
| **A. Новая Story `SUPPORTOPS-API-004` «Service API Vertical Slice»** (рекомендуется) | Нет коллизии, префикс из структуры владельца. При активации владелец сужает пересекающиеся части API-001 (endpoints) и API-003 (аутентификация) |
| B. Расширить `API-001` | Story раздувается до девяти пакетов; в ней нет аутентификации и персистентности |
| C. Новый префикс `SUPPORTOPS-SVC-001` | Вводит префикс вне утверждённой структуры |

**Решение владельца (D-1):** Story `SUPPORTOPS-API-004` «Service API Vertical Slice». Структура Mission утверждена владельцем и отличается от первоначального предложения ревизии 2:

| Mission | Содержание | Пакеты этого документа |
|---|---|---|
| M01 API Foundation | FastAPI, конфигурация, `/healthz`, `/readyz`, error contracts, Demo Mode | WP-1 |
| M02 Trusted Identity & Persistence | credentials, tenant, scopes, PostgreSQL, tickets, runs, идемпотентный POST | WP-2, WP-3 |
| M03 Execution Integration | worker, lifecycle, интеграция SupportAgent, результат, отказы | WP-4 |
| M04 Vertical Slice Acceptance | e2e HTTP, регрессия, security validation, demo-документация | WP-5 |

WP-6…WP-9 (стабильная идемпотентность, recovery, trace API, production-интеграции) в Story API-004 не входят и планируются отдельно. WP-6 в любом случае ждёт завершения SEC-003.

Трек затрагивает Phase 5 (персистентность), хотя `API-003` находится в Phase 7; структуру фаз не меняем. Регистрация Story не активирует её: `active.md` и SEC-003 не изменяются.

**Сообщение коммита дизайна:** `docs(supportops): propose service API architecture`.

---

## Приложение A. Что проверено

- Код (HEAD `2e4a149`): `agent.py`, `run_manager.py`, `models.py`, `authorization.py`, `tool_executor.py`, `tools.py`, `tool_definitions.py`, `mock_data.py`, `refund_evaluation.py`, `policy.py`, `classification.py`, `run_agent.py`, `pyproject.toml`.
- Планы: `.monolith/context/planned/API-001/002/003`, `STATE-001/002`, `SEC-004`, `roadmap-index.md`.
- Совместимость (PyPI, read-only, без установки): `fastapi 0.163.1` требует Python ≥3.11, `pydantic>=2.9.0`, `anyio>=4.1.0`, `starlette>=0.46.0`; в `uv.lock` — `pydantic 2.14.1`, `anyio 4.15.1`, проект `>=3.11`: конфликтов нет. `uvicorn 0.54.0` (Python ≥3.10) и `psycopg 3.3.9` (Python ≥3.10) совместимы. Версии на 2026-11-15, финальный pin — при установке и по `uv lock`.
- Идемпотентность: `tools.py:148-205`, `tool_definitions.py` (`CreateSupportCaseArguments`, регистрация), `agent.py` (`run() -> str`, `last_run`), `tool_executor.py:44` (`SAFE_ERROR_CODES`), тесты `already_exists`.
- MONOLITH: `docs/adr/ADR-017-durable-execution-state.md` (§1, §2, §2.3, §2.3a), `src/durable/types.ts`. Остальные ADR по Durable State (ADR-018/019, DURABLE-SEC-*) **не читались**: вывод §6 основан на ADR-017 и структуре `src/durable/`.

## Приложение B. Что не проверено

- Поведение пакета `openai` по таймаутам и ретраям (в коде они не заданы; дефолты SDK не сверялись с документацией).
- Контракт metadata-only trace (SR-4, ныне в `main`) в ревизии 2 не читался; перед WP-6 и WP-8 его нужно изучить по актуальному коду.
- Нагрузочные характеристики, стоимость, поведение `SKIP LOCKED` на выбранной версии PostgreSQL: проектные допущения, не измерения.
- Правовая сторона D-8 и §15.2 не оценивалась.
- Не проверено, что тестов на разные `reason` для одного тикета нет вообще: найдены только тесты `already_exists` (поиск по `already_exists`).
- Установка и запуск FastAPI/psycopg не выполнялись; совместимость — только по метаданным PyPI.
- Допущения по `lease`/дедлайну (120/180 с) не измерялись.
