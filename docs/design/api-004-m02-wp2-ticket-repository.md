# SUPPORTOPS-API-004 / M02-WP2 — TicketRepository & Idempotency (техническое задание)

> **Статус:** ТЗ. **Реализация не начата** и не начинается до (1) согласования интерфейсов с Alpha (`api-004-interface-alignment-and-integration-gate.md`) и (2) явного решения владельца.
> **Основание:** `e6bf9b1` (PostgreSQL Foundation), `api-003-product-track.md` §11, §13, `api-004-m02-persistence-foundation.md` §4–§5, §9.
> **Автор:** Beta. **Дата:** 2026-11-16.

## 1. Цель

Создание тикета и run **в одной PostgreSQL-транзакции**, идемпотентно, с изоляцией tenant; чтение тикета по `(tenant_id, ticket_id)`. Без worker, без вызова LLM, без `SupportAgent`.

## 2. Не входит

`RunQueue` и любые переходы run (WP3), PG-реализация `CredentialRepository` (Alpha), БД-роли (WP4), `/readyz` с БД (WP4), retention, rate limit, изменения `src/supportops_agent/**`, Gate, `RefundPolicyEngine`, Redis/RabbitMQ/ORM.

## 3. Предусловия (блокирующие)

| # | Предусловие | Статус |
|---|---|---|
| P1 | Подтверждён `interfaces.py` (TenantContext, CredentialRecord, Scope, Clock) | ожидает Alpha/владельца |
| P2 | Канонические коды `unauthenticated`, `insufficient_scope`, `service_unavailable` + `idempotency_key_reuse` в `errors.py` | решение владельца есть; изменение публичного поведения — по согласованию |
| P3 | `SecuredRoute` для `/v1` (Alpha, `security/dependencies.py`) | реализован у Alpha (`95cb791`), не интегрирован |
| P4 | Решение по 55P03/57014 (таймаут блокировки) → 503 или иное (вопрос 11 M02) | открыт; по умолчанию 503 |
| P5 | HTTP-слой WP2 использует `authenticate`/`require_scope` Alpha, а не свои | зависит от P1–P3 |

Если P1–P3 не закрыты, WP2 может быть реализован **только до границы репозитория** (слои 1–2 §4), без HTTP-роутеров.

## 4. Состав работ

```text
src/supportops_service/
  ids.py                       # new_ticket_id(), new_run_id(): 'tkt_'/'run_' + secrets.token_hex(16); is_ticket_id()
  repositories/
    __init__.py
    protocols.py               # TicketRepository (Protocol), TicketView, CreateResult
    tickets.py                 # PgTicketRepository(pool)
    fakes.py                   # InMemoryTicketRepository — тот же контрактный тест
  db/errors.py                 # + IdempotencyConflict
  schemas.py                   # Pydantic: CreateTicketRequest/Response, TicketResponse (extra=forbid)
  routers/tickets.py           # POST /v1/tickets, GET /v1/tickets/{ticket_id}   [только после P1–P3]
  errors.py                    # + 409 idempotency_key_reuse, 400 idempotency_key_required, 415, 503 (по согласованию)
tests/repositories/, tests/service/test_tickets_api.py
```

Слои: (1) `ids`, `schemas`, `request_hash` → (2) репозиторий PG + fake → (3) HTTP-роутеры. Слои 1–2 не требуют решений по P1–P3.

## 5. Контракт репозитория

```python
class TicketRepository(Protocol):
    def create_ticket(
        self, *, tenant_id: str, idempotency_key: str,
        customer_id: str, message: str, order_id: str | None,
    ) -> CreateResult: ...          # CreateResult(ticket_id, run_id, status, created_at, replayed: bool)

    def get_ticket(self, *, tenant_id: str, ticket_id: str) -> TicketView | None: ...
```

- `tenant_id` — keyword-only, **без значения по умолчанию**, `str` (не `TenantContext`). Вызов без него → `TypeError` (тест).
- Метода «по id без tenant» нет. `TicketView` не содержит `message`/`customer_id`/`order_id` (в API не возвращаются, api-003 §11.2) — репозиторий для API их не читает вовсе.
- `request_hash` вычисляется репозиторием из `(customer_id, message, order_id)` (не доверяется вызывающему): `sha256(json.dumps({"customer_id", "message", "order_id"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))`; `order_id=None` → `null`. Нормализация (strip) — в Pydantic до репозитория.
- Исключения: `IdempotencyConflict` (ключ уже использован с другим hash); `StorageUnavailable` (недоступность БД/таймаут); прочие — не перехватываются (→ generic 500, без текста). Тексты фиксированы, значений параметров нет.

## 6. Алгоритм `create_ticket` (PG)

