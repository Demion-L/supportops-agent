# SUPPORTOPS-API-004 / M02-WP2 — Integration Handoff и Retention Boundary

> **Статус:** реализованы слои 1–2 (identifiers, `TicketRepository` PG + fake, тесты). HTTP-слой (слой 3) **не реализован**. Merge не выполнялся.
> **Основание:** `docs/design/api-004-m02-wp2-ticket-repository.md`, `api-004-interface-alignment-and-integration-gate.md`.
> **Дата:** 2026-11-16. **Автор:** Beta.

## 1. Что реализовано

| Файл | Назначение |
|---|---|
| `src/supportops_service/ids.py` | `new_ticket_id()`, `new_run_id()` (`secrets.token_hex(16)`), `is_ticket_id()`/`is_run_id()` (`fullmatch`) |
| `src/supportops_service/repositories/protocols.py` | `TicketRepository` (Protocol), `CreateResult`, `TicketView` |
| `src/supportops_service/repositories/_inputs.py` | валидация аргументов + `compute_request_hash` — **общие** для PG и fake |
| `src/supportops_service/repositories/tickets.py` | `PgTicketRepository(pool)` |
| `src/supportops_service/repositories/fakes.py` | `InMemoryTicketRepository` |
| `src/supportops_service/db/errors.py` (+) | `IdempotencyConflict`, `InvalidRepositoryInput`, `StorageFailure` |

Миграции **не добавлялись**: схема `0001` достаточна (UNIQUE `(tenant_id, idempotency_key)`, составной FK, `UNIQUE(ticket_id)` в `runs`, CHECK формата id).

## 2. Контракт для Alpha / HTTP-слоя

- `create_ticket(*, tenant_id, idempotency_key, customer_id, message, order_id) -> CreateResult(ticket_id, run_id, status, created_at, replayed)`;
- `get_ticket(*, tenant_id, ticket_id) -> TicketView | None`; `None` одинаков для чужого, несуществующего и некорректного id (для некорректного БД не затрагивается);
- `tenant_id` — keyword-only, без default, `str` (не `TenantContext`);
- ошибки (фиксированный текст, без значений):

| Исключение | Смысл | HTTP (предложение) |
|---|---|---|
| `IdempotencyConflict` | тот же ключ, другой запрос | 409 `idempotency_key_reuse` |
| `StorageUnavailable` | БД недоступна / таймаут / `lock_timeout` / `statement_timeout` | 503 `service_unavailable` |
| `StorageFailure` | иной сбой БД; ничего не создано | 500 `internal_error` (generic) |
| `InvalidRepositoryInput` | нарушен контракт аргументов (дефект вызывающего: HTTP-слой обязан валидировать раньше) | 500 `internal_error`, если дошло |

- Границы валидации репозитория (повторяют HTTP-контракт, защита второго эшелона): `customer_id` 1–100, `message` 1–4000, `order_id` 1–100|null, без NUL и lone surrogate; ключ `[A-Za-z0-9._:-]{8,100}`; tenant `[a-z0-9][a-z0-9-]{0,62}`.
- Репозиторий **не** читает `api_credentials`, не импортирует `supportops_agent`/LLM/сеть (статические тесты + проверка в чистом интерпретаторе).

## 3. Что нужно от интеграции (Integration Gate)

1. `errors.py`: маппинг `IdempotencyConflict`→409, `StorageUnavailable`→503 (общий обработчик), коды `idempotency_key_required`, `unsupported_media_type`; решение D2 (владелец кодов 401/403).
2. `create_app`: lifespan `DatabasePool.open()/close()`, `tickets` в `app.state` (§3.4 alignment doc).
3. Роутеры `POST/GET /v1/tickets` поверх `SecuredRoute` + `require_scope` Alpha; tenant — только `ctx.tenant_id`.
4. `interfaces.py` изменений **не требует**: `TicketRepository` определён в `repositories/protocols.py` (Beta-owned). Если Alpha хочет Protocol в `interfaces.py` — согласованный патч отдельно.
5. G8: `SUPPORTOPS_REQUIRE_PG=1 pytest tests/db tests/repositories` на интегрированной ветке.

## 4. Retention Boundary (не реализовано; документируется)

