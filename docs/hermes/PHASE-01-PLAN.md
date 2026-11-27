# HERMES · Phase 01 — Engineering Consolidation: план (2026-11-27)

## 1. Проверенная baseline

- `main` @ `5afdf2d`. Полный регресс на изолированной PostgreSQL 16 (Docker, `SUPPORTOPS_REQUIRE_PG=1`): **1072 passed, 0 skipped** (10 мин 19 с).
- Воспроизведение: `docker run -d --rm --name hermes-pg-test -e POSTGRES_PASSWORD=<pw> -p 127.0.0.1:55439:5432 postgres:16-alpine`;
  `SUPPORTOPS_TEST_DATABASE_URL=postgresql://postgres:<pw>@127.0.0.1:55439/postgres SUPPORTOPS_REQUIRE_PG=1 .venv/bin/python -m pytest -q`.
  Локальная PG через unix-сокет не годится: один тест (bad credentials) ложно падает (trust/peer).
- Срез `GET /v1/tickets/{id}` (untracked, worktree `api004-http`): **33 passed** на PG. В `create_app` роутер не подключён (тесты монтируют его сами).

## 2. Инвентаризация работ вне `main`

| Актив | Где | Статус | Действие |
| ----- | --- | ------ | -------- |
| EVAL-001 Harness + Record/Replay | `eval/supportops-eval-001`, +2 коммита, 0 behind | Коммиты есть, не влиты | Merge — нужно разрешение |
| Дизайн Live-eval (60 КБ) | untracked, worktree eval-001 | Только на диске | Закоммитить в ветке eval — нужно разрешение |
| `GET /v1/tickets/{id}` + 2 файла тестов + schemas | untracked, worktree api004-http | Проверен (33 passed), не подключён | Подключить в `create_app`, закоммитить — нужно разрешение |
| Agent Commons experiment | untracked `experiments/`, worktree commons-exp-001 | Изолированный эксперимент, 26 behind | Решение владельца: сохранить коммитом в ветке или архивировать |
| Security contract rev 2 | untracked, worktree api004-alpha | Устарел (rev 3 в `main`) | Удалить после разрешения |
| `docs/hermes/*`, `README.md`, `.monolith/context/maps/roadmap-epoch-2-hermes.md` | `main`, untracked/modified | Только на диске | Коммит — нужно разрешение |
| Ветки `design/…`, `integration/…`, `test/…-wp4`, `feat/…beta-wp2` | 0 уникальных коммитов | Влиты | Удаление веток — только с разрешения |

Уникальных коммитов, которым что-либо угрожает, нет, кроме двух EVAL-001. **Риск потери — untracked-файлы** (всего 5 наборов).

## 3. Задачи Phase 01 и зависимости

```
[A0 Сохранить документы эпохи II (commit)]──┐
[A1 Решение customer_id / идентичность]────►[B1 Подключить GET /v1/tickets в create_app]──►[B2 API-004 M02-остаток: POST /v1/tickets]
[A2 Merge EVAL-001 + commit Live-eval design]──►[C1 Evaluation в baseline, regression на PG]
[A3 Разрешить G1 (state.json)]──►[D1 Удаление влитых worktree]
[B*, C1]──►[E1 Codebase Organization]──►[E2 Technical Identity Migration (алиасы env, fail-closed)]──►[F Verified Baseline]
```

Реальные блокеры — только разрешения владельца (commit/merge) и решение по `customer_id`. Технических зависимостей между B и C нет.

## 4. Потоки

### Поток 1 — API-004 Completion (worktree: `supportops-agent-api004-http`, ветка `feat/supportops-api004-http-slice`)
- **Цель/scope:** подключить `GET /v1/tickets/{id}` в `create_app`, тест подключения, security regression, PG regression. `POST /v1/tickets` — только после решения владельца о доверенном источнике `customer_id`.
- **Результат:** согласованное состояние API-004 M02 в ветке; отчёт.
- **Приёмка:** новые + прежние тесты зелёные на PG 16 (`REQUIRE_PG=1`); cross-tenant/unknown/malformed id → одинаковый 404; tenant только из `TenantContext`; публичные контракты не ломаются.
- **Зависимости:** разрешение на commit; решение по `customer_id` (для POST).
- **Конфликты:** трогает `src/supportops_service/`, `tests/service|integration`. С потоком 2 не пересекается.

### Поток 2 — Evaluation Integration (worktree: `supportops-agent-eval-001`, ветка `eval/supportops-eval-001`)
- **Цель/scope:** сохранить дизайн Live-eval, довести ветку до интегрируемого состояния (rebase не нужен: 0 behind), регресс на PG, подготовить merge в `main`.
- **Результат:** ветка готова к merge; отчёт о покрытии harness и recordings.
- **Приёмка:** 1072 + тесты eval зелёные на PG; платных LLM-вызовов нет; recordings синтетические; нет секретов/ПД в recordings.
- **Зависимости:** разрешение на commit; merge — отдельное разрешение.
- **Конфликты:** `src/supportops_agent/evaluation/`, `tests/evaluation/`, `docs/evaluation/`. Пересечений с потоком 1 нет. Общее: `README.md`/`docs/hermes` — не трогать.

Оба потока **не** должны трогать `.monolith/state.json`, MONOLITH, `main` и друг друга. Старт потоков без разрешения на commit даёт только проверки/правки на диске.

## 5. Промпты

**Промпт потока 1.**
> Ты работаешь в `/home/demion/projects/AI/SUPPORT-AGENT-OPS/supportops-agent-api004-http` (ветка `feat/supportops-api004-http-slice`). Прочитай `AGENTS.md`, `.monolith/context/planned/SUPPORTOPS-API-004.md`, `docs/security/api-004-security-contract.md`, `docs/hermes/PHASE-01-PLAN.md`. Режим: implementation. Задача: подключить уже написанный (untracked) `create_tickets_router` к `create_app` с учётом scope `tickets:read`, добавить тест подключения, не добавлять `POST /v1/tickets`. Тесты: изолированная PG 16 в Docker (см. план §1), `SUPPORTOPS_REQUIRE_PG=1`, полный набор должен остаться зелёным. Запрещено: commit/merge/push без явного разрешения владельца, правка `.monolith/state.json`, MONOLITH, security contract, реальные LLM-вызовы, изменения публичных контрактов. Любое изменение security contract — сначала конкретное предложение владельцу. Итоговый отчёт на русском.

**Промпт потока 2.**
> Ты работаешь в `/home/demion/projects/AI/SUPPORT-AGENT-OPS/supportops-agent-eval-001` (ветка `eval/supportops-eval-001`). Прочитай `AGENTS.md`, `.monolith/context/planned/SUPPORTOPS-EVAL-001.md`, `docs/evaluation/*`, `docs/hermes/PHASE-01-PLAN.md`. Режим: implementation/review. Задача: проверить harness и Record/Replay на изолированной PG 16 (план §1), убедиться, что recordings синтетические и без ПД/секретов, что платных LLM-вызовов нет, и подготовить отчёт о готовности ветки к merge в `main`. Запрещено: commit/merge/push без явного разрешения владельца, правка `.monolith/state.json`, MONOLITH, живые LLM-вызовы, реализация Live-eval (только дизайн существует). Итоговый отчёт на русском.

## 6. Не стартовало (намеренно)

Codebase Organization и Technical Identity Migration — после потоков 1–2 (иначе конфликты путей и потеря untracked-работы). Миграция: по `06`, фаза 1 — алиасы env с fail-closed при конфликте; репозиторий не переименовывать. Phase 02+ не начинались.
