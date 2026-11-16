# SUPPORTOPS-API-004 / M02 — Trusted Identity & Persistence (PostgreSQL Foundation)

> **Статус:** M02-WP1 (PostgreSQL Foundation) **реализован** и закоммичен (`e6bf9b1`); остальное (§5, §6–§8, роли, `/readyz`, репозитории) — **проект**, не реализовано. Каждый раздел ниже помечен: `[РЕАЛИЗОВАНО]` или `[ПРОЕКТ]`.
> **Дата:** 2026-11-16. **Основание:** WP1 FastAPI закоммичен (`b33191b`); `api-003-product-track.md` (§5–§6, §14–§15); `docs/security/api-004-security-contract.md` (Alpha).
> **Проверено (PostgreSQL 16.14, 55 PG-/unit-тестов, `SUPPORTOPS_REQUIRE_PG=1`, 0 skipped):** pool lifecycle, отсутствие соединений при импорте, checksum, advisory lock, конкурентные runner, ограничения схемы, триггеры неизменяемости, отказ БД без утечки секретов. **Не проверялось:** CAS/lease/fencing/идемпотентность (SQL ниже — эскизы WP2/WP3), БД-роли, RLS, нагрузка.
> **Решения владельца (2026-11-16):** PostgreSQL 16 (dev/CI); sync `psycopg 3` + `psycopg-pool`, без ORM; `tenants` отложена до второго tenant; `schema_migrations` создаёт runner; роли — отдельным ops-скриптом (не реализованы).

---

## 1. Scope

**Реализовано в WP1:** `db/pool.py`, `db/migrate.py`, `db/errors.py`, миграция `0001`, PG-тестовая инфраструктура и тесты (`tests/db/`). **Остальное — проект.**

**Входит (Beta):** PostgreSQL-схема (`api_credentials`, `tickets`, `runs`), версионированные миграции, репозитории `tickets`/`runs` для API, внутренний worker-интерфейс очереди (без реализации worker), транзакционные границы, идемпотентность, CAS-переходы, lease, fencing, семантика отказов и политика восстановления, `/readyz` с проверкой БД и версии схемы, PG-тестовая инфраструктура.

**Входит (Alpha, зависит от наших таблиц):** PG-реализация `CredentialRepository`, `authenticate`, `require_scope`, security-тесты. Схему `api_credentials` определяет контракт Alpha §3.3; DDL и миграция — у Beta.

**Не входит:** worker-процесс и исполнение агента, подключение `SupportAgent`, `tenants`/`support_cases`/`run_steps`/`audit_events`, RLS, rate limit, выдача/ротация ключей через API, Redis/RabbitMQ, ORM. Любое изменение `src/supportops_agent/**`, Authorization Gate, `RefundPolicyEngine` — вне scope (стоп-условие).

## 2. Components

`[РЕАЛИЗОВАНО]` — только `db/*`; остальные модули — `[ПРОЕКТ]`.

```text
src/supportops_service/
  db/
    pool.py            # [РЕАЛИЗОВАНО] DatabaseSettings (SecretStr DSN) + DatabasePool: sync ConnectionPool,
                       #   open=False (нет I/O при создании), autocommit, dict_row, таймауты сессии
    migrate.py         # [РЕАЛИЗОВАНО] runner: schema_migrations, advisory lock, checksum
    migrations/
      0001_initial.sql # [РЕАЛИЗОВАНО] api_credentials, tickets, runs, индексы, триггеры
    errors.py          # [РЕАЛИЗОВАНО] StorageUnavailable, MigrationError(+Checksum/Set);
                       # [ПРОЕКТ] IdempotencyConflict, FencingRejected — в WP2/WP3
  repositories/
    protocols.py       # TicketRepository (API), RunQueue (worker) — РАЗНЫЕ Protocol
    tickets.py         # PG-реализация TicketRepository
    runs_queue.py      # PG-реализация RunQueue (claim/heartbeat/complete/fail/reap)
    credentials.py     # Alpha
    fakes.py           # in-memory реализации (тесты без PG)
  ids.py               # tkt_/run_ + 128 бит
  routers/health.py    # /readyz: SELECT 1 + версия схемы
```

**Решение: sync psycopg 3.** Существующий `SupportAgent` и клиент LLM блокирующие; worker синхронный; FastAPI запускает sync-эндпоинты в threadpool. Sync-pool проще для fencing-транзакций и тестов. Async не даёт выигрыша при одном API и одном worker. Пересмотр — при реальной нагрузке.

Зависимости (добавлены, `e6bf9b1`): `psycopg[binary]>=3.2,<4`, `psycopg-pool>=3.2,<4`; `pyproject.toml`: `package-data` для `db/migrations/*.sql` и pytest-маркер `postgres`.

