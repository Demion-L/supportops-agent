# HERMES · Сверка governance (2026-11-27)

Перечень governance-операций MONOLITH, которые не выполнены или невыполнимы, для последующей сверки владельцем.
`state.json` не редактировался, не копировался между worktree; MONOLITH не изменялся.

## Выполнено (документы, без state.json)

- Зарегистрирован Epoch II Roadmap: `.monolith/context/maps/roadmap-epoch-2-hermes.md` (+ ссылка из `roadmap-index.md`).
- Эпоха и идентичность зафиксированы в `README.md`, `docs/hermes/00…08`, `FINAL-REPORT.md` (не закоммичены).
- Миссии и Stories для будущих работ не регистрировались; фиктивных завершённых Missions нет.

## Невыполнимо / отложено

| # | Операция | Причина | Что нужно |
| - | -------- | ------- | --------- |
| G1 | Расхождение Tactical Coordinatorium между worktree | `state.json` в gitignore и живёт отдельно в каждом worktree. `main`: revision 31, 4 миссии (MIS-0001…0004, все completed), 23 checkpoint. `supportops-agent-api004-m01`: revision 33, **5 миссий (MIS-0005 `in_progress`)**, 24 checkpoint | Сверка владельцем штатными средствами. Ручное копирование запрещено директивой |
| G2 | Удаление worktree `supportops-agent-api004-m01` | Ветка полностью влита (HEAD — предок `main`), чистый tree, **но** в его ignored `state.json` лежит единственная копия MIS-0005 и 1 checkpoint. Удаление уничтожило бы запись | Сначала разрешить G1 |
| G3 | Финализация SEC-003 | `active.md` противоречив: «No active story recorded» и одновременно «Active story: SUPPORTOPS-SEC-003… in progress»; roadmap-index показывает SEC-003 ACTIVE; в `state.json` MIS-0004 completed | Штатный skill `finalize-story` по решению владельца |
| G4 (устранено 2026-11-27) | Статус API-004 в MONOLITH | `roadmap-index.md` обновлён: IN PROGRESS; M01/M02 и HTTP slice интегрированы в `main` | Обновление владельцем/через finalize |
| G5 | Missions для Phase 01 | Директива: не регистрировать Missions для работ, которые не начались; работы Phase 01 без commit-разрешения ограничены проверками | Регистрировать при старте реализации в отдельных worktree |

## Прочие находки аудита

- Worktree `api004-alpha`: untracked `docs/security/api-004-security-contract.md` — **устаревшая редакция 2**; редакция 3 в `main` — надмножество (проверено diff). Удалять после разрешения владельца, не раньше.
- Тест `tests/db/test_pool_pg.py::test_bad_credentials_fail_closed_and_leak_nothing` падает на локальной PG через unix-сокет с trust/peer-аутентификацией (пароль не проверяется) — **артефакт окружения, не дефект кода**; на PG 16 с паролем по TCP проходит. Для воспроизводимого регресса использовать изолированный контейнер (см. PHASE-01-PLAN).
