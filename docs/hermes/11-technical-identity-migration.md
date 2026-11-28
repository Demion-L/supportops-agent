# Technical Identity Migration (E2-Phase 01)

**Дата:** 2026-11-28 · **Основание:** решение владельца HERMES E2-PHASE 01 · **Исходный план:** `06-technical-identity-migration-plan.md` (исторический).
Принцип: не механическая замена `SupportOps → HERMES`. SupportOps остаётся предметным доменом; HERMES — платформа.

## Карта идентификаторов

| Идентификатор | До | После | Решение и причина |
|---|---|---|---|
| Пакет HTTP-сервиса | `supportops_service` | `hermes_service` | **Переименован.** Сервис — платформенная инфраструктура (auth, БД, middleware), не зависит от агента. Внутренний путь импорта, документированной внешней точки входа нет (пример `uvicorn --factory` — в Epoch I design-документе). Совместимых псевдонимов нет: два пакета-двойника ослабили бы статические проверки границ. |
| Пакет агента | `supportops_agent` | `supportops_agent` | **Сохранён** — предметный домен SupportOps; будущий доменный модуль. |
| Дистрибутив | `supportops-agent` | `hermes` | **Переименован** (`pyproject.toml`, `uv.lock` пересобран `uv lock --offline`, версии зависимостей не менялись). Не публиковался. |
| Env: конфигурация сервиса | `SUPPORTOPS_{DEMO_MODE,DOCS_ENABLED,ENVIRONMENT,LLM_API_KEY,PEPPER,PEPPER_ID,PREVIOUS_PEPPERS,DATABASE_URL}` | `HERMES_*` | **Новый префикс + deprecated fallback.** Секреты и деплой-окружения вне репозитория: молчаливая потеря `PEPPER` инвалидировала бы все API-ключи. Правила: пустое значение = не задано; оба заданы и различаются → `ServiceConfigurationError` без значений (fail-closed); устаревшее имя → предупреждение в лог без значения. |
| Env: тестовая инфраструктура | `SUPPORTOPS_TEST_DATABASE_URL`, `SUPPORTOPS_REQUIRE_PG` | `HERMES_TEST_DATABASE_URL`, `HERMES_REQUIRE_PG` | Новый префикс, старый принимается (CI владельца). |
| Заголовок OpenAPI | `SupportOps Service API` | `HERMES Service API` | Изменён; пути, схемы, коды ответов не менялись. |
| PostgreSQL `application_name` | `supportops-service` | `hermes-service` | Изменён (операционный идентификатор в `pg_stat_activity`). |
| Логгеры сервиса | `supportops_service.*` | `hermes_service.*` | Следуют за пакетом. Правила алертов по имени логгера, если есть вне репозитория, нужно обновить. |
| Evaluation Framework | пакет `evaluation`, docstring «SupportOps Agent Evaluation Harness» | пакет `evaluation` (имя нейтрально), docstring «HERMES Evaluation Framework» | Recordings и датасеты **не менялись** (в них нет идентификаторов проекта). |
| Agent Commons Experiment | проверка «не импортирует `supportops_agent`» | + `hermes_service`, + запрет `hermes` в импортах эксперимента | Изоляция усилена. |
| Таблицы, функции, триггеры, SQL-миграции | — | без изменений | Не содержат названия проекта; checksums применённых миграций неприкосновенны. |
| Формат API-ключей, HTTP-контракты, security-инварианты | — | без изменений | Внешние контракты. |
| Story/Mission ID, ветки, ADR, документы Epoch I (`docs/design`, `docs/security`, `.monolith`) | `SUPPORTOPS-*` | без изменений | Историческая достоверность. Эти документы используют старые имена модулей (`supportops_service`). |
| Имя `monolith.config.json` (`supportops-agent`), каталог проекта, репозиторий `Demion-L/supportops-agent`, `origin` | — | без изменений | Требуют отдельного разрешения владельца; `monolith.config.json` связан с состоянием MONOLITH. |
| CLI entrypoints, Docker | отсутствуют | — | Нечего переименовывать (`[project.scripts]`, Dockerfile не существуют). |

## Проверка PG-набора и среды

Тесты выполняются в изолированном `.venv` worktree (`uv sync --frozen`); `hermes_service.__file__` и `supportops_agent.__file__` указывают в worktree, а не в `main`.

## Известные последствия

- Внешние скрипты, импортирующие `supportops_service`, или команды `uvicorn supportops_service.app:create_app` необходимо обновить на `hermes_service`.
- Устаревший префикс `SUPPORTOPS_` планируется убрать отдельным решением после обновления окружений.