**Pool (`db/pool.py`).** Конструктор не соединяется и не запускает потоки; `open()` явный и ждёт рабочего соединения (`open_timeout`); `close()` идемпотентен. Соединения: `autocommit=True`, `dict_row`, `connect_timeout`, `application_name`, и сессионные `statement_timeout` (5 с), `lock_timeout` (2 с), `idle_in_transaction_session_timeout` (10 с). Ошибки получения соединения (`PoolTimeout`, `PoolClosed`, `OperationalError`) → `StorageUnavailable` с фиксированным текстом, `from None` (сообщение драйвера и DSN не уходят наружу). Остальные ошибки тела (нарушения ограничений) проходят без изменений — их трактует вызывающий. DSN — `SecretStr`, не попадает в `repr`; ошибки конфигурации не эхо-ят значения. **Известное ограничение:** `psycopg.OperationalError` из тела блока тоже превращается в `StorageUnavailable`, включая `lock_timeout`/`statement_timeout` (SQLSTATE 55P03/57014 — подклассы OperationalError); для API это 503 (fail-closed), но в WP2 нужно решить, считать ли таймаут блокировки 503 или 409/retry.

## 3. Database Schema

Эскиз основан на `api-003` §5.1 с уточнениями по контракту Alpha.

`[РЕАЛИЗОВАНО]` — DDL ниже соответствует `0001_initial.sql` (кроме `schema_migrations`: её создаёт runner, §3.1, не файл миграции).

```sql
CREATE TABLE api_credentials (
  credential_id uuid PRIMARY KEY,
  tenant_id     text   NOT NULL CHECK (tenant_id ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
  key_prefix    text   NOT NULL UNIQUE CHECK (key_prefix ~ '^[a-z2-7]{12}$'),
  key_hash      bytea  NOT NULL CHECK (octet_length(key_hash) = 32),
  pepper_id     text   NOT NULL,
  scopes        text[] NOT NULL,                 -- сырые строки; фильтр по Scope — в Alpha
  created_at    timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz,
  revoked_at    timestamptz,
  last_used_at  timestamptz);

CREATE TABLE tickets (                           -- append-only
  ticket_id       text PRIMARY KEY CHECK (ticket_id ~ '^tkt_[0-9a-f]{32}$'),
  tenant_id       text NOT NULL CHECK (tenant_id ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
  customer_id     text NOT NULL,
  message         text NOT NULL,
  order_id        text,
  idempotency_key text  NOT NULL,
  request_hash    bytea NOT NULL CHECK (octet_length(request_hash) = 32),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, idempotency_key),
  UNIQUE (tenant_id, ticket_id));

CREATE TABLE runs (
  run_id        text PRIMARY KEY CHECK (run_id ~ '^run_[0-9a-f]{32}$'),
  tenant_id     text NOT NULL,
  ticket_id     text NOT NULL,
  status        text NOT NULL DEFAULT 'accepted'
                CHECK (status IN ('accepted','running','completed','failed')),
  attempt       int  NOT NULL DEFAULT 0 CHECK (attempt >= 0),   -- fencing token
  max_attempts  int  NOT NULL DEFAULT 1 CHECK (max_attempts >= 1),
  lease_owner   text,
  lease_until   timestamptz,
  answer        text,
  failure_code  text CHECK (failure_code ~ '^[a-z_]{1,64}$'),
  created_at    timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  finished_at   timestamptz,
  UNIQUE (ticket_id),
  UNIQUE (tenant_id, run_id),
  FOREIGN KEY (tenant_id, ticket_id) REFERENCES tickets (tenant_id, ticket_id),
  CHECK (status <> 'completed' OR (answer IS NOT NULL AND finished_at IS NOT NULL)),
  CHECK (status <> 'failed'    OR (failure_code IS NOT NULL AND finished_at IS NOT NULL)),
  CHECK (status <> 'running'   OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL AND attempt >= 1)),
  CHECK (status NOT IN ('completed','failed') OR (lease_owner IS NULL AND lease_until IS NULL)),
  CHECK (attempt <= max_attempts));

CREATE INDEX runs_claim_idx ON runs (created_at) WHERE status = 'accepted';
CREATE INDEX runs_lease_idx ON runs (lease_until) WHERE status = 'running';

-- Триггеры (defence in depth, независимо от ролей; сообщения фиксированы, значения строк не эхо-ятся;
-- SQLSTATE integrity_constraint_violation):
--   tickets_no_update  BEFORE UPDATE ON tickets      -> любое UPDATE запрещено (append-only)
--   runs_guard         BEFORE UPDATE ON runs         -> run_id/tenant_id/ticket_id/created_at неизменны;
--                                                      из completed/failed изменений нет; attempt не убывает
```

DELETE триггерами **не** блокируется: `tickets` защищена от удаления составным FK из `runs`; `runs` удалить можно (нужна привилегия DELETE, у `api`/`worker` её не будет). `TRUNCATE` триггеры строк не вызывает — права на него не выдаются ни одной прикладной роли. Тесты используют `TRUNCATE` для очистки данных.

### 3.1 Migration Runner `[РЕАЛИЗОВАНО]`

