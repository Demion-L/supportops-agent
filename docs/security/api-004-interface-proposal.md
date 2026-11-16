# SUPPORTOPS-API-004 — Interface Proposal & Integration Contract (Alpha → Beta)

> **Статус:** предложение, редакция 2 (2026-11-16). Владелец утвердил направления (канонический `TenantContext` в `interfaces.py`, набор интерфейсов ниже); патч **не применён**. `src/supportops_service/interfaces.py` (владелец — Beta) **не изменялся**.
> **База:** `b33191b`. Сверено с `interfaces.py`, `app.py`, `config.py`, `errors.py`, `middleware.py` (`b33191b`) и с PostgreSQL Foundation (`docs/design/api-004-m02-persistence-foundation.md`, `db/migrations/0001_initial.sql` в рабочем дереве `main`, не закоммичено).

## 1. Interface patch для `interfaces.py` (выполняет Beta)

### 1.1 Заменить

| Определение в `b33191b` | Заменить на |
|---|---|
| `TenantContext.scopes: frozenset[str]` | `scopes: frozenset[Scope]` (поля `tenant_id`, `credential_id` без изменений; `frozen=True, slots=True` сохранить) |
| `CredentialRecord` (`key_hash: str`, `revoked_at: str \| None`, нет `pepper_id`/`expires_at`) | версия из §1.2 |
| `CredentialRepository` (только `get_by_prefix`) | + `touch_last_used` |
| `Authenticate`, `RequireScope` (заготовки) | `RequireScope = Callable[[Scope], Callable[..., TenantContext]]`; `Authenticate` по смыслу без изменений. Реализации — у Alpha (`security/dependencies.py`) |
| Шапка «PRELIMINARY … NOT agreed» | «agreed, API-004» после подтверждения |

### 1.2 Добавить (дословно из `security/contracts.py`)

```python
class Scope(StrEnum):
    TICKETS_WRITE = "tickets:write"
    TICKETS_READ = "tickets:read"
    TRACE_READ = "trace:read"

@dataclass(frozen=True, slots=True)
class CredentialRecord:
    credential_id: str
    tenant_id: str
    key_prefix: str
    key_hash: bytes = field(repr=False)      # HMAC-SHA256, 32 байта
    pepper_id: str
    scopes: frozenset[str]                   # сырые строки: фильтр по Scope — в Alpha
    expires_at: datetime | None = None       # tz-aware
    revoked_at: datetime | None = None       # tz-aware

class CredentialRepository(Protocol):
    def get_by_prefix(self, key_prefix: str) -> CredentialRecord | None: ...
    def touch_last_used(self, credential_id: str) -> None: ...   # best-effort

class Clock(Protocol):
    def now(self) -> datetime: ...           # tz-aware UTC
```

`TenantContext` — единственное определение, неизменяемое, создаётся **только** фабрикой `security/tenant_context.build_tenant_context` после успешной аутентификации (статические тесты: одно определение, один конструирующий модуль).

`authenticate` и `require_scope` остаются в `security/dependencies.py` (Alpha); в `interfaces.py` — только сигнатуры.

### 1.3 Импорты Alpha, которые изменятся после применения патча (6 мест)

| Файл | Сейчас | Станет |
|---|---|---|
| `src/supportops_service/security/tenant_context.py` | `…security.contracts import Scope` | `…interfaces import Scope, TenantContext` |
| `src/supportops_service/security/credentials.py` | `…security.contracts import Clock, CredentialRepository` | `…interfaces import Clock, CredentialRepository` |
| `src/supportops_service/security/dependencies.py` | `…security.contracts import Scope` | `…interfaces import Scope, TenantContext` |
| `tests/service/security/conftest.py` | `…security.contracts import CredentialRecord, Scope` | `…interfaces import CredentialRecord, Scope` |
| `tests/service/security/test_security_scopes_tenant.py` | `…security.contracts import Scope` | `…interfaces import Scope` |
| `tests/service/security/test_security_route_audit.py` | `…security.contracts import Scope` | `…interfaces import Scope` |