```text
with pool.connection() as conn, conn.transaction():          # READ COMMITTED
    ticket_id, run_id = ids
    row = INSERT INTO tickets (ticket_id, tenant_id, customer_id, message, order_id, idempotency_key, request_hash)
          VALUES (%s,...) ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
          RETURNING ticket_id, created_at
    if row:                                                    # новый ключ
        INSERT INTO runs (run_id, tenant_id, ticket_id) VALUES (%s,%s,%s)   # status/attempt — DEFAULT
        return CreateResult(..., status="accepted", replayed=False)
# транзакция закрыта (rollback тут не нужен — вставки не было)
SELECT t.ticket_id, t.request_hash, t.created_at, r.run_id, r.status
  FROM tickets t JOIN runs r ON r.tenant_id = t.tenant_id AND r.ticket_id = t.ticket_id
 WHERE t.tenant_id = %s AND t.idempotency_key = %s
compare_digest(stored, new) ? replayed result : raise IdempotencyConflict
```

Свойства, которые обязаны быть доказаны тестами:

1. Оба INSERT в одной транзакции: сбой после первого → **0 строк** в обеих таблицах.
2. Конкурентные первые запросы: второй INSERT блокируется на уникальном индексе до коммита первого, затем `DO NOTHING` → ветка replay → 1 тикет, 1 run. Если первая транзакция откатилась — второй вставляет сам.
3. Replay-SELECT выполняется **после** коммита/решения конфликта, поэтому строка гарантированно видна и `runs` уже есть (run вставляется в той же транзакции, что и тикет).
4. Replay и conflict **не пишут** ничего (нет побочных INSERT/UPDATE, нет расхода `ticket_id`).
5. Всё параметризовано (`%s`), SQL-строки — константы модуля; ни одной f-строки с данными. Статический тест по AST: в `repositories/` нет f-string/`%`/`.format` внутри аргумента `execute`.
6. Столкновение `ticket_id`/`run_id` (PK, 128 бит) — не обрабатывается ретраем; это `IntegrityError` → generic 500 (практически невозможно). Не маскируется под replay: replay определяется **только** по `(tenant_id, idempotency_key)`.
7. Таймаут блокировки на конкурентном INSERT (`lock_timeout` 2 с) → `StorageUnavailable` (503) по умолчанию; клиент повторяет с тем же ключом и получает replay. Альтернатива (P4) — решение владельца.

`get_ticket`: если `ticket_id` не соответствует `^tkt_[0-9a-f]{32}$` → `None` **без обращения к БД**; иначе `SELECT … WHERE t.tenant_id = %s AND t.ticket_id = %s` (JOIN `runs` по составному ключу). Чужой/несуществующий/некорректный → один и тот же `None` → один и тот же 404 (одинаковые тело, заголовки, статус).

## 7. HTTP-контракты (реализуются после P1–P3)

### `POST /v1/tickets`

| | |
|---|---|
| Scope | `tickets:write` |
| Порядок проверок | 401 → 403 → `Idempotency-Key` (400) → размер ≤16 KiB (413) → Content-Type (415) → валидация (422) → операция (`SecuredRoute` гарантирует, что auth раньше разбора тела) |
| Заголовок | `Idempotency-Key` `[A-Za-z0-9._:-]{8,100}`; ключ не логируется целиком |
| Тело | `{"customer_id": 1–100, "message": 1–4000, "order_id": 1–100 \| null}`, `extra=forbid` (`tenant_id`, `ticket_id`, `run_id`, `status` → 422) |
| Tenant | **только** `ctx.tenant_id` из credential; `X-Tenant-Id` и тело игнорируются/запрещены |
| 202 | `{"ticket_id","run_id","status":"accepted","created_at"}` + `Location: /v1/tickets/{ticket_id}` |
| 200 | replay: те же id, текущий `status`, `Idempotent-Replayed: true` |
| 409 | `idempotency_key_reuse` — другой hash при том же ключе |
| 503 | `service_unavailable` — `StorageUnavailable`; ничего не создано |
| Побочные эффекты | **Никаких** вызовов LLM/`SupportAgent`/сети в обработчике (тест: monkeypatch сети/`openai` + AST-запрет импорта `supportops_agent`) |

### `GET /v1/tickets/{ticket_id}`

Scope `tickets:read`. 200: `{"ticket_id","run_id","status","created_at","started_at"?,"finished_at"?,"answer"?,"failure"?}`; в WP2 run всегда `accepted` (исполнителя нет), поэтому `answer`/`failure` не появляются, но схема ответа и сериализация поддерживают их (проверяются через прямую подготовку строк в тесте). Не возвращает `message`, `customer_id`, `order_id`. 404 `not_found` для чужого/несуществующего/некорректного. 503 при `StorageUnavailable`.

Lifecycle `DatabasePool` в приложении (`open` в lifespan, `close` при остановке; без соединения при импорте/`create_app`) — часть WP2 (слой 3) и согласуется с Alpha в `create_app` (см. alignment doc).

## 8. Безопасность