- Файлы `migrations/NNNN_name.sql` (`^\d{4}_[a-z0-9_]+\.sql$`), версии непрерывны с 0001; пропуск, дубликат, неверное имя → `MigrationSetError` до обращения к БД.
- `schema_migrations(version, name, checksum, applied_at)` создаёт **runner** (`CREATE TABLE IF NOT EXISTS`) внутри транзакции, после взятия блокировки.
- Checksum — SHA-256 файла, CRLF→LF нормализован. Изменённый применённый файл или имя → `MigrationChecksumMismatch`; версия в БД, неизвестная сборке, или дыра в истории → `MigrationSetError`. **Автоисправления нет**, схема не меняется.
- Сериализация: `pg_advisory_xact_lock(0x53504F504D494752)` — конкурентные runner становятся в очередь, второй видит уже применённое (no-op). `lock_timeout` на время прогона 120 с, `statement_timeout` отключён только в этой транзакции (`set_config(..., true)`).
- **Весь прогон — одна транзакция:** применяются все ожидающие миграции или ни одна (тест отката). Следствие: нет `CREATE INDEX CONCURRENTLY` и других команд, запрещённых в транзакции; такая миграция потребует отдельного ADR/режима runner.
- Миграция исполняется простым протоколом без параметров (multi-statement); в SQL миграций нет пользовательских данных.
- `current_version(pool)` читает максимальную версию (0 при отсутствии таблицы) — для будущего `/readyz`.
- Роль, под которой запускается runner, — владелец DDL (будущая `supportops_migrator`); приложение (`api`/`worker`) миграции не запускает — `[ПРОЕКТ]`, решение о запуске в deploy-пайплайне (вне M02).

Замечания:

- Идентификаторы (формат фиксирует CHECK `^tkt_[0-9a-f]{32}$`): `'tkt_' + uuid4().hex` и `'run_' + uuid4().hex` (122 бита случайности — достаточно; контракт говорит «128 бит», формулировку уточнить — вопрос 6). Генерирует сервер (`secrets`-совместимо), не клиент.
- `failure_code` в БД проверяется по формату, а не по закрытому списку — список живёт в коде (`SAFE_ERROR_CODES` ∪ `deadline_exceeded`, `worker_lost`, `unsupported_tenant`) и проверяется репозиторием; так добавление кода не требует миграции.
- `answer` хранится только при `completed`. Хранение `message` и `answer` — вопрос retention (D-8), вне M02.
- **Таблица `tenants`: в M02 не добавляется.** Основание: один Demo Tenant; `tenant_id` везде ограничен slug-регэкспом, составные FK связывают `runs` с `tickets`, поэтому позднее добавление `tenants` — аддитивная миграция (backfill → FK `NOT VALID` → `VALIDATE`). Что теряем: FK-целостность и флаг отключения tenant (Alpha §3.4 шаг 5, §6.7 — до появления таблицы отключение = отзыв credential). Альтернатива — добавить сейчас (≈15 строк DDL + seed); это решение владельца (вопрос 1). Рекомендация: отложить, триггер — второй tenant.
- Роли БД `[ПРОЕКТ, НЕ РЕАЛИЗОВАНО]` — решение владельца: отдельный ops-скрипт (не миграция), реализация — отдельным пакетом (M02-WP4). Планируемый набор: `supportops_migrator` (DDL), `supportops_api` (`SELECT` credentials; `INSERT, SELECT` tickets; `INSERT, SELECT` runs; `UPDATE (last_used_at)` credentials), `supportops_worker` (`SELECT` tickets; `SELECT, UPDATE` runs; **нет доступа к `api_credentials`** — AC-11 Alpha). У `api`/`worker` нет `UPDATE/DELETE` на `tickets`.

### 3.2 Конфликты триггеров с recovery и retention `[АНАЛИЗ, схема не менялась]`

| # | Сценарий | Конфликт? | Вывод |
|---|---|---|---|
| 1 | Recovery: reap `running → failed(worker_lost)` | нет | UPDATE не трогает identity-поля, `attempt` не убывает, исходный статус не терминален |
| 2 | Recovery после WP-6: `running → accepted` (повтор) | нет, но **не защищено схемой** | Триггер разрешает; CHECK не требует `lease_* IS NULL` для `accepted` — reap обязан обнулять lease сам (тест WP3) |
| 3 | Recovery: ручной возврат `failed → accepted` | **да (намеренно)** | Терминальный run неизменяем; повтор — только новым тикетом/ключом. Совпадает с политикой §8 |
| 4 | Retention: удаление `tickets`+`runs` по сроку | нет, при порядке `DELETE runs` → `DELETE tickets` | Нужна отдельная роль с DELETE; `api`/`worker` её не получают |
| 5 | Retention: **обезличивание** (`message`, `customer_id`, `answer` → NULL/«redacted») с сохранением записи | **да** | `tickets_no_update` блокирует UPDATE; для `runs` — terminal-защита; плюс `CHECK completed ⇒ answer NOT NULL` не допускает `answer = NULL` |
| 6 | Retention через DELETE ломает идемпотентность | **семантический** | После удаления тикета повтор старого `Idempotency-Key` создаст новый тикет+run (повторный платный вызов LLM) |
| 7 | Offboarding tenant | нет | DELETE `runs` → `tickets` → `api_credentials` по `tenant_id` |

