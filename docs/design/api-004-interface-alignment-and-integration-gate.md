# SUPPORTOPS-API-004 — Interface Alignment (Alpha ↔ Beta) и Integration Gate

> **Статус:** план. **Ни один публичный интерфейс не изменён**, merge не выполнялся. Все изменения ниже выполняются только после финального согласования с Alpha и владельцем.
> **Источники (читались без изменений):** `supportops-agent-api004-alpha/docs/security/api-004-interface-proposal.md` и `security-contract.md`; код Alpha `95cb791` (`security/{contracts,credentials,dependencies,errors,route_audit,tenant_context}.py`); Beta `e6bf9b1`.
> **Дата:** 2026-11-16.

## 1. Состояние

| | Beta | Alpha |
|---|---|---|
| Коммит | `e6bf9b1` (на `main`, `c369cb9` + PostgreSQL Foundation) | `95cb791` в worktree `supportops-agent-api004-alpha` (на `b33191b`) |
| Файлы | `pyproject.toml`, `uv.lock`, `db/**`, `tests/db/**` | `security/**`, `tests/service/security/**`, документы |
| Пересечение | — | **Нет общих изменённых файлов.** `git merge-tree` (только чтение, worktree и refs не тронуты): дерево собирается без конфликтов (`37d79b6…`) |

Proposal Alpha доступен — блокирующей зависимости нет. Оговорка: merge-tree проверяет текстовые конфликты, но **не** семантические (см. §5, коллизия `CredentialRecord`); семантика проверяется только тестами после настоящей интеграции, которая здесь **не выполнялась**.

## 2. Решения владельца (зафиксированы)

1. `TenantContext` — единственное определение, в `interfaces.py`.
2. `CredentialRecord`: `key_hash: bytes`, `pepper_id`, `expires_at`, `revoked_at`.
3. Канонические коды: `unauthenticated`, `insufficient_scope`, `service_unavailable`.
4. `SecuredRoute` обязателен для защищённых `/v1` endpoints.
5. `create_app` принимает `CredentialRepository`, `Clock`, конфигурацию pepper.
6. Общий интерфейс сейчас не менять — до окончательного согласования с Alpha.

## 3. Точный список изменений (не применён)

### 3.1 `interfaces.py` (владелец — Beta; подтверждает Alpha)

| # | Изменение |
|---|---|
| 1 | Добавить `Scope(StrEnum)`: `tickets:write`, `tickets:read`, `trace:read` — дословно из `security/contracts.py` |
| 2 | `TenantContext`: оставить **единственное** определение здесь; `scopes: frozenset[Scope]` (Alpha совместима с `frozenset[str]`, т.к. `Scope` — `str`; предпочтительно `Scope`). `tenant_context.py` Alpha — только фабрика |
| 3 | `CredentialRecord`: `credential_id: str`, `tenant_id: str`, `key_prefix: str`, `key_hash: bytes` (`repr=False`), `pepper_id: str`, `scopes: frozenset[str]` (сырые), `expires_at: datetime \| None`, `revoked_at: datetime \| None`. **Поля — дословно как в `security/contracts.py`** |
| 4 | `CredentialRepository`: `get_by_prefix(key_prefix) -> CredentialRecord \| None` + `touch_last_used(credential_id) -> None` (best-effort) |
| 5 | `Clock` Protocol (`now() -> datetime`, tz-aware UTC); `SystemClock` остаётся у Alpha (`credentials.py`) |
| 6 | `Authenticate` / `RequireScope`: оставить; `RequireScope = Callable[[Scope], Authenticate]` |
| 7 | Удалить статус PRELIMINARY из docstring; перенос из `contracts.py` — дословный, затем `contracts.py` → re-export → удаление (Alpha) |

PG-типы: `api_credentials.key_hash bytea` → `bytes` (psycopg отдаёт `bytes`), `expires_at`/`revoked_at timestamptz` → tz-aware `datetime`, `scopes text[]` → `list[str]` → `frozenset[str]` в репозитории. Схема менять не требуется — **совместима без миграции**.

### 3.2 `errors.py` (публичное поведение — только с согласия)

