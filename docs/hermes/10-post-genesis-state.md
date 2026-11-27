# Состояние после Genesis: консолидация (2026-11-27)

Актуализация исторических документов 00–08: они зафиксированы на момент исследования (например, «10 worktree») и **не переписывались**. Актуальное состояние — здесь.

## 1. Worktree (после Repository Hygiene)

Удалены 4 завершённых (`api-003`, `api004-beta-wp2`, `api004-integration`, `sec003-wp4`): чистые, без уникальных коммитов, HEAD входит в `main`. Ветки сохранены. Осталось 5 дополнительных:

| Worktree | Причина сохранения | Дальнейшее |
|---|---|---|
| `api004-http` | Незавершённый HTTP Slice (4 untracked файла) | SESSION ALPHA |
| `eval-001` | 2 уникальных коммита (`7375918`, `3bcdfb9`) + untracked дизайн Live-eval | SESSION BETA |
| `api004-m01` | Игнорируемый `.monolith/state.json` с `MIS-0005`, отсутствующей в основном state (revision 33 против 31) | Расхождение зафиксировано, не блокер Phase 01 (§5) |
| `api004-alpha` | (удалён после коммита консолидации) устаревшая ред. 2 contract — §2 | — |
| `commons-exp-001` | Результаты эксперимента подготовлены в `experiments/agent_commons/` (§3), но ещё не закоммичены | Удаление после коммита архива |

## 2. Security contract API-004

`main` содержит редакцию 3 (`2511051`, `016e5a0`, `b076ae8`), в которой Alpha-редакция 2 принята как основа. Построчное сравнение с untracked-файлом worktree: отличаются только (а) статус-строка (ред. 2 «ждут подтверждения Beta» против ред. 3 «интегрированная»), (б) абзацы реализации ред. 3 (PepperSet, AuthenticationGate, FastAPI-интеграция, PostgreSQL-репозиторий), (в) строки таблицы ошибок (503 `Retry-After`, 500 `internal_error`), (г) устаревшие ограничения/«не проверено». **Уникальных требований в untracked-версии нет**; она не противоречит ред. 3 лишь в той части, где устарела. Вывод: сохранять нечего. Резервная копия: `scratchpad/api-004-security-contract.rev2-alpha-untracked.md` (sha256 `9a354612…89762`, временное хранилище сессии).

## 3. Архив эксперимента AC-EXP-001 (Agent Commons)

Подготовлен к отдельному коммиту: `experiments/agent_commons/` (31 файл, ≈228 КБ) + `experiments/conftest.py`.

- **Методология и выводы:** `README.md`, `architecture.md`, `threat-model.md`, `results.md` (30 seeds × 100 задач; commons +0.346 к baseline, но −0.043 к локальному экспертному playbook — преимущество обмена **не доказано**; ограничения и рекомендации следующего эксперимента — в `results.md`).
- **Исключено:** `results_data.json` (712 КБ, воспроизводится `python3 run_experiment.py` детерминированно), `__pycache__`, `.pytest_cache`. Секретов нет (совпадения grep — синтетические строки-ловушки в тестах).
- **Изоляция:** тест `test_no_production_supportops_import` требует отсутствия `supportops_agent` в процессе, поэтому в общем прогоне падал (1 failed). `experiments/conftest.py` исключает каталог из корневого `pytest`; из `experiments/agent_commons` запускается отдельно (72 passed). Корневой прогон: 934 passed, 138 skipped, как до копирования. `pyproject.toml` не менялся.
- Состав `experiments/` — единственное, что не относится к docs-only: коммитить **отдельно** от документации.

## 4. Манифест коммитов (предложение, коммиты не выполнялись)

1. **docs-only:** `README.md`, `docs/hermes/` (00–10, `FINAL-REPORT.md`).
2. **archive:** `experiments/`.
3. Не включать ничего из других worktree.

## 5. Governance (MONOLITH)

- Roadmap Epoch II зарегистрирован: `.monolith/context/maps/roadmap-epoch-2-hermes.md`. Устаревшие записи о SEC-003 в `active.md` и `roadmap-index.md` исправлены 2026-11-27 по подтверждённым данным (капсула финализации, `MIS-0004 completed`, `78af152` в `main`); история Missions не менялась.
- Расхождение `MIS-0005` (worktree `api004-m01`) не разрешалось; `state.json` не копировался и не редактировался. Остальные операции — `governance-reconciliation-2026-11-27.md`.
- Идентичность клиента (решение владельца): tenant-asserted customer identity — `customer_id` от аутентифицированного tenant backend является заявленным предметным идентификатором, а не подтверждением личности; не основание для авторизации чувствительных операций.
