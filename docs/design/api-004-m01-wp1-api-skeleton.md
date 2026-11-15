# SUPPORTOPS-API-004 / M01 / WP1 — API Skeleton (implementation package)

> Статус: техническое задание, ничего не реализовано. Story: [`SUPPORTOPS-API-004`](../../.monolith/context/planned/SUPPORTOPS-API-004.md). Дизайн: [`api-003-product-track.md`](api-003-product-track.md) (§3, §11).
> Исполнитель: Beta. Зависимости пакета устанавливаются **только после** подтверждения владельца (§5).

## 1. Цель

Запускаемое FastAPI-приложение с контрактной оболочкой. Без PostgreSQL, без аутентификации, без worker, без реального LLM.

```text
FastAPI
   ├── /healthz
   ├── /readyz
   └── Demo Configuration
```

## 2. Состав

Новый пакет `src/supportops_service/` (существующий `supportops_agent` не изменяется):

| Файл | Содержание |
|---|---|
| `__init__.py` | версия пакета |
| `config.py` | `ServiceSettings` (Pydantic, из env с префиксом `SUPPORTOPS_`): `llm_mode` = `demo` (по умолчанию) \| `live`; `docs_enabled=false`; `environment`. Live без явной конфигурации ключа → ошибка старта; ключ LLM читается только в Live |
| `app.py` | `create_app(settings) -> FastAPI` (фабрика, без глобального состояния при импорте); подключение middleware, обработчиков ошибок и роутеров |
| `errors.py` | единый envelope `{"error": {"code", "message", "request_id"}}`; фиксированные коды и тексты; обработчики 404, 405, 422, 500 |
| `middleware.py` | `X-Request-ID` (валидный входящий `[A-Za-z0-9-]{1,64}` принимается, иначе генерируется; возвращается всегда); `Cache-Control: no-store` для `/v1` |
| `routers/health.py` | `GET /healthz`, `GET /readyz` |
| `llm_mode.py` | выбор режима; в WP1 — только флаг и проверка, что Demo не создаёт сетевых клиентов |

Тесты: `tests/service/test_app_skeleton.py`, `tests/service/test_errors.py`, `tests/service/test_config.py`.

## 3. Поведение

- `GET /healthz` → `200 {"status":"ok"}`, без обращений к зависимостям.
- `GET /readyz` → `200 {"status":"ready","llm_mode":"demo"}` при корректной конфигурации. Проверка БД добавляется в M02 (аддитивно); в WP1 readiness означает «конфигурация загружена».
- Неизвестный маршрут → `404` в envelope. Неверный метод → `405` в envelope.
- `422` содержит `fields: [{"path": [...], "type": "..."}]` и **не содержит значений input** (дефолтный обработчик FastAPI их отражает).
- Необработанное исключение → `500` с фиксированным envelope, без текста исключения и трассировки.
- `/docs`, `/redoc`, `/openapi.json` выключены по умолчанию.
- CORS не включается.
- Demo Mode: нет создания `OpenAI`-клиента, нет сетевых вызовов, детерминированное поведение. Live Mode в WP1 только конфигурируется, не вызывается.

## 4. Acceptance

1. `uvicorn --factory supportops_service.app:create_app` запускается локально, `/healthz` и `/readyz` отвечают.
2. 404/405/422/500 дают envelope; в теле 422 нет введённых значений (тест с маркерной строкой).
3. `X-Request-ID` возвращается всегда; невалидный входящий заменяется.
4. `/docs` недоступен по умолчанию.
5. Тесты проходят без сети (тест-страж: запрет `socket.connect` в фикстуре) и без PostgreSQL.
6. Demo Mode по умолчанию; Live без явной конфигурации не стартует.
7. Весь существующий набор тестов зелёный; файлы `src/supportops_agent/*` не изменены (`git diff --stat` показывает только новые файлы и зависимости).

## 5. Предлагаемые зависимости (не устанавливать до подтверждения)

Среда: Python 3.14.4 локально; проект `requires-python >=3.11`. Версии — последние на PyPI на 2026-11-15 (метаданные, без установки); финальный pin — по `uv lock` при установке.