Затем удалить `security/contracts.py`. Порядок: (1) Beta применяет патч, `contracts.py` остаётся; (2) Alpha одним коммитом меняет 6 импортов и удаляет `contracts.py`; (3) полный регресс. До шага 2 существуют два `CredentialRecord` (разные поля), но Alpha не опирается на `interfaces.CredentialRecord`.

### 1.4 Затронутые тесты

- Alpha: `tests/service/security/**` (6 импортов); в `test_security_static_boundaries.py` проверка «единственное определение `TenantContext`» остаётся верной; добавить проверку «`contracts.py` отсутствует».
- Beta: `tests/service/**` определения интерфейсов не импортируют (проверено grep) — патч их не затрагивает; тесты `interfaces.py`, если появятся, обновить под `frozenset[Scope]`/`bytes`/`datetime`.
- `frozenset[str]` → `frozenset[Scope]`: `Scope` — `StrEnum`, сравнение со строками и сериализация сохраняются.

### 1.5 Что Beta выполняет самостоятельно (Alpha этих файлов не трогает)

1. Применить §1.1–1.2 к `interfaces.py`.
2. Подключение в `create_app` (§2.2) и поля `pepper`/`pepper_id` в `ServiceSettings`.
3. Решение по расхождению механизма ошибок (§2.5, вопрос владельцу).
4. PG-схема уже совместима (`key_hash bytea(32)`, `pepper_id`, `scopes text[]`, `expires_at`, `revoked_at`, `last_used_at`).
5. `FastAPI(redirect_slashes=False)` (§3, LOW-1).
6. Подключать роутеры `/v1` только с `route_class=SecuredRoute` и вызывать `find_unsecured_routes(app)` в тесте `create_app` (§2.3).

## 2. Integration Contract

### 2.1 Кто создаёт `TenantContext`, где он живёт, как не утекает

1. Создаёт **только** `CredentialVerifier.authenticate` через `build_tenant_context` (Alpha). Вход — единственный заголовок `Authorization`; другие заголовки, query, cookie, path и тело не читаются (статический тест).
2. Хранение: ключ в **`request.scope`** конкретного запроса (`supportops.verified_tenant_context`), пишется серверным кодом `SecuredRoute`. Не `scope["state"]` (может делиться между запросами), не `ContextVar`, не глобальное состояние, не `app.state`.
3. Время жизни = время жизни ASGI-запроса; между запросами ничего не сохраняется (кэш аутентификации в MVP запрещён, статический тест на `lru_cache`/`cachetools`). Клиент не может подставить значение (тесты `test_client_cannot_inject_a_verified_context`, `test_shared_scope_state_cannot_authenticate_later_requests`).
4. Handlers получают контекст только как параметр `Depends(require_scope(...))`; репозитории tickets/runs принимают `tenant_id: str` (keyword-only, без default), не `TenantContext`.

### 2.2 Подключение (Beta в `create_app`)

```python
verifier = CredentialVerifier(credential_repository, PepperSet({pepper_id: pepper_bytes}), clock)
install_security(app, verifier)   # app.state.credential_verifier + обработчик SecurityError
v1 = APIRouter(prefix="/v1", route_class=SecuredRoute)
```

- PG `CredentialRepository` — реализация Alpha (`repositories/credentials.py`) поверх пула Beta; sync psycopg совместим: `SecuredRoute` вызывает `verifier.authenticate` через `run_in_threadpool`. Конвертации: `credential_id uuid → str`, `scopes text[] → list` (принимается), `key_hash bytea → bytes`.
- Pepper: `SecretStr` из окружения, **без значений по умолчанию**; старт без pepper при подключённой аутентификации — отказ. Alpha окружение не читает.
- Если `install_security` не вызван, защищённый маршрут отвечает **503**, не пропускает (`NOT_CONFIGURED`, тест `test_unconfigured_security_denies`).