**Не реализовано:** удаление, обезличивание, таблица tombstone, роль `retention`.

### 4.1 Персональные / потенциально персональные данные

| Поле | Таблица | Класс |
|---|---|---|
| `message` | `tickets` | **персональные** (свободный текст клиента) |
| `customer_id` | `tickets` | **персональные** (идентификатор субъекта у tenant) |
| `order_id` | `tickets` | потенциально персональные (связываемы с субъектом) |
| `answer` | `runs` | **персональные** (может цитировать данные клиента) |
| `idempotency_key` | `tickets` | клиентский токен; персональным не считается, но **может** содержать идентификатор, выбранный клиентом (не контролируется) |
| `request_hash` | `tickets` | SHA-256 от `(customer_id, message, order_id)`: **не** считается безопасным — для коротких/предсказуемых значений допускает перебор (dictionary attack) ⇒ при обезличивании не хранить |
| `ticket_id`, `run_id`, `tenant_id`, статусы, времена, `failure_code` | оба | непersональные идентификаторы/метаданные |

### 4.2 Судьба idempotency key после удаления тикета

При retention = DELETE (`runs` → `tickets`, ADR M02 §3.2 п.6) ключ исчезает вместе со строкой: повтор старого `Idempotency-Key` создаст **новый** ticket+run (повторное исполнение / платный вызов LLM). Окно идемпотентности ограничено сроком хранения. Репозиторий WP2 это поведение не маскирует и не меняет (replay определяется только по существующей строке).

### 4.3 Минимальный tombstone (если решение владельца — сохранять окно идемпотентности)

Допустимое содержимое: `tenant_id`, `idempotency_key` **или его keyed-хэш**, `ticket_id`, `run_id`, `deleted_at`, `expires_at`. Недопустимо: `message`, `customer_id`, `order_id`, `answer`, `request_hash` (перебираем).
Почему без `request_hash`: tombstone должен отвечать только «ключ уже использован»; различение replay/conflict требует хэша, поэтому либо (а) отвечать единым `409`/`410`-подобным кодом для любого повтора после удаления, либо (б) хранить **HMAC** хэша с секретом вне БД (не восстанавливаемо из дампа БД). Выбор — решение владельца.
Если ключ клиентский и может нести идентификатор — в tombstone хранить `HMAC(secret, tenant_id || key)`, а не сам ключ.

### 4.4 Ограничения для будущей миграции

- `tickets_no_update` блокирует обезличивание UPDATE-ом; `runs_guard` блокирует любые UPDATE терминальных run; `CHECK completed ⇒ answer NOT NULL` не допускает `answer = NULL` (ADR M02 §3.2: варианты (a) полное удаление, (b) вынос PII в `ticket_content`).
- Порядок DELETE: `runs` → `tickets` (составной FK). У `api`/`worker` нет права DELETE; нужна отдельная роль.
- Если `message` и др. переедут в `ticket_content`, `PgTicketRepository` потребует миграции запросов INSERT (одна транзакция сохраняется, 3 INSERT вместо 2) и обновления контрактных тестов; `TicketView` не меняется.
- Окно между конфликтом и lookup (строка удалена retention-задачей): репозиторий отвечает `StorageUnavailable` (fail-closed, клиент повторяет; покрыто тестом).
- Бэкапы/репликация вне БД-схемы: срок хранения в бэкапах входит в retention-политику.

## 5. Не покрыто WP2

HTTP-роутеры; `RunQueue`/worker; роли БД и гранты (WP4); `/readyz` с БД; RLS; нагрузочные и долгие тесты; поведение при failover/реплике; утечка host:port из логгера `psycopg_pool` (см. отчёт).


## 6. Санитизация ошибок хранилища (фикс `db/pool.py`)

Дефект: `raise StorageUnavailable() from None` обнуляет `__cause__`, но **не** `__context__` — исходное исключение драйвера (`psycopg.OperationalError`, текст которого может содержать host/DSN) оставалось достижимым через `error.__context__`. В ветке «потеря соединения внутри тела» цепочку дополнительно ставит сам `contextlib.contextmanager` (он вызывает генератор из собственного обработчика).

