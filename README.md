# HERMES

**Modular Multi-Tenant Business Orchestration Platform**

*One orchestrator. Multiple capabilities. Infinite business configurations.*

> Latysh Development · Epoch II: Genesis · 27 ноября 2026
> Историческое название первой эпохи и первого предметного домена — **SupportOps Agent**.

---

## Что такое HERMES

HERMES — проект модульной мультитенантной платформы, которая должна исполнять индивидуальные наборы бизнес-возможностей (инструменты, интеграции, политики, знания, workflow, специализированные ИИ-агенты, задачи для человека) для разных организаций, не позволяя ни одному модулю получить полномочия только из-за решения LLM.

HERMES **не создан с нуля**. Он наследует весь инженерный фундамент SupportOps Agent: код, историю Git, security contracts, тесты, persistence, документацию и незавершённые направления. Название и предназначение утверждены владельцем; **конкретная архитектура ещё не утверждена** и исследуется (см. `docs/hermes/`).

## Статус (честно)

| Что | Состояние |
|---|---|
| Имя и предназначение | Утверждено владельцем 2026-11-27 |
| Предметный домен | Единственный реализованный — **SupportOps** (клиентская поддержка, заказы, возвраты) |
| Агентное ядро | Рабочее, жёстко защищённое, но однодоменное и однотенантное (в ядре нет понятия tenant) |
| Сервисный слой | API-скелет, аутентификация по API-ключам, tenant из credential, PostgreSQL, репозиторий тикетов |
| Оркестрация модулей | **Не реализована.** Это гипотеза, а не возможность |
| Готовность к production | Не заявляется |

Гипотеза «SupportOps — фундамент оркестратора» **подтверждена частично**; границы подтверждённого и неподтверждённого — в `docs/hermes/FINAL-REPORT.md`.

## Навигация по документам эпохи II

| Документ | Содержание |
|---|---|
| [`docs/hermes/FINAL-REPORT.md`](docs/hermes/FINAL-REPORT.md) | Итоговый отчёт (27 разделов), начинать отсюда |
| [`docs/hermes/00-owner-decision-2026-11-27.md`](docs/hermes/00-owner-decision-2026-11-27.md) | Запись решения владельца |
| [`docs/hermes/01-identity-and-genesis.md`](docs/hermes/01-identity-and-genesis.md) | Идентичность, миссия, видение, переход SupportOps → HERMES |
| [`docs/hermes/02-architecture-inventory.md`](docs/hermes/02-architecture-inventory.md) | Подтверждённая карта архитектуры + матрица переиспользования (A–E) |
| [`docs/hermes/03-modular-orchestration-research.md`](docs/hermes/03-modular-orchestration-research.md) | Capability-модули, мультитенантная композиция, оркестрация, каналы, outcome-метрики |
| [`docs/hermes/04-alternatives-and-commercial-validation.md`](docs/hermes/04-alternatives-and-commercial-validation.md) | Альтернативы, кандидаты A–D, минимальный эксперимент, демо AI Dispatcher |
| [`docs/hermes/05-stc-and-ecosystem.md`](docs/hermes/05-stc-and-ecosystem.md) | Совместимость с STC, границы экосистемы Latysh Development |
| [`docs/hermes/06-technical-identity-migration-plan.md`](docs/hermes/06-technical-identity-migration-plan.md) | План безопасного технического переименования |
| [`docs/hermes/07-engineering-continuity.md`](docs/hermes/07-engineering-continuity.md) | Влияние на текущие направления разработки |
| [`docs/hermes/08-independent-architecture-review.md`](docs/hermes/08-independent-architecture-review.md) | Независимое ревью (способное опровергнуть гипотезу) |
| [`docs/hermes/09-epoch-ii-roadmap.md`](docs/hermes/09-epoch-ii-roadmap.md) | Epoch II Roadmap: фазы E2-Phase 00–10 |
| [`docs/hermes/10-post-genesis-state.md`](docs/hermes/10-post-genesis-state.md) | Актуальное состояние после Genesis: worktree, security contract, архив эксперимента |
| [`docs/architecture/codebase-layout.md`](docs/architecture/codebase-layout.md) | Структура пакетов `supportops_agent`, слои зависимостей, соответствие старых и новых путей (E2-Phase 01) |

Документы Epoch I (`docs/design/`, `docs/security/`, `.monolith/context/`) сохранены без изменений и остаются историческим источником истины для эпохи SupportOps.

## Технические идентификаторы (E2-Phase 01)

| Идентификатор | Состояние |
|---|---|
| Python-пакет HTTP-сервиса | `supportops_service` → **`hermes_service`** (старый путь импорта не сохранён) |
| Дистрибутив (`pyproject.toml`, `uv.lock`) | `supportops-agent` → **`hermes`** |
| Переменные окружения | **`HERMES_*`**; `SUPPORTOPS_*` принимаются как устаревший fallback, при расхождении значений — отказ (fail-closed) |
| Заголовок OpenAPI, `application_name` PostgreSQL, имена логгеров сервиса | `HERMES Service API`, `hermes-service`, `hermes_service.*` |
| Python-пакет `supportops_agent` | **сохранён** — предметный домен SupportOps |
| Таблицы/миграции БД, формат API-ключей, HTTP-контракты, Story/Mission/ADR, GitHub-репозиторий и `origin` | **сохранены** |

Полная карта «до/после» и обоснование — `docs/hermes/11-technical-identity-migration.md`.

## Экосистема Latysh Development

HERMES — один из самостоятельных проектов: MONOLITH (процесс разработки, не runtime-зависимость), SPHYNX, STC, KAIROS, AGORA, FEMIDA, SYMPOSIUM. Никакой общий runtime и никакие межпроектные зависимости не вводятся без отдельного решения владельца.

---

Before start to work run this: source .venv/bin/activate