### 2.3 SecuredRoute — обязательность и политики

- Все защищённые маршруты `/v1` — только на `route_class=SecuredRoute`. Он всегда аутентифицирует (401) и проверяет scopes, объявленные **на маршруте** (`Depends(require_scope(X))` в параметрах или `dependencies=[...]` декоратора), **до** чтения тела. Разные маршруты — разные scopes.
- Регистрация без `SecuredRoute` обнаруживается `security/route_audit.find_unsecured_routes(app, prefix="/v1", public_paths=())` (включая `include_router` с префиксом, вложенные роутеры, WebSocket, `Mount`); тест `test_real_application_has_no_unsecured_v1_route` падает при появлении такого маршрута в `create_app`.
- Публичные маршруты под `/v1` — только через явный `public_paths` (точные пути, без wildcard), с ревью.
- **Ограничение (FastAPI 0.163.1):** scopes, заданные через `include_router(..., dependencies=[...])` / `APIRouter(dependencies=[...])`, не видны `SecuredRoute` при построении обработчика. Защита **сохраняется** (403 при валидном теле), но битое тело у аутентифицированного без scope даёт 422 вместо 403. Правило: объявлять scopes на маршруте. Зафиксировано тестом `test_router_level_scope_dependencies_still_enforce_scope_after_body_parsing`.

### 2.4 Единый 401, 503, worker

- **401:** все причины (формат, неизвестный префикс, неверный секрет, отозван, истёк, некорректная запись) — один байт-идентичный ответ `unauthenticated` + `WWW-Authenticate: Bearer`; причина только в серверном логе.
- **503:** любое исключение `get_by_prefix` (в т.ч. недоступный PostgreSQL, `StorageUnavailable`) → `503 service_unavailable`; текст исключения отбрасывается и не цепляется как `__context__`. Недоступность не превращается ни в 401, ни в allow.
- **Worker:** `CredentialRepository`/`CredentialRecord` разрешены только в `security/`, `repositories/`, `interfaces.py` (статический тест); роль БД `supportops_worker` не имеет доступа к `api_credentials` (Beta M02 §3); worker не использует HTTP-credential.

### 2.5 Расхождение механизма ошибок (требует решения владельца)

Beta M02 §10.1 (#7, #9, #13) предлагает: сменить коды `errors.py` на канонические, добавить `ApiError(headers=…)` и ставить `authenticate` первой зависимостью. Alpha реализовал собственные `SecurityError` и обработчик (`security/errors.py`) поверх `errors.error_body`; работает и покрыт тестами. Два механизма на один 401 дадут риск расхождения тела/заголовков. **Рекомендация:** Alpha-механизм — единственный для 401/403/503 аутентификации; Beta лишь выравнивает `_FIXED` (`401 → unauthenticated`, `403 → insufficient_scope`) на случай стороннего `HTTPException`, без `ApiError`.

## 3. Интеграционные риски (для Beta)

- **LOW-1:** `redirect_slashes=True` (по умолчанию) отвечает 307 на `/v1/x/` без аутентификации — раскрывает существование маршрута. Рекомендуется `FastAPI(redirect_slashes=False)`.
- **LOW-2:** `touch_last_used` на каждый успешный запрос — запись в БД на горячем пути. Best-effort, на решение не влияет; при нагрузке — троттлинг в PG-репозитории.
- Срок жизни ключей (по умолчанию 90 дней, конфигурируемый, без автопродления) — это выдача ключей; в этом пакете не реализуется. `expires_at`/`revoked_at` проверяются на каждом запросе.
- Отключение tenant не реализовано: у `CredentialRecord` нет статуса tenant, таблицы `tenants` нет. Обязательно **до второго tenant** (до тех пор — отзыв всех credential tenant).