Схему самостоятельно **не менялась**. ADR-рекомендация (решение владельца, до WP3/WP4, вместе с D-8 retention): (a) принять политику «retention = полное удаление» (DELETE ролью `supportops_retention`, порядок runs→tickets), сознательно приняв пункт 6 с окном идемпотентности ≤ срока хранения; **или** (b) если нужно обезличивание — отдельная миграция: вынести PII в отдельную таблицу `ticket_content` (UPDATE/DELETE разрешён ей, `tickets` остаётся неизменяемой записью идентичности и `request_hash`), `answer` вынести аналогично и снять `CHECK completed ⇒ answer`. Рекомендуется (b), если retention по D-8 потребует хранения ключей идемпотентности дольше PII; иначе (a).

## 4. Transaction Boundaries

| Операция | Транзакция | Изоляция | Соединение |
|---|---|---|---|
| Создание тикета+run (API) | **одна** `with conn.transaction()`: INSERT ticket → INSERT run | READ COMMITTED | pool, на время запроса |
| Чтение тикета+run (API) | один SELECT (JOIN), autocommit | READ COMMITTED | pool |
| Lookup credential (Alpha) | один SELECT, autocommit | READ COMMITTED | pool |
| `touch_last_used` | отдельный autocommit UPDATE, best-effort, ошибки глотаются, не в цепочке решения | — | pool |
| Claim (worker) | один UPDATE…FROM (подзапрос `FOR UPDATE SKIP LOCKED`) | READ COMMITTED | pool, возвращается сразу |
| Heartbeat / complete / fail / reap | один UPDATE (CAS) | READ COMMITTED | pool, на время операции |

Правила:

1. **Агент исполняется вне транзакции и без удерживаемого соединения.** Между claim и complete соединение возвращено в pool; корректность держится на lease+fencing, а не на долгой блокировке.
2. Никаких вложенных/распределённых транзакций; каждая операция — одна короткая транзакция или один оператор.
3. Для каждого соединения (`configure`): `statement_timeout` (например 5 с), `lock_timeout` (2 с), `idle_in_transaction_session_timeout` (10 с), `application_name`. Значения — в `ServiceSettings`, без «магических» значений в репозиториях.
4. Pool: `min_size`/`max_size` из настроек; `timeout` ожидания соединения конечен. `PoolTimeout`, `OperationalError` на этапе соединения → `StorageUnavailable` → HTTP 503 `service_unavailable` (контракт Alpha §7.1/§7.2: недоступность БД — не 401, не успех).
5. Любая другая ошибка БД → `StorageUnavailable`/500 без текста исключения (тест с маркером, как в WP1).
6. READ COMMITTED достаточно: все конфликты разрешаются уникальными ограничениями и однооператорными CAS; SERIALIZABLE не нужен и потребовал бы retry-обвязки.

## 5. Idempotency `[ПРОЕКТ — M02-WP2]`

Контракт (api-003 §11): `Idempotency-Key` обязателен; область — **tenant**; `UNIQUE (tenant_id, idempotency_key)`; `request_hash` = SHA-256 канонического JSON `{customer_id, message, order_id}` (отсортированные ключи, UTF-8, `order_id` = `null` если нет).

Алгоритм `create_ticket(*, tenant_id, idempotency_key, customer_id, message, order_id)`:

```text
BEGIN
  INSERT INTO tickets (...) VALUES (...)
    ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING ticket_id
  если вставлено:
     INSERT INTO runs (run_id, tenant_id, ticket_id) ...
     COMMIT → Created(ticket_id, run_id, status='accepted')
  иначе:
     SELECT t.ticket_id, t.request_hash, r.run_id, r.status
       FROM tickets t JOIN runs r USING (ticket_id)
      WHERE t.tenant_id = :t AND t.idempotency_key = :k
     hash совпал  → Replayed(...)  (HTTP 200, Idempotent-Replayed: true)
     hash не совпал → IdempotencyConflict  (HTTP 409 idempotency_key_reuse)
```

Свойства:

- Параллельные первые запросы с одним ключом: второй `INSERT` ждёт коммита первого (уникальный индекс) и затем получает конфликт → ветка replay. Итог: 1 тикет, 1 run. Если первый откатился — второй вставляет сам.
- В replay-ветке строка гарантированно видна: конфликт возможен только с закоммиченной строкой.
- Сравнение хэшей — `hmac.compare_digest` (не секрет, но единый стиль и без таймингов).
- Replay возвращает *текущий* статус; не создаёт новый run.
- Один и тот же ключ у разных tenant — независимые записи (Alpha T16).
- 409 возвращается только в пределах tenant — репозиторий принимает `tenant_id`, глобального поиска по ключу нет.
- Пользовательский ключ не логируется целиком (формат `[A-Za-z0-9._:-]{8,100}` проверяется на уровне HTTP, не репозитория).
- Идемпотентность **бизнес-операций** (`create_support_case`) — отдельная тема WP-6; M02 её не решает и не затрагивает.

## 6. Run Lifecycle `[ПРОЕКТ — M02-WP3]` (инварианты терминальности и монотонности `attempt` уже в БД-триггере)

```text
accepted ──claim──▶ running ──complete──▶ completed
                       │  └──fail──────▶ failed
                       └──reap (lease истёк)──▶ failed(worker_lost)          [срез 1]
                                           └─▶ accepted (attempt<max)        [после WP-6]
```

