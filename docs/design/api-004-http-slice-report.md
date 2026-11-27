# API-004 HTTP Slice — отчёт (Alpha, HERMES E2-Phase 01)

> **Ветка:** `feat/supportops-api004-http-slice`. **Статус:** реализовано, не закоммичено (commit/merge/push — отдельное разрешение владельца).
> **Контракт:** [`docs/security/api-004-security-contract.md`](../security/api-004-security-contract.md) ред. 4 (§2a, §7.1, AC-18/19, §10a).

## Что реализовано

| Эндпоинт | Scope | Результат |
|---|---|---|
| `POST /v1/tickets` | `tickets:write` | 202 `{ticket_id, run_id, status:"accepted", created_at}` + `Location`; повтор → 200 + `Idempotent-Replayed: true`; другое тело → 409 |
| `GET /v1/tickets/{ticket_id}` | `tickets:read` | 200 закрытая проекция; чужой/несуществующий/некорректный id → одинаковый 404 |

Порядок проверок POST: 401 → 403 → 400 `idempotency_key_required` → 413 → 415 → 422 → операция.
Тело читается вручную (лимит 16 KiB, в т. ч. chunked), `SecuredRoute` аутентифицирует раньше.

## Файлы

- `src/supportops_service/routers/tickets.py` — GET + POST.
- `src/supportops_service/schemas.py` — `CreateTicketRequest` (strict, `extra=forbid`, strip, без NUL/суррогатов), `CreateTicketResponse`, `TicketResponse`.
- `src/supportops_service/errors.py` — `RequestRejected` (400/413/415, фиксированные коды/тексты). Остальное без изменений.
- Тесты: `tests/service/test_tickets_http.py`, `tests/service/test_tickets_post_http.py`, `tests/integration/test_tickets_http_pg.py`, `tests/integration/test_tickets_post_pg.py`.

## Не менялось

`src/supportops_agent/**`, миграции, `repositories/**`, `security/**`, `.monolith/**`, другие worktree. Агент, LLM и сеть из обработчиков недостижимы (статические и сетевые тесты).

## Модель customer identity

См. контракт §2a: tenant — только из credential; `customer_id` — tenant-asserted, не verified, не основание авторизации; одинаковые `customer_id` у разных tenant не пересекаются. Остаточные риски R-ID-1…3 и R-HTTP-1…4 — в контракте.