| Пакет | Версия | Группа | Python | Совместимость |
|---|---|---|---|---|
| `fastapi` | `>=0.163.1,<0.164` | `service` | ≥3.11 | требует `pydantic>=2.9`, `starlette>=0.46`, `anyio>=4.1`; в `uv.lock` pydantic 2.14.1, anyio 4.15.1 — конфликтов нет |
| `uvicorn` | `>=0.54.0,<0.55` | `service` | ≥3.10 | ASGI-сервер; `uvicorn[standard]` не нужен |
| `psycopg[binary]` | `>=3.3.9,<3.4` | `service` (нужен с M02, не в WP1) | ≥3.10 | драйвер PostgreSQL (синхронный, SKIP LOCKED) |
| `psycopg-pool` | `>=3.3.3,<3.4` | `service` (с M02) | ≥3.10 | пул соединений |
| `httpx` | `>=0.28.1,<0.29` | `dev` | ≥3.8 | требуется `fastapi.testclient` |
| `pytest` | уже `>=9.2.0` | `dev` | — | без изменений |

Для WP1 достаточно: `fastapi`, `uvicorn`, `httpx` (dev). `psycopg`/`psycopg-pool` добавляются в M02. `pytest-asyncio` не нужен (sync-эндпоинты, sync `TestClient`). Изменения `pyproject.toml`/`uv.lock` — только на старте реализации, одним коммитом, владелец файла — Beta.

Не проверено: фактическая установка и `uv lock`; совместимость `openai>=3.19` с новыми `anyio`/`httpx` разрешит `uv lock`.

## 6. Границы владения Alpha / Beta (D-11)

Каждый production-файл имеет ровно одного владельца. Общий файл с двумя редакторами запрещён; изменения общего интерфейса — только по согласованию обеих сторон до начала работ.

**Beta**

| Область | Файлы |
|---|---|
| Приложение, конфигурация, ошибки, health | `src/supportops_service/{__init__,app,config,errors,middleware,llm_mode}.py`, `routers/health.py` |
| Персистентность (M02) | `src/supportops_service/db/**`, `migrations/**`, `repositories/{tickets,runs}.py`, `routers/tickets.py` |
| Worker (M03) | `src/supportops_service/worker/**`, интеграция `SupportAgent` |
| Зависимости | `pyproject.toml`, `uv.lock` (единственный владелец) |
| Тесты | `tests/service/{test_app*,test_errors,test_config,test_health,db/**,worker/**}` |

**Alpha**

| Область | Файлы |
|---|---|
| Аутентификация и tenant context (M02) | `src/supportops_service/security/{credentials,tenant_context,dependencies}.py`; PG-реализация `CredentialRepository` — `repositories/credentials.py` |
| Изоляция tenant, security regression | `tests/service/security/**` |
| Не затрагивается | `src/supportops_agent/**` — SEC-003 (Alpha, отдельная worktree/WP4) |

**Общие интерфейсы** (согласовать до M02, зафиксировать в `src/supportops_service/interfaces.py`, владелец файла — Beta, изменение только с подтверждением Alpha):

```text
TenantContext(tenant_id: str, credential_id: str, scopes: frozenset[str])   # неизменяемый
CredentialRecord(credential_id, tenant_id, key_prefix, key_hash, scopes, revoked_at)
CredentialRepository (Protocol): get_by_prefix(key_prefix) -> CredentialRecord | None
authenticate: FastAPI-зависимость -> TenantContext   (одинаковый 401 на все причины)
require_scope(scope) -> зависимость -> TenantContext (403 insufficient_scope)
Error envelope и коды — из errors.py (Beta)
```

Правила: Alpha не правит `app.py`/`errors.py` (подключает роутер/зависимости через публичные точки расширения, согласованные в WP1); Beta не правит `security/**`. Обе стороны работают в своих worktree и ветках; интеграция — через `main` по готовности, merge только по решению владельца. WP1 Alpha не блокирует: аутентификации в WP1 нет.

## 7. Не входит в WP1

PostgreSQL и миграции, аутентификация и `TenantContext` (реализация), тикеты, worker, вызовы LLM, trace-эндпоинт, установка зависимостей до подтверждения, изменение Authorization Gate, RefundPolicyEngine и `supportops_agent`.

## 8. Риски

- Дефолтный 422 FastAPI отражает input — закрывается тестом с маркером.
- Фабрика приложения не должна читать окружение при импорте (иначе тесты не изолированы).
- Изменение `uv.lock` затрагивает общий файл — один коммит владельца Beta, согласование с Alpha до слияния.