- Терминальные состояния `completed`/`failed` необратимы: **триггер `runs_guard` отклоняет любой UPDATE** терминальной строки (реализовано), CAS в репозитории — первый рубеж, CHECK фиксирует поля.
- `max_attempts = 1` в M02; автоповтор запрещён, пока не решена бизнес-идемпотентность (api-003 §13).
- `attempt` увеличивается **только** при claim; reap не уменьшает его.
- Допустимая матрица переходов реализуется как набор отдельных методов репозитория, каждый — один CAS-`UPDATE` (§7). Общего метода `set_status(status=...)` не существует.

## 7. CAS `[ПРОЕКТ — M02-WP3]`

Все изменения `runs` — `UPDATE … WHERE <предусловие> RETURNING …`; `rowcount = 0` означает «предусловие не выполнено» и **не** является ошибкой БД.

```sql
-- claim (worker, кросс-tenant по природе; единственный такой запрос)
UPDATE runs r SET status='running', attempt = r.attempt + 1,
       lease_owner = :worker, lease_until = statement_timestamp() + :ttl,
       started_at = COALESCE(r.started_at, statement_timestamp())
 WHERE r.run_id = (SELECT run_id FROM runs WHERE status='accepted'
                    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING r.run_id, r.tenant_id, r.ticket_id, r.attempt;
-- затем SELECT тикета: WHERE tenant_id = :tenant_из_строки AND ticket_id = :id

-- heartbeat
UPDATE runs SET lease_until = statement_timestamp() + :ttl
 WHERE run_id=:r AND tenant_id=:t AND attempt=:a AND status='running' AND lease_owner=:w;

-- complete
UPDATE runs SET status='completed', answer=:ans, finished_at=statement_timestamp(),
       lease_owner=NULL, lease_until=NULL
 WHERE run_id=:r AND tenant_id=:t AND attempt=:a AND status='running' AND lease_owner=:w;

-- fail: то же с status='failed', failure_code=:code

-- reap (срез 1)
UPDATE runs SET status='failed', failure_code='worker_lost', finished_at=statement_timestamp(),
       lease_owner=NULL, lease_until=NULL
 WHERE status='running' AND lease_until < statement_timestamp()
RETURNING run_id, tenant_id;
```

Источник времени для lease — **часы БД** (`statement_timestamp()`), а не worker: нет перекоса часов между процессами. Для тестов — прямое изменение `lease_until` тестовым помощником (не инъекция Clock в SQL).

## 8. Lease & Fencing `[ПРОЕКТ — M02-WP3]` (монотонность `attempt` обеспечивает триггер; остальное — репозиторий)

- **Fencing token = `runs.attempt`** (монотонен, растёт только в claim). Worker получает `ClaimedRun(attempt=N)` и передаёт `N` во все последующие операции.
- Запись результата с устаревшим `attempt` или после reap даёт `rowcount=0` → `FencingRejected`; worker обязан отбросить результат (не повторять, не записывать иначе). Тест: два «воркера», второй перехватил после истечения lease — запись первого отклонена.
- Просроченный, но ещё не reaped run: complete первого worker **принимается** (никто его не перехватил, `attempt` и `status` совпадают). Это сознательно: lease — подсказка для reaper, а не условие записи; fence — это `attempt` + `status`.
- `lease_owner` — вторичная проверка (идентификатор процесса worker), не заменяет `attempt`.
- TTL lease и интервал heartbeat — настройки worker (M02 их только хранит как параметры методов; значений по умолчанию в репозитории нет).
- Reaper — метод `RunQueue.reap_expired()`; кто и как часто его вызывает — задача WP-4 (worker). В M02 только метод и тесты.
- Claim не пропускает `accepted` со старым `created_at`: порядок FIFO по `created_at`, `SKIP LOCKED` исключает блокировку между worker.
- Автоповтор (после WP-6): reap возвращает run в `accepted` при `attempt < max_attempts`; fencing уже защищает от зомби-записи.

**Семантика отказов:**

| Сбой | Результат |
|---|---|
| Worker упал в середине | lease истёк → reap → `failed(worker_lost)`; клиент видит `failed`, повтор — новым `Idempotency-Key` |
| Ошибка агента | `fail(code ∈ закрытый набор)`; текст исключения в БД не пишется |
| БД недоступна при complete | worker повторяет запись в пределах lease; не успел → reap; результат потерян, `failed(worker_lost)` (приемлемо для среза 1) |
| Двойной claim | невозможен: `FOR UPDATE SKIP LOCKED` + CAS по `status='accepted'` |
| Зомби-запись | `FencingRejected` |
| Отказ БД в API | 503 `service_unavailable`, ничего не создано (транзакция откатилась) |

**Recovery policy:** (1) старт worker не восстанавливает «свои» run — только reaper по `lease_until`; (2) `accepted` run без worker остаются в очереди неограниченно (метрика возраста — WP-8); (3) ручное вмешательство через SQL не требуется и не поддерживается API.

## 9. Tenant Isolation `[ПРОЕКТ в части репозиториев; в схеме реализовано: slug CHECK, составные FK]`