- Tenant — только из credential; `customer_id` — доверенное поле tenant-backend, **не** определяется моделью и не берётся из `message`.
- Все запросы параметризованы; `tenant_id` в каждом `WHERE` по tenant-данным; `INSERT` берёт `tenant_id` из аргумента.
- Ошибки БД не раскрывают SQL/DSN/значения: `StorageUnavailable` — фиксированный текст; generic handler логирует только класс исключения (уже реализован в WP1). Тест с маркером в `message`/`Idempotency-Key`: маркер не появляется ни в ответе, ни в логах при отказе БД, конфликте и нарушении ограничения.
- Конфликт идемпотентности не раскрывает данные первого запроса (в теле 409 — только фиксированный код/текст).
- Fail-closed: любая необработанная ошибка БД → не 2xx; транзакция откатывается.
- Роль БД `api` в WP4 не получает UPDATE/DELETE на `tickets`; WP2 не рассчитывает на роли (триггер уже защищает).

## 9. Тесты (все PG-тесты — с `SUPPORTOPS_REQUIRE_PG=1`)

| Группа | Проверки |
|---|---|
| Unit | `ids` (формат, уникальность, regex БД), `request_hash` (канонизация, порядок ключей, `null`, Unicode), Pydantic-схемы (`extra=forbid`, границы), `TypeError` без `tenant_id`, `tenant_id` не по умолчанию |
| Контрактные (parametrized: PG + fake) | create → get; replay; conflict; два tenant с одним ключом независимы; чужой/несуществующий/некорректный id → идентичный `None` |
| PG atomic | сбой между INSERT ticket и INSERT run (инъекция через триггер/патч курсора) → 0 строк; успех → 1+1 |
| PG concurrency | N=20 потоков с одним ключом и одним телом → 1 тикет, 1 run, 1 `replayed=False`, 19 `replayed=True`; N=20 с одним ключом и **разными** телами → ровно один победитель, остальные `IdempotencyConflict` или replay (одинаковое тело), ни одного лишнего run; параллельные разные ключи → все созданы |
| PG replay | повтор не меняет число строк; `created_at` и id неизменны; replay после смены статуса run (подготовленной напрямую) возвращает текущий статус |
| Crash consistency | процесс/соединение обрывается после первого INSERT без COMMIT (`pg_terminate_backend`) → строк нет; повтор ключа создаёт тикет заново; обрыв **после** COMMIT → replay возвращает те же id |
| Нет двойного run | `UNIQUE (ticket_id)` + счёт `runs` после всех сценариев = числу тикетов |
| Failure | недоступная БД/закрытый pool → `StorageUnavailable`; lock_timeout на конкурентном INSERT; маркер секрета отсутствует в исключении/логе |
| HTTP (после P1–P3) | 401/403 раньше 400/413/415/422; 202/200/409/503; `Location`, `Idempotent-Replayed`; `tenant_id`/`ticket_id` в теле → 422; чужой GET = несуществующий GET (байтовое равенство тел, кроме `request_id`); нет вызова LLM; отказ БД → 503 без утечки |
| Статические | нет f-string SQL; API-слой не импортирует `runs_queue`/`supportops_agent`; `git diff` не затрагивает `src/supportops_agent/**` |

## 10. Критерии приёмки

1. Все свойства §6 доказаны PG-тестами на PostgreSQL 16 с `SUPPORTOPS_REQUIRE_PG=1`, 0 skipped.
2. Ни одного пути создания тикета без run и run без тикета.
3. 20 параллельных одинаковых запросов → 1 тикет, 1 run.
4. Чужой/несуществующий/некорректный id неразличимы на уровне репозитория и HTTP.
5. Отказ БД → 503 (HTTP) / `StorageUnavailable` (репозиторий), без текста исключения.
6. Нет вызовов LLM в обработчиках; `src/supportops_agent/**` не изменён.
7. Полный регресс зелёный; fake и PG проходят один и тот же контрактный набор.

## 11. Оценка и порядок

Слой 1–2 (ids, hash, схемы, репозиторий, fake, контрактные + PG-тесты): основная часть работ, не зависит от Alpha. Слой 3 (роутеры, errors, lifespan pool): после P1–P3 и интеграции Alpha; изменение публичных сигнатур — только с согласия владельца.

## 12. Риски WP2

| # | Риск | Митигация |
|---|---|---|
| W1 | Таймаут блокировки на конкурентных дублях даёт 503 вместо replay | клиент повторяет с тем же ключом; решение P4 |
| W2 | Retention по D-8 (DELETE тикета) сбрасывает окно идемпотентности | ADR M02 §3.2 |
| W3 | `OperationalError` из тела блока в пуле глотает диагностику | осознанно (fail-closed); диагностика — метрики WP-8 |
| W4 | Расхождение fake и PG | общий контрактный набор |
| W5 | Рост размера `message` (хранение PII) | лимит 4000, retention вне WP2 |