Исправление (минимальное, типы и семантика fail-closed не менялись): ошибка драйвера ловится, выставляется флаг; `StorageUnavailable()` поднимается **вне** блока `except`; в ветке тела контекст дополнительно обрывается явно (`__context__ = None`, `__suppress_context__ = True`). Инвариант: у публичных ошибок хранилища `__cause__ is None` и `__context__ is None`, текст фиксирован. Новой иерархии нет; непредвиденные (не availability) ошибки по-прежнему пробрасываются как есть (их интерпретирует репозиторий → `StorageFailure`).

Регрессия: `tests/db/test_pool_sanitization.py` (синтетический маркер; без БД) + ужесточённые проверки в `test_pool_pg.py`/`test_tickets_pg.py`. Проверено: на старом `pool.py` 5 из 8 новых тестов падают.

## 7. Контракт маппинга ошибок → HTTP (для Integration Gate; не реализован)

Отдельного обработчика `StorageUnavailable`/`IdempotencyConflict` в `errors.py` **ещё нет**: сейчас любое такое исключение дало бы 500 через `_unhandled_handler`. Точный контракт (подтверждён владельцем):

| Источник | HTTP | `error.code` | Кто формирует |
|---|---|---|---|
| Неверный/отсутствующий credential | 401 | `unauthenticated` (+ `WWW-Authenticate: Bearer`) | Alpha `SecurityError` (единственный механизм) |
| Недостаточный scope | 403 | `insufficient_scope` | Alpha `SecurityError` |
| `IdempotencyConflict` | 409 | `idempotency_key_reuse` | общий обработчик `errors.py` (Beta) |
| `StorageUnavailable` | 503 | `service_unavailable` | общий обработчик `errors.py` (Beta); патч 0002 Alpha добавляет только запись `_FIXED[503]` |
| `StorageFailure` | 500 | `internal_error` | `_unhandled_handler` (без изменений) |
| `InvalidRepositoryInput` (неожиданный) | 500 | `internal_error` | `_unhandled_handler`; на уровне роутера недопустим — валидация до репозитория |

Правила: тело ответа — только `error_body(code, fixed_message, request_id)`; `str(exc)`, `repr(exc)`, `__context__` в ответ и в лог не попадают (логируется только имя класса); 503 допускает `Retry-After` (решение владельца); `IdempotencyConflict` не содержит данных первого запроса. Нужны ещё коды `idempotency_key_required` (400) и `unsupported_media_type` (415) — HTTP-слой, не WP2.

## 8. Готовность Interface Alignment (Beta)

Проверка актуальности патчей Alpha (`docs/security/patches/` в worktree Alpha, `95cb791`), только чтение: `git apply --check` — 0001 и 0002 применимы к текущему worktree Beta (в т.ч. с этим коммитом); 0003 применим к worktree Alpha. Патчи не применялись.

Порядок интеграции: (1) коммиты Beta WP1/WP2 уже в основе; (2) 0001 `interfaces.py` (Beta); (3) 0002 `errors.py`/`config.py`/`app.py` + `tests/service/test_security_wiring.py` (Beta); (4) 0003 (Alpha); (5) затем Beta-добавки: обработчики 409/503, коды 400/415, lifespan пула, роутеры `/v1/tickets`.

Файлы, затрагиваемые патчами: `interfaces.py`, `app.py`, `config.py`, `errors.py`, `tests/service/test_security_wiring.py` (0001–0002); `repositories/credentials.py`, `security/{contracts,credentials,dependencies,tenant_context}.py`, 5 тестов `tests/service/security/**`, `tests/security_pg/**` (0003/Alpha).

Расхождения/риски:
- `repositories/__init__.py` существует у обеих сторон с разной docstring (add/add) — тривиальный конфликт; взять любую, оставить пакет без I/O.
- Патчи не задают обработчик `StorageUnavailable` (только `_FIXED[503]`), нет `IdempotencyConflict` → 409: это Beta-добавка (§7).
- `tenant_id` в репозиториях Beta — keyword-only `str` (согласовано, расхождений нет).
- Решение 8 «`SecuredRoute` на `/v1`» и пункт `redirect_slashes=False` — в 0002; в Beta WP2 роутеров `/v1` нет.