1. **API-репозиторий** (`TicketRepository`): все методы — `*, tenant_id: str`, **без значения по умолчанию**; `tenant_id` — единственный источник `ctx.tenant_id`. Метода «по id без tenant» нет. Принимает `str`, не `TenantContext` (контракт Alpha §4.3; закрывает открытый вопрос 7 Alpha — предлагается подтвердить).
2. Любой SQL по tenant-объектам содержит `WHERE tenant_id = %(tenant_id)s AND …`; `INSERT` берёт `tenant_id` из аргумента. Запрос чужого, несуществующего и некорректного id → один результат `None` → единый 404.
3. **Worker-интерфейс** (`RunQueue`) — отдельный Protocol в отдельном модуле, **не импортируется** API-слоем (проверка тестом по AST, AC-11). `claim_next()` — единственная операция без tenant на входе; сразу возвращает `tenant_id` из строки; все последующие вызовы принимают `ClaimedRun` и используют его `tenant_id`. HTTP-credential worker не использует; БД-роль `supportops_worker` не видит `api_credentials`.
4. Составные FK `(tenant_id, ticket_id)` — `runs` не может принадлежать другому tenant, чем его тикет.
5. `tenant_id` в БД ограничен slug-регэкспом (CHECK) — совпадает с §4.1 контракта Alpha.
6. RLS — второй эшелон, **не** в M02 (api-003: до подключения второго tenant). Подготовка: все запросы уже содержат `tenant_id`, так что включение RLS не меняет код репозиториев.
7. Тест изоляции — на уровне репозитория с двумя tenant (свой/чужой/несуществующий/некорректный) и на HTTP-уровне после подключения роутеров.

## 10. Integration Contract with Alpha

### 10.1 Сверка WP1 с Security Contract (Phase 2)

Файл `src/supportops_service/interfaces.py` в WP1 помечен как **PRELIMINARY / не согласован**; публичные сигнатуры в WP1 **не менялись** (контракт Alpha — черновик, §8 и вопросы 6–7, 12 не согласованы владельцем). Расхождения и предложения:

| # | Элемент | WP1 (`b33191b`) | Контракт Alpha | Предложение (не применено) |
|---|---|---|---|---|
| 1 | `TenantContext` | frozen dataclass, `scopes: frozenset[str]` | `frozenset[Scope]` | Определение — **только** в `interfaces.py` (Beta); `scopes: frozenset[Scope]`. Второго типа нет; `security/tenant_context.py` Alpha — только фабрика/валидация slug |
| 2 | `Scope` | отсутствует | `StrEnum` из 3 значений | Добавить в `interfaces.py` |
| 3 | `CredentialRecord` | `key_hash: str`, нет `pepper_id`, `expires_at`; `revoked_at: str` | `key_hash: bytes`, `pepper_id`, `expires_at: datetime\|None`, `revoked_at: datetime\|None`, `scopes` сырые строки | Принять версию Alpha |
| 4 | `CredentialRepository` | только `get_by_prefix` | + `touch_last_used(credential_id)` | Принять; best-effort |
| 5 | `Clock` | отсутствует | `now() -> datetime` | Добавить Protocol + `SystemClock`; tz-aware UTC |
| 6 | `authenticate`, `require_scope` | Protocol-эскизы | реализует Alpha в `security/dependencies.py` | Beta владеет только сигнатурой-эскизом; реализации нет |
| 7 | Коды ошибок | 401 `unauthorized`, 403 `forbidden` | 401 `unauthenticated`, 403 `insufficient_scope` | Изменить в `errors.py`: коды по контракту. **Изменение публичного поведения, сделать после согласования** |
| 8 | Новые коды | нет | 409 `idempotency_key_reuse`, 503 `service_unavailable` | Добавить в `errors.py` вместе с M02-WP2 |
| 9 | `WWW-Authenticate: Bearer` на 401 | не поддерживается | обязателен, байтовая идентичность | Точка расширения: исключение `ApiError(status, code, headers=…)`, обработчик пробрасывает `headers`; Beta реализует, Alpha использует |
| 10 | Точки подключения | `create_app()` без параметров зависимостей | инжектируемые `CredentialRepository` и `Clock`; роутеры без правки `app.py` Alpha-ом | `create_app(settings=None, *, credentials=None, clock=None, extra_routers=())`; хранить в `app.state`; Alpha пишет `Depends` поверх `app.state` |
| 11 | Pepper | отсутствует | `pepper`, `pepper_id` из окружения, без значений по умолчанию; старт без них отказ, если подключена аутентификация | Добавить `SecretStr` поля в `ServiceSettings`; проверка в `create_app` при подключённой аутентификации |
| 12 | Обработчик 422 | не отражает input (есть) | то же | Совместимо |
| 13 | Порядок проверок | не реализован (нет роутеров) | auth 401 → scope 403 → заголовки → размер → валидация | Обеспечивается зависимостями роутера; Beta кладёт `authenticate` первой зависимостью |
| 14 | `X-Tenant-Id` | не читается | не читается | Совместимо |
| 15 | Worker trust boundary | не реализован | worker без HTTP/credential | M02: отдельный `RunQueue`, роль БД без доступа к credentials |
| 16 | Request ID | `[A-Za-z0-9-]{1,64}` | то же (T17) | Совместимо |