| # | Изменение | Примечание |
|---|---|---|
| 1 | Коды 401/403: `unauthorized`→`unauthenticated`, `forbidden`→`insufficient_scope` в `_FIXED` | Изменение публичного кода ответа; в WP1 нет защищённых роутеров, обратной совместимости клиентов нет, но тесты WP1 на старые коды нужно обновить |
| 2 | Добавить 503 `service_unavailable` (fixed text) в `_FIXED` | Alpha использует свои исключения; Beta-обработчик нужен для `StorageUnavailable` вне security |
| 3 | Добавить 409 `idempotency_key_reuse`, 400 `idempotency_key_required`, 415 `unsupported_media_type` | WP2 |
| 4 | Поддержка `headers` в `_http_exception_handler` (`WWW-Authenticate: Bearer`) **или** оставить Alpha-обработчик `SecurityError` | Рекомендация: оставить у Alpha (уже реализован), Beta не дублирует. Решение владельца |
| 5 | Обработчик `StorageUnavailable` → 503 `service_unavailable` (общий, не только auth) | WP2 |
| 6 | Зафиксировать `error_body` и `get_request_id` как стабильный публичный API | От них зависит `security/errors.py` |

Коллизия: Alpha-обработчик `SecurityError` и Beta-`_FIXED[401/403]` дают **два источника** кодов 401/403. После п.1 они совпадают по коду; тест на эквивалентность тела обязателен.

### 3.3 `ServiceSettings` / `config.py`

| # | Изменение |
|---|---|
| 1 | `pepper: SecretStr \| None`, `pepper_id: str \| None` (`SUPPORTOPS_PEPPER`, `SUPPORTOPS_PEPPER_ID`); без значений по умолчанию, не в `repr`, значения не эхо-ятся |
| 2 | `database_url` — **не** в `ServiceSettings`: уже `DatabaseSettings.from_env()` (`SUPPORTOPS_DATABASE_URL`); `create_app` получает готовый `DatabasePool`/репозитории, а не DSN |
| 3 | Проверка: если подключена аутентификация, а pepper не задан → `ServiceConfigurationError` при `create_app` (fail-closed) |
| 4 | Формат `PepperSet` (несколько pepper по `pepper_id`, ротация) — определяет Alpha; конфигурация одного pepper в WP2, множественные — отдельное решение |

### 3.4 `create_app` и dependency injection

Целевая сигнатура (предложение, **не применять до согласования**):

```python
def create_app(
    settings: ServiceSettings | None = None, *,
    credentials: CredentialRepository | None = None,
    clock: Clock | None = None,
    tickets: TicketRepository | None = None,      # WP2
    pool: DatabasePool | None = None,             # lifespan: open/close, WP2
    extra_routers: Sequence[APIRouter] = (),
) -> FastAPI
```

- Все новые параметры **keyword-only и опциональны** → существующие вызовы `create_app()`/`create_app(settings)` не ломаются (WP1-тесты остаются).
- Если `credentials is None` → защищённые роутеры **не подключаются**; приложение отдаёт только `/healthz`, `/readyz`. Если защищённые роутеры подключены, а `credentials`/pepper отсутствуют → ошибка конфигурации при старте (никогда не «открытый» режим).
- Вызов Alpha: `install_security(app, CredentialVerifier(credentials, PepperSet(...), clock or SystemClock()))` — только пишет `app.state.credential_verifier` и регистрирует обработчик.
- `DatabasePool`: `open()` в lifespan (не при `create_app`), `close()` при остановке; `create_app` и импорт соединений не создают (тест WP1 `test_no_side_effects` расширяется).
- Репозитории хранятся в `app.state`; зависимости читают `request.app.state` (как у Alpha).
- Порядок middleware: `RequestContextMiddleware` внешний (как сейчас).

### 3.5 `SecuredRoute`

Все роутеры `/v1`: `APIRouter(prefix="/v1", route_class=SecuredRoute)`; зависимость `Depends(require_scope(Scope.…))` на каждой операции. Проверка — `route_audit.find_unsecured_routes(app)` в тесте: ни одного `/v1` маршрута без `SecuredRoute` и scope. Без `SecuredRoute` битое тело без ключа получит 422 вместо 401 (нарушение порядка проверок) — поэтому тест обязателен.

### 3.6 `CredentialRepository` (PG, владелец — Alpha)

- Файл `repositories/credentials.py` (Alpha); использует `DatabasePool` Beta (`pool.connection()`), `row_factory=dict_row` уже настроен.
- `get_by_prefix`: `SELECT … FROM api_credentials WHERE key_prefix = %s`; `touch_last_used`: `UPDATE api_credentials SET last_used_at = %s WHERE credential_id = %s`, ошибки глотаются (best-effort).
- `StorageUnavailable` из пула → Alpha превращает в `AuthenticationUnavailable` → 503 (fail-closed: недоступность БД — никогда не 401 и не успех).
- В `api_credentials` нет метода вставки: seed/выдача ключей — отдельное решение (вопрос 5 M02).
- Права БД: `supportops_api` — `SELECT`, `UPDATE (last_used_at)`; `supportops_worker` — **нет доступа** (WP4).

