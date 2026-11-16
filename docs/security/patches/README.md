# SUPPORTOPS-API-004 — патчи интеграции Alpha ↔ Beta

> **Статус:** подготовлены и проверены в **симуляции** (копия `main@7ac7cb6` + файлы Alpha в scratch-каталоге вне репозиториев). **Ни один патч не применён ни к одному репозиторию.** Это не Integration Gate (G1–G12) и не общий merge.

## Порядок (строго)

| # | Файл | Кому применять | Что меняет |
|---|---|---|---|
| 1 | `0001-beta-interfaces.patch` | **Beta** (владелец `interfaces.py`) | `Scope`, `TenantContext.scopes: frozenset[Scope]`, `CredentialRecord` (`key_hash: bytes`, `pepper_id`, `expires_at`, `revoked_at`), `CredentialRepository` (+`touch_last_used`), `Clock`; снят статус PRELIMINARY |
| 2 | `0002-beta-errors-config-app-wiring.patch` | **Beta** (`errors.py`, `config.py`, `app.py`) | коды 401/403 → `unauthenticated`/`insufficient_scope`, 503 `service_unavailable`; `pepper`/`pepper_id` в `ServiceSettings` (без default, `SecretStr`); `create_app(..., credentials=, clock=, v1_routers=)`; `redirect_slashes=False`; стартовая проверка `find_unsecured_routes` (fail-closed); тест `tests/service/test_security_wiring.py` |
| 3 | `0003-alpha-adopt-interfaces.patch` | **Alpha**, только после 1 | импорты → `interfaces`, удаление `security/contracts.py`, тест «одно определение каждого общего типа» (перегенерирован в INTEGRATION-02: учитывает `test_security_hardening.py`) |
| 4 | `0004-beta-pepper-set-and-scope-audit-wiring.patch` | **Beta** (`config.py`, `app.py`), после 1–2 и после 3 в Alpha | `ServiceSettings.previous_peppers` (`SUPPORTOPS_PREVIOUS_PEPPERS`, `id=value,id=value`, verify-only); `create_app` строит `PepperSet.from_config(active_id=pepper_id, ...)`; стартовая проверка `find_scopes_outside_route_signature` (F4); тест `tests/service/test_pepper_rotation_wiring.py` |

Патчи 1–2 и 4 проверены `git apply --check` на чистом `main@7ac7cb6` (последовательно); патч 3 — на текущем Alpha-worktree.

Промежуточное состояние после шага 1 и до шага 3 корректно: `contracts.py` остаётся рабочим (дословная копия), два `CredentialRecord` сосуществуют, но Alpha не использует `interfaces.CredentialRecord` до шага 3.

## Чего патчи НЕ делают

- Не добавляют `repositories/tickets.py`, `runs_queue.py`, коды `409/400/415`, `StorageUnavailable`-обработчик вне auth (WP2, Beta).
- Формат нескольких pepper (патч 0004) — предложение: активный `PEPPER`/`PEPPER_ID` + verify-only `PREVIOUS_PEPPERS`. Утверждает владелец.
- Не меняют `uv.lock`/`pyproject.toml`: код Alpha не добавляет зависимостей (PG-репозиторий не импортирует драйвер).
- Не создают DB-роли и не выдают права (WP4); тест `test_least_privilege_role_is_sufficient` лишь доказывает, что `SELECT` + `UPDATE(last_used_at)` на `api_credentials` достаточны.

## Как проверить после применения

```bash
# PG-тесты: skip = провал
SUPPORTOPS_REQUIRE_PG=1 SUPPORTOPS_TEST_DATABASE_URL=postgresql://… pytest tests/security_pg tests/db
pytest tests            # полный регресс
git diff main..HEAD -- src/supportops_agent   # должен быть пуст (G6)
```