**Граница ответственности (уточнение):** Beta — `interfaces.py`, `errors.py`, `app.py`, миграции, `repositories/{tickets,runs_queue}.py`; Alpha — `security/**`, `repositories/credentials.py`, `tests/service/security/**`. Изменение `interfaces.py` — только с подтверждением Alpha (контракт §8).

### 10.2 Что M02 даёт Alpha

- Таблица `api_credentials` с полями из §3.3 контракта и ограничениями формата (CHECK).
- Pool и транзакционные утилиты; единый `StorageUnavailable` → 503.
- Правило: `CredentialRepository` PG-реализация использует тот же pool; недоступность БД → исключение, которое dependency Alpha превращает в 503.

### 10.3 Что M02 ждёт от Alpha

- Подтверждение таблицы §10.1 (особенно 1, 3, 7, 9, 10), решения вопросов 6, 7, 12 контракта.
- `Scope`-фильтрацию и ключевую математику (HMAC) — только у Alpha; Beta хранит `key_hash` как `bytea(32)` и не вычисляет.
- Seed-скрипт Demo Tenant: генерация ключа — у Alpha (формат `sok1.…`), запись — через репозиторий/миграционный seed Beta. **Предложение:** скрипт принадлежит Alpha, вставка — через метод `CredentialRepository.insert` (не входит в read-only Protocol Alpha) — уточнить (вопрос 5).

## 11. Test Plan

**Реализовано (WP1):** маркер `postgres`; PG из `SUPPORTOPS_TEST_DATABASE_URL` (пользователь с правом `CREATE DATABASE`). Фикстуры создают **отдельную временную БД** (`CREATE DATABASE … / DROP … WITH (FORCE)`) на тест; для schema-тестов — одна БД на модуль и `TRUNCATE` перед тестом. Без переменной тесты skip, но при `SUPPORTOPS_REQUIRE_PG=1` отсутствие или недоступность PG — **ошибка** (проверено: 8 ERROR вместо skip). Работает на PostgreSQL 16.14 в локальном Docker-контейнере. Реализованные группы: Migrations, Schema (CHECK, FK, триггеры, immutability), Failure (pool: недоступный сервер, неверные учётные данные, отсутствие секретов в исключениях и логах, исчерпание pool, statement_timeout), Regression-часть (импорт без соединений/потоков/сокетов). Полный регресс на момент коммита: 698 passed.

Группы Atomic create / Idempotency / Tenant isolation (репозиторий) / CAS / Lease / Fakes и тесты БД-ролей — **[ПРОЕКТ]**, пакеты WP2–WP4.

| Группа | Проверки |
|---|---|
| Migrations | применение с нуля; повторное применение — no-op; изменённый checksum — отказ; конкурентный запуск двух runner (advisory lock); версия схемы читается `/readyz` |
| Schema | CHECK на формат id/tenant/hash; `completed` без answer — отказ; `running` без lease — отказ; роль `api` не может `UPDATE/DELETE tickets`; роль `worker` не читает `api_credentials` |
| Atomic create | инъекция сбоя между INSERT ticket и INSERT run → 0 строк в обеих таблицах; успех → 1+1 |
| Idempotency | повтор → те же id, `Replayed`; другое тело → `IdempotencyConflict`; тот же ключ у другого tenant — независим; N=20 параллельных → 1 тикет, 1 run |
| Tenant isolation | свой/чужой/несуществующий/некорректный id → идентичный `None`; обязательность keyword `tenant_id` (вызов без него → `TypeError`); статическая проверка: API-модули не импортируют `runs_queue` |
| CAS | claim FIFO; два worker на N заданиях — каждое взято ровно раз; complete с чужим `attempt`/`owner` → `FencingRejected`; complete из `completed` → отклонён; терминальные состояния неизменны |
| Lease | heartbeat продлевает; просроченный и reaped → complete отклонён; просроченный без reap → complete принят; reap `running` → `failed(worker_lost)`, `accepted` не затрагивает |
| Failure | недоступность БД (остановленный pool/неверный DSN) → `StorageUnavailable`; нет текста исключения в ответе/логе (маркер); `/readyz` → 503 без причин |
| Fakes | in-memory реализации проходят **тот же** набор контрактных тестов (parametrized), чтобы HTTP-тесты без PG не расходились с PG |
| Regression | `src/supportops_agent/**` без изменений; импорт `supportops_service` не создаёт соединений/сети |

## 12. Acceptance Criteria

1. `[ВЫПОЛНЕНО]` Миграция `0001` применяется на чистой PG, идемпотентна, checksum-защита работает.
2. Создание тикета+run атомарно; при сбое не остаётся ни одной строки.
3. Идемпотентность: повтор → те же id; конфликт тела → `IdempotencyConflict`; параллельные запросы → 1 тикет, 1 run.
4. Все методы API-репозиториев требуют `tenant_id` (keyword-only, без default); чужой/несуществующий/некорректный id неразличимы.
5. `RunQueue` недоступен из API-слоя (тест импортов); worker-роль БД не читает `api_credentials`.
6. CAS-переходы: каждый перепрыгивающий/повторный/устаревший переход отклоняется; два worker не берут один run.
7. Fencing: запись с неверным `attempt` отклоняется; reap → `failed(worker_lost)`; автоповтора нет.
8. Недоступность БД → `StorageUnavailable` → 503; текст исключения нигде не появляется.
9. `/readyz` проверяет БД и версию схемы; без причин в ответе.
10. `[ВЫПОЛНЕНО для WP1]` Не изменены `src/supportops_agent/**`; SupportAgent не импортируется service-пакетом; регресс зелёный (698 на `e6bf9b1`).
11. Все решения §10.1 по публичным сигнатурам согласованы с Alpha и отражены в `interfaces.py` до начала реализации репозиториев.

