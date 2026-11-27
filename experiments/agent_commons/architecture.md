# Архитектура AC-EXP-001

## Участники

```
 Agent B (Knowledge)          Agent A (Support)               Agent C (Evaluator)
   share() ─────────┐       ask / validate / feedback          pending() / record_*()
                    ▼              ▼                                ▲
              ┌───────────────────────────────┐                     │
              │ CommonsService  (AgentPort)   │──── Sink ───────────┘
              │ Ledger = fold(EventLog)       │   (отдельная способность)
              └───────────────────────────────┘
 A ── execute() ──► World (симулятор) ◄── OracleView (read-only) ── C
 A ── authorize() ─► LocalPolicy (локально, без Commons)
```

* **A** видит `Task` и `AgentPort` (4 операции). Не импортирует симулятор/oracle/Evaluator (проверяется AST-тестом).
* **B** публикует через `share`. Ссылок на A не имеет, исполнять ничего не может.
* **C** получает *заявленные* факты (`ApplicationEvidence`) и перепроверяет их по состоянию мира. Не импортирует
  agents/service/policy (AST-тест). Единственный канал записи оценок — `Sink`, агентам не выдаётся.
* Протокол (`protocol.py`, `dto.py`) отделён от реализации Evaluator (`evaluator.py`).

## Поток одной задачи (Commons-режим)

1. `Ask(problem_type, context)` → кандидаты с контекстной оценкой.
2. Для каждого кандидата (до 3): порог оценки → **локальная** проверка применимости (не доверяем фильтру сервера) →
   `Validate` (открывает Application, проверяет актуальность версии) → **локальная политика** → исполнение →
   `Feedback(applied)`.
3. Если Commons недоступен или ничего не подошло — локальный playbook (S6).
4. Evaluator: читает фактический исход из мира, пишет OBSERVED → EVALUATED (или проверяет заявленный отказ политики).
5. Оценка рекомендации в контексте обновляется только по этим независимым вердиктам.

## Модель состояния (идеи из MONOLITH Durable State, без зависимости)

Заимствовано концептуально: append-only лог с хэш-цепочкой; состояние = чистая свёртка лога (перестраивается
при рестарте); типизированные коды ошибок; отказ от учёта непроверенных заявлений; replay-проверка инвариантов.
Не заимствовано: лизы/fencing, effect-intent, retention/prune, канонизация сверх нужного. Исходники MONOLITH
читались (типы `dist/durable/*.d.ts`), кода не копировалось, runtime-зависимости нет.

**Recommendation** (версионированное знание): `PROPOSED → VALIDATED → REVOKED`, `PROPOSED → REJECTED`.
Версия `n+1` — новая запись; статистика не наследуется.

**Application** (применение; много на одну Recommendation):
`PROPOSED → VALIDATED → APPLIED → OBSERVED → EVALUATED`.
Альтернативный переход, потребовавшийся на практике: `PROPOSED|VALIDATED → REJECTED`
(`not_applicable`, `stale_version`, `revoked`, `denied_by_local_policy`). Отказ локальной политики дополнительно
получает независимое подтверждение (`app.rejection_assessed`), иначе агент мог бы «саботировать» хорошую рекомендацию
ложным заявлением об отказе.

Application хранит: id рекомендации и версию, контекст, результат Validate, наблюдение, вердикт Evaluator;
самоотчёты агента — отдельно и не участвуют в оценке.

## Оценка (scoring.py)

```
prior_rec = (S_all + 1) / (N_all + 2)
score_ctx = (S_ctx + 2 * prior_rec) / (N_ctx + 2)
```
`SOLVED`: +1/+1; `NOT_SOLVED` и `BLOCKED_BY_POLICY`: +0/+1; `HARMFUL`: +0/+3. `NOT_APPLICABLE`, `UNVERIFIED` не
учитываются. `confidence` автора используется только как tie-break при равной оценке. Порог принятия у A: 0.4.
Режим `global` (только `prior_rec`) — ablation для проверки S9.

## Хранилище

JSONL, `fsync` на каждую запись, без wall-clock. Оборванный хвост обрезается; повреждение в середине или
семантически невозможная история — `LogCorruptError`.