## 4. Открытые решения для владельца

| # | Решение |
|---|---|
| D1 | Подтвердить §3.1–3.5 как единое изменение публичных интерфейсов |
| D2 | 401/403: Alpha-обработчик единственный источник, или Beta `_FIXED` тоже переименовать (§3.2 п.1, 4) |
| D3 | `scopes: frozenset[Scope]` или `frozenset[str]` в `TenantContext` |
| D4 | 55P03/57014 в API: 503 или иное |
| D5 | Владелец seed Demo Tenant и метода вставки credential |

## 5. Известные риски интеграции

1. **Коллизия `CredentialRecord`:** в `interfaces.py` (старые поля) и `security/contracts.py` (новые). При слиянии без §3.1 имена совпадают, типы различаются — тесты Alpha не ломаются (они не опираются на `interfaces.CredentialRecord`), но Beta-код, импортирующий старую версию, получит неверный тип. Решение: §3.1 п.3 до объединения.
2. Два обработчика 401/403 (см. §3.2).
3. `uv.lock`/`pyproject.toml` изменяла только Beta; Alpha зависимостей не добавляла — конфликтов нет, но после merge нужно `uv lock --check`.
4. Тесты Alpha (`tests/service/security/**`) работают на in-memory репозитории; PG-реализация `CredentialRepository` отсутствует → интеграционная проверка auth+PG возможна только после реализации Alpha.

## 6. Integration Gate (минимальная процедура; **merge выполняет владелец/по отдельному согласованию**)

| Шаг | Действие | Критерий прохождения |
|---|---|---|
| G1 | Зафиксировать SHA обеих сторон: Beta `e6bf9b1` (+docs-коммит), Alpha `95cb791` (+следующие); оба worktree чистые (`git status`) | SHA совпадают с согласованными; нет незакоммиченного |
| G2 | Согласовать `interfaces.py` (§3.1) и `errors.py` (§3.2): отдельный коммит владельца интерфейса, ревью другой стороной | Подтверждение Alpha и владельца письменно (D1–D3) |
| G3 | Сухая проверка: `git merge-tree --write-tree <beta> <alpha>` | Нет конфликтов; список файлов ожидаемый |
| G4 | Интеграционная ветка от `main`; `git merge --no-ff` Alpha (отдельное разрешение владельца) | Конфликтов нет; `git diff --stat` только ожидаемые файлы |
| G5 | `uv sync --frozen`; `uv lock --check` | Без изменений lock |
| G6 | `git diff main..HEAD -- src/supportops_agent` | **Пусто** |
| G7 | Полный `pytest` (все тесты SEC-003 + service + security + db) | 0 failed; число тестов ≥ сумма сторон |
| G8 | PostgreSQL 16 в отдельном контейнере: `SUPPORTOPS_REQUIRE_PG=1 SUPPORTOPS_TEST_DATABASE_URL=… pytest tests/db` (и PG-тесты репозиториев) | 0 skipped, 0 failed |
| G9 | Security regression: `tests/service/security/**`, `route_audit` (нет незащищённых `/v1`), тест утечек (маркеры секретов в ответах/логах), статические границы (нет импорта `supportops_agent` из service, API не импортирует `runs_queue`) | Все зелёные |
| G10 | Импорт и запуск: `python -c "import supportops_service.app"` без соединений/потоков; `create_app()` с in-memory репозиториями; `uvicorn` старт + `GET /healthz`, `/readyz`; `/v1` без ключа → 401 с `WWW-Authenticate` | Успех; кодов 5xx нет |
| G11 | Проверка отсутствия `state.json`, `.env`, `__pycache__` в индексе; `git diff --check` | Чисто |
| G12 | Любое изменение существующего публичного API после G4 → остановка и отдельное согласование владельца | — |

Общий регресс Alpha+Beta считается пройденным **только после** G7–G10 на интегрированной ветке. До этого заявлять нельзя.

## 7. Что сейчас не делается

Интеграционный merge, push, реализация `TicketRepository`/worker/`CredentialRepository`, изменения `interfaces.py`/`errors.py`/`app.py`/`config.py`, миграция по retention (ADR M02 §3.2), создание ролей БД, операции Coordinatorium.