## 13. Risks

| # | Риск | Влияние | Митигация |
|---|---|---|---|
| R1 | PG-тесты молча пропускаются в CI | ложная уверенность | `SUPPORTOPS_REQUIRE_PG=1` в CI → ошибка при отсутствии PG |
| R2 | Дрейф схемы без ORM | расхождение кода и БД | checksum миграций; `/readyz` проверяет версию; контрактные тесты |
| R3 | Роли БД не создаются миграцией | в проде приложение работает под владельцем | ops-скрипт/compose; тест проверяет права под реальными ролями; зафиксировать в runbook (вне M02) |
| R4 | Расхождение публичных сигнатур с Alpha | переделка M02 | §10.1 согласовать до реализации; стоп-условие |
| R5 | Потеря результата при падении между агентом и complete | лишний платный вызов LLM, `failed(worker_lost)` | принято для среза 1; автоповтор только после WP-6 |
| R6 | `tenants` отсутствует | нет FK/флага отключения | slug-CHECK + составные FK; триггер — второй tenant |
| R7 | Sync-pool исчерпывается (threadpool ≥ pool) | 503 под нагрузкой | `pool.max_size` ≥ потоков, конечный `timeout`, нагрузочный тест — вне M02 |
| R8 | Время lease зависит от часов БД | неожиданный reap при смене часов БД | единый источник времени (БД); мониторинг — вне M02 |
| R9 | UUID4 даёт 122 бита, не 128 | расхождение с формулировкой контракта | см. вопрос 6; при необходимости `secrets.token_hex(16)` |
| R10 | `message`/`answer` хранят персональные данные | юридические вопросы | retention — D-8, вне M02; не логировать |

## 14. Dependencies

- Решения владельца: §15 (закрытые — отмечены).
- Alpha: подтверждение §10.1; решение вопросов 6, 7, 12 контракта; формат `api_credentials`.
- Пакеты: `psycopg[binary]>=3.2,<4`, `psycopg-pool>=3.2,<4` — **добавлены** (`e6bf9b1`).
- PostgreSQL **16** (dev/CI; решение владельца). Docker-compose для разработки — не создан.
- MONOLITH v4: миграция должна завершиться до любых изменений governance state (регистрация M02 как Mission — после неё).
- Не зависит от: SupportAgent, Authorization Gate, `RefundPolicyEngine`.

**Пакеты реализации (предложение):**

| Пакет | Содержание | Зависит от |
|---|---|---|
| M02-WP1 **(выполнен, `e6bf9b1`)** | pool, migrate runner, `0001`, PG test harness, schema-тесты | — |
| M02-WP2 | `TicketRepository` (PG + fake), atomic create, idempotency, HTTP-коды 409/503 в `errors.py` — ТЗ: `api-004-m02-wp2-ticket-repository.md` | WP1, §10.1 п.7–9 (согласование с Alpha: `api-004-interface-alignment-and-integration-gate.md`) |
| M02-WP3 | `RunQueue` (claim/heartbeat/complete/fail/reap), fencing-тесты | WP1 |
| M02-WP4 | роли/гранты, `/readyz` с БД, изоляционные тесты, контрактные тесты fake↔PG | WP2, WP3 |
| (Alpha) | `CredentialRepository` PG, `authenticate`, `require_scope` | WP1, §10.1 |

## 15. Open Questions

Для владельца:

1. ~~`tenants`~~ — **решено: отложить до второго tenant.**
2. ~~Sync vs async~~ — **решено: sync.**
3. ~~Пакеты~~ — **решено и добавлено.**
4. **Публичные изменения `interfaces.py`/`errors.py` (§10.1 п.1–5, 7–11)** — принять предложения; без этого M02-WP2 не стартует.
5. **Seed Demo Tenant** — кто владеет скриптом (Alpha генерирует ключ, Beta вставляет?).
6. **Формат id:** 122 бита (`uuid4().hex`) или ровно 128 (`secrets.token_hex(16)`) — рекомендуется `secrets.token_hex(16)`, чтобы совпасть с контрактом.
7. ~~Роли БД~~ — **решено: ops-скрипт** (реализация — отдельный пакет WP4). Остаётся: роль `retention` (§3.2).
8. **Поведение принятых run при отзыве ключа** (Alpha, вопрос 3) — влияет на то, нужна ли `credential_id` в `runs` (в схеме её нет; рекомендация — не добавлять).
9. **Retention** `message`/`answer` (D-8) — до M02 или после; влияет на выбор варианта (a)/(b) в §3.2.
10. ~~Версия PostgreSQL~~ — **решено: 16.**
11. **Таймаут блокировки/statement** (SQLSTATE 55P03/57014) в API: 503 (текущее поведение пула) или иное (§2, ограничение pool).
